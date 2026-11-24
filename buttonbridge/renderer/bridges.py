"""Finding ESP32 bridges on the network and talking to them.

Protocol: docs/BRIDGE-PROTOCOL.md. Each bridge advertises _sdbridge._tcp over mDNS. A
Bridge keeps one WebSocket open to it (reconnecting as needed), uploads each key image to the
bridge's cache once, and then shows images by hash.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import struct
from dataclasses import dataclass
from typing import Awaitable, Callable

import websockets

log = logging.getLogger(__name__)

SERVICE = "_sdbridge._tcp.local."
RECONNECT_DELAY = 1.0  # seconds
# The bridges are on the local network, so a missing pong means the link is gone: notice within
# about 8 s rather than 30, so fewer presses land while the renderer still thinks it's connected.
PING_INTERVAL = 3.0  # seconds
PING_TIMEOUT = 5.0


def panel_id(name: str) -> str:
    """The ID a config refers to a bridge by: sd- and the last six hex digits of its MAC.

    Accepts an mDNS instance or host name (sdbridge-19c9a4, sd-19c9a4.local) or a MAC.
    """
    match = re.search(r"([0-9a-f]{6})(?:\.local\.?)?$", name.lower().replace(":", ""))
    if not match:
        raise ValueError(f"not a bridge name: {name}")
    return "sd-" + match.group(1)


@dataclass
class Deck:
    """The Stream Deck attached to a bridge, as its hello describes it."""

    model: str
    rows: int
    cols: int
    key_size: int
    format: str  # "bmp" or "jpeg"
    transform: str  # see the protocol doc
    dials: int = 0  # the Stream Deck +'s knobs
    screen_width: int = 0  # its touch strip (the Neo's info bar too); 0 if none
    screen_height: int = 0
    screen_transform: str = "none"

    @property
    def keys(self) -> int:
        return self.rows * self.cols

    @classmethod
    def from_json(cls, data: dict | None) -> "Deck | None":
        if not data or not data.get("keySize"):
            return None
        screen = data.get("screen") or {}
        return cls(data["model"], data["rows"], data["cols"], data["keySize"], data["format"], data["transform"],
                   data.get("dials", 0), screen.get("width", 0), screen.get("height", 0), screen.get("transform", "none"))


KeyHandler = Callable[[int, bool], Awaitable[None]]  # key (0-based), down
DeckHandler = Callable[["Deck | None"], Awaitable[None]]
DialHandler = Callable[[int, int, "bool | None"], Awaitable[None]]  # dial (0-based), detents turned, pushed in/out (None for a turn)
TouchHandler = Callable[[str, int, int], Awaitable[None]]  # tap, long or swipe; x and y on the strip


class Bridge:
    """One bridge: a WebSocket that reconnects, with the deck's details and an image cache."""

    def __init__(self, id: str, host: str, port: int = 80):
        self.id = id
        self.host = host
        self.port = port
        self.deck: Deck | None = None
        self.on_key: KeyHandler | None = None
        self.on_deck: DeckHandler | None = None
        self.on_dial: DialHandler | None = None
        self.on_touch: TouchHandler | None = None
        self._ws = None
        self._cached: set[str] = set()
        self._showing: dict[int, str] = {}
        self._screen: dict[tuple, bytes] = {}  # what each area of the screen shows
        self._last_images: dict[str, bytes] = {}  # by hash, to upload again if the bridge loses one
        self._task: asyncio.Task | None = None
        self._send_lock = asyncio.Lock()
        self._events: asyncio.Queue[dict] = asyncio.Queue()
        self._worker: asyncio.Task | None = None

    @property
    def connected(self) -> bool:
        return self._ws is not None

    def start(self) -> None:
        if not self._task:
            self._task = asyncio.create_task(self._run(), name=f"bridge {self.id}")
            self._worker = asyncio.create_task(self._work(), name=f"bridge {self.id} events")

    async def stop(self) -> None:
        for task in (self._task, self._worker):
            if task:
                task.cancel()
        self._task = self._worker = None

    def moved(self, host: str, port: int) -> None:
        """mDNS saw the bridge at a new address: use it from the next connection."""
        self.host, self.port = host, port

    async def show(self, key: int, image: bytes) -> None:
        """Puts an image (already in the deck's own format) on a key, uploading it if needed."""
        digest = hashlib.sha256(image).digest()[:16]
        hex_digest = digest.hex()
        if self._showing.get(key) == hex_digest or not self._ws:
            return
        async with self._send_lock:
            if hex_digest not in self._cached:
                await self._send(bytes([0x02]) + digest + image)
                self._cached.add(hex_digest)
            await self._send(json.dumps({"type": "show", "key": key, "hash": hex_digest}))
        self._showing[key] = hex_digest
        self._last_images[hex_digest] = image
        if len(self._last_images) > 256:
            showing = set(self._showing.values())
            self._last_images = {h: i for h, i in self._last_images.items() if h in showing}

    async def show_screen(self, x: int, y: int, width: int, height: int, image: bytes) -> None:
        """Puts a JPEG (in the screen's own orientation) on part of the deck's screen."""
        area = (x, y, width, height)
        if self._screen.get(area) == image or not self._ws:
            return
        async with self._send_lock:
            await self._send(bytes([0x03]) + struct.pack("<4H", *area) + image)
        self._screen[area] = image

    async def brightness(self, percent: int) -> None:
        await self._send(json.dumps({"type": "brightness", "percent": max(0, min(100, int(percent)))}))

    async def _send(self, message) -> None:
        if self._ws:
            try:
                await self._ws.send(message)
            except websockets.ConnectionClosed:
                pass

    async def _run(self) -> None:
        while True:
            try:
                async with websockets.connect(
                    f"ws://{self.host}:{self.port}/ws", max_size=None, ping_interval=PING_INTERVAL,
                    ping_timeout=PING_TIMEOUT, open_timeout=5
                ) as ws:
                    hello = json.loads(await ws.recv())
                    self._cached = set(hello.get("cached", []))
                    self._showing = {}
                    self._screen = {}
                    self._ws = ws
                    network = hello.get("network") or {}
                    log.info("%s: connected at %s over %s (firmware %s)", self.id, self.host,
                             _describe_network(network), hello.get("firmware"))
                    await self._set_deck(Deck.from_json(hello.get("deck")))
                    async for message in ws:
                        if isinstance(message, str):
                            # Handled by _work, so a slow Home Assistant call never stops this loop
                            # reading: a stalled reader misses pongs and the connection drops.
                            self._events.put_nowait(json.loads(message))
            except (OSError, asyncio.TimeoutError, websockets.WebSocketException) as error:
                if self._ws is not None or not isinstance(error, OSError):
                    log.warning("%s: connection lost (%s); retrying", self.id, error)
            finally:
                was_connected = self._ws is not None
                self._ws = None
                if was_connected:
                    await self._set_deck(None)
            await asyncio.sleep(RECONNECT_DELAY)

    async def _work(self) -> None:
        """Handles the bridge's events one at a time, in order."""
        while True:
            event = await self._events.get()
            try:
                await self._handle(event)
            except Exception:  # noqa: BLE001 - one bad event mustn't stop the rest
                log.exception("%s: handling %s", self.id, event.get("type"))

    async def _set_deck(self, deck: Deck | None) -> None:
        self.deck = deck
        self._showing = {}
        self._screen = {}
        if self.on_deck:
            await self.on_deck(deck)

    async def _handle(self, event: dict) -> None:
        kind = event.get("type")
        if kind == "key" and self.on_key:
            await self.on_key(event["key"], event["down"])
        elif kind == "dial" and self.on_dial:
            await self.on_dial(event["dial"], event.get("turn", 0), event.get("down"))
        elif kind == "touch" and self.on_touch:
            await self.on_touch(event.get("kind", "tap"), event.get("x", 0), event.get("y", 0))
        elif kind == "deck":
            await self._set_deck(Deck.from_json(event.get("deck")) if event.get("connected") else None)
        elif kind == "missing":
            # The bridge restarted or dropped it from its cache: upload it again.
            self._cached.discard(event["hash"])
            image = self._last_images.get(event["hash"])
            self._showing.pop(event["key"], None)
            if image:
                await self.show(event["key"], image)
        elif kind == "drawn" and not event.get("ok"):
            what = f"screen at {event.get('x')}, {event.get('y')}" if event.get("screen") else f"key {event.get('key')}"
            log.warning("%s: %s didn't draw: %s", self.id, what, event.get("error"))


def _describe_network(network: dict) -> str:
    """"Wi-Fi (-58 dBm)", "Ethernet", or "the network" for older firmware."""
    active = network.get("active")
    if active == "wifi":
        return f"Wi-Fi ({network['rssi']} dBm)" if "rssi" in network else "Wi-Fi"
    return "Ethernet" if active == "ethernet" else "the network"


class Discovery:
    """Watches mDNS for bridges and calls found(id, host, port) for each one, again if it moves."""

    def __init__(self, found: Callable[[str, str, int], None]):
        self._found = found
        self._zeroconf = None

    async def start(self) -> None:
        from zeroconf import ServiceStateChange
        from zeroconf.asyncio import AsyncServiceBrowser, AsyncServiceInfo, AsyncZeroconf

        self._zeroconf = AsyncZeroconf()
        loop = asyncio.get_running_loop()

        async def resolve(name: str) -> None:
            info = AsyncServiceInfo(SERVICE, name)
            if await info.async_request(self._zeroconf.zeroconf, 3000) and info.parsed_addresses():
                try:
                    self._found(panel_id(name.removesuffix("." + SERVICE)), info.parsed_addresses()[0], info.port)
                except ValueError:
                    log.warning("Ignoring a bridge with an unexpected name: %s", name)

        def changed(zeroconf, service_type, name, state_change):
            if state_change in (ServiceStateChange.Added, ServiceStateChange.Updated):
                loop.create_task(resolve(name))

        self._browser = AsyncServiceBrowser(self._zeroconf.zeroconf, SERVICE, handlers=[changed])

    async def stop(self) -> None:
        if self._zeroconf:
            await self._zeroconf.async_close()
