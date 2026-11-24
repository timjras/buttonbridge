"""The renderer: connects Home Assistant, the config file and every bridge on the network.

python -m renderer run streamdeck.yaml
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from .bridges import Bridge, Deck, Discovery
from .config import Config, ConfigError, load_config
from .draw import KeyLook, render_dial, render_key, split_text, to_native
from .ha import HomeAssistant
from .panel import PanelController

log = logging.getLogger(__name__)

CONFIG_POLL = 2.0  # seconds between checks for a changed config file


class Renderer:
    def __init__(self, config_path: Path, ha: HomeAssistant, bridge_hosts: list[str] | None = None,
                 web_port: int | None = None, ingress_only: bool = False, bridges: bool = True):
        self.config_path = config_path
        self.ha = ha
        self.config: Config | None = None
        self.bridges: dict[str, Bridge] = {}
        self.panels: dict[str, PanelController] = {}  # by bridge id
        self._config_mtime = 0.0
        self._bridge_hosts = bridge_hosts or []
        self._web_port = web_port
        self._ingress_only = ingress_only
        self._use_bridges = bridges

    async def run(self) -> None:
        self.ha.start()
        self._load_config(first=True)
        discovery = Discovery(self._found)
        if self._use_bridges:
            await discovery.start()
        for host in self._bridge_hosts:
            await self._add_host(host)
        if self._web_port:
            from .web import SetupPage

            await SetupPage(self, self._web_port, self._ingress_only).start()
        try:
            while True:
                await asyncio.sleep(CONFIG_POLL)
                self._load_config()
        finally:
            if self._use_bridges:
                await discovery.stop()

    async def _add_host(self, host: str) -> None:
        """A bridge named on the command line, for networks where mDNS doesn't reach."""
        import json
        import urllib.request

        def fetch():
            with urllib.request.urlopen(f"http://{host}/info", timeout=5) as response:
                return json.load(response)

        try:
            info = await asyncio.to_thread(fetch)
        except OSError as error:
            log.warning("No bridge at %s: %s", host, error)
            return
        from .bridges import panel_id

        self._found(panel_id(info["mac"]), host, 80)

    # --- Config -------------------------------------------------------------

    def reload_soon(self) -> None:
        """The config file was just written: load it on the next check, whatever its timestamp."""
        self._config_mtime = 0.0

    def _load_config(self, first: bool = False) -> None:
        try:
            mtime = self.config_path.stat().st_mtime
        except FileNotFoundError:
            if first:
                log.warning("%s doesn't exist yet; every panel will show its ID", self.config_path)
                self.config = Config()
            return
        if mtime == self._config_mtime:
            return
        self._config_mtime = mtime
        try:
            config = load_config(self.config_path)
        except ConfigError as error:
            log.error("%s has problems, so the panels keep the last good version:\n  %s", self.config_path, "\n  ".join(error.problems))
            if self.config is None:
                self.config = Config()
            return
        log.info("Loaded %s: %d panel(s)", self.config_path, len(config.panels))
        self.config = config
        self.ha.clear_templates()
        for bridge in self.bridges.values():
            self._assign(bridge)

    # --- Bridges ------------------------------------------------------------

    def _found(self, bridge_id: str, host: str, port: int) -> None:
        bridge = self.bridges.get(bridge_id)
        if bridge:
            if (bridge.host, bridge.port) != (host, port):
                bridge.moved(host, port)
            return
        log.info("Found bridge %s at %s", bridge_id, host)
        bridge = Bridge(bridge_id, host, port)
        self.bridges[bridge_id] = bridge
        self._assign(bridge)
        bridge.start()

    def _assign(self, bridge: Bridge) -> None:
        """Hands a bridge to its panel's controller, or shows its ID if it isn't configured."""
        old = self.panels.pop(bridge.id, None)
        if old:
            old.detach()
        for name, panel in (self.config.panels if self.config else {}).items():
            if panel.id == bridge.id:
                controller = PanelController(name, panel, self.config.styles, bridge, self.ha, self.config_path.parent)
                self.panels[bridge.id] = controller
                controller.attach()
                return

        async def show_id(deck: Deck | None) -> None:
            if deck:
                await self._show_id(bridge, deck)

        async def ignore_key(_key: int, _down: bool) -> None:
            pass

        bridge.on_deck = show_id
        bridge.on_key = ignore_key
        bridge.on_dial = bridge.on_touch = None
        if bridge.deck:
            asyncio.create_task(self._show_id(bridge, bridge.deck))

    async def _show_id(self, bridge: Bridge, deck: Deck) -> None:
        log.info("Bridge %s isn't in the config; showing its ID on the keys", bridge.id)
        await bridge.brightness(60)
        parts = ["New", "panel"] + split_text(bridge.id, max(1, deck.keys - 2))
        for key in range(deck.keys):
            text = parts[key] if key < len(parts) else ""
            image = render_key(KeyLook(text=text, background="dark"), deck.key_size)
            await bridge.show(key, to_native(image, deck.format, deck.transform))
        if deck.dials and deck.screen_width:   # a Stream Deck +: blank its touch strip
            blank = to_native(render_dial(None, deck.screen_width, deck.screen_height), "jpeg", deck.screen_transform)
            await bridge.show_screen(0, 0, deck.screen_width, deck.screen_height, blank)
