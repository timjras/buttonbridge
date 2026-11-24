#!/usr/bin/env python3
"""One-button Home Assistant test for a Stream Deck.

Key 0 shows the live on/off state of one entity, and pressing it toggles that
entity. Settings come from environment variables (see SETUP.md):

    HA_URL     e.g. ws://homeassistant.local:8123/api/websocket
    HA_TOKEN   a long-lived access token from your HA profile page
    HA_ENTITY  e.g. light.living_room
"""

import asyncio
import json
import os
import sys

from PIL import Image, ImageDraw, ImageFont
import websockets
from StreamDeck.DeviceManager import DeviceManager
from StreamDeck.ImageHelpers import PILHelper

HA_URL = os.environ.get("HA_URL", "ws://homeassistant.local:8123/api/websocket")
HA_TOKEN = os.environ.get("HA_TOKEN", "")
HA_ENTITY = os.environ.get("HA_ENTITY", "")

KEY = 0


def render_key(deck, entity_id, state):
    """Draw a simple key: yellow when on, dark grey when off, with labels."""
    on = state == "on"
    image = PILHelper.create_key_image(deck, background="#f5b700" if on else "#222222")
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default()
    w, h = image.size
    name = entity_id.split(".", 1)[-1].replace("_", " ")[:12]
    fg = "black" if on else "white"
    draw.text((w / 2, h * 0.35), name, font=font, anchor="mm", fill=fg)
    draw.text((w / 2, h * 0.7), (state or "?").upper(), font=font, anchor="mm", fill=fg)
    return PILHelper.to_native_key_format(deck, image)


class HomeAssistant:
    """Minimal Home Assistant websocket client."""

    def __init__(self, url, token):
        self.url = url
        self.token = token
        self.ws = None
        self.next_id = 1

    async def connect(self):
        self.ws = await websockets.connect(self.url, max_size=None)
        msg = json.loads(await self.ws.recv())
        if msg.get("type") != "auth_required":
            raise RuntimeError(f"unexpected greeting: {msg}")
        await self.ws.send(json.dumps({"type": "auth", "access_token": self.token}))
        msg = json.loads(await self.ws.recv())
        if msg.get("type") != "auth_ok":
            raise RuntimeError("Home Assistant rejected the token (check HA_TOKEN)")

    async def send(self, payload):
        payload["id"] = self.next_id
        self.next_id += 1
        await self.ws.send(json.dumps(payload))
        return payload["id"]

    async def toggle(self, entity_id):
        await self.send({
            "type": "call_service",
            "domain": "homeassistant",
            "service": "toggle",
            "target": {"entity_id": entity_id},
        })


async def main():
    if not HA_TOKEN or not HA_ENTITY:
        sys.exit("Set HA_TOKEN and HA_ENTITY first (see SETUP.md).")

    decks = DeviceManager().enumerate()
    if not decks:
        sys.exit("No Stream Deck found. Is it plugged in, and is the udev rule installed?")
    deck = decks[0]
    deck.open()
    deck.reset()
    deck.set_brightness(60)
    print(f"Using {deck.deck_type()} with {deck.key_count()} keys")

    ha = HomeAssistant(HA_URL, HA_TOKEN)
    await ha.connect()
    print("Connected to Home Assistant")

    loop = asyncio.get_running_loop()
    presses = asyncio.Queue()

    # The deck library calls this from its own thread, so hand off to asyncio.
    def on_key(_deck, key, pressed):
        if pressed and key == KEY:
            loop.call_soon_threadsafe(presses.put_nowait, key)

    deck.set_key_callback(on_key)

    states_id = await ha.send({"type": "get_states"})
    await ha.send({"type": "subscribe_events", "event_type": "state_changed"})

    def show(state):
        with deck:
            deck.set_key_image(KEY, render_key(deck, HA_ENTITY, state))

    show(None)

    async def handle_presses():
        while True:
            await presses.get()
            await ha.toggle(HA_ENTITY)

    press_task = asyncio.create_task(handle_presses())
    try:
        async for raw in ha.ws:
            msg = json.loads(raw)
            if msg.get("id") == states_id and msg.get("type") == "result":
                for s in msg.get("result") or []:
                    if s["entity_id"] == HA_ENTITY:
                        show(s["state"])
                        print(f"{HA_ENTITY} is {s['state']}")
                        break
                else:
                    print(f"Warning: {HA_ENTITY} not found in Home Assistant")
            elif msg.get("type") == "event":
                data = msg["event"].get("data", {})
                if data.get("entity_id") == HA_ENTITY and data.get("new_state"):
                    state = data["new_state"]["state"]
                    show(state)
                    print(f"{HA_ENTITY} -> {state}")
    finally:
        press_task.cancel()
        with deck:
            deck.reset()
            deck.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
