#!/usr/bin/env python3
"""Finds bridges on the network, connects to one and exercises the protocol.

    python tools/test_bridge.py                  # find bridges over mDNS and use the first
    python tools/test_bridge.py sdbridge-19c9a4  # or name one (host name or IP address)

Uploads two images per key to the bridge's image cache (a numbered colored square, and the
same with its colors flipped), shows the first on every key and flips a key while it's held,
printing every message from the bridge and how long each press took to show. Needs: websockets pillow zeroconf.
"""
import asyncio
import hashlib
import io
import json
import socket
import sys
import time

import websockets
from PIL import Image, ImageDraw, ImageFont

SERVICE = "_sdbridge._tcp.local."
COLORS = ["#0A84FF", "#30D158", "#FF9F0A", "#FF453A", "#BF5AF2", "#64D2FF", "#FFD60A", "#AC8E68"]

# Output pixel (x, y) comes from source pixel ... (see docs/BRIDGE-PROTOCOL.md); PIL's
# rotations are counterclockwise.
TRANSFORMS = {
    "none": None,
    "transpose": Image.Transpose.TRANSPOSE,
    "rotate90": Image.Transpose.ROTATE_270,
    "rotate270": Image.Transpose.ROTATE_90,
    "rotate180": Image.Transpose.ROTATE_180,
}


def find_bridges(wait=3.0):
    from zeroconf import ServiceBrowser, Zeroconf

    found = {}

    class Listener:
        def add_service(self, zc, kind, name):
            info = zc.get_service_info(kind, name)
            if info and info.parsed_addresses():
                txt = {k.decode(): (v or b"").decode() for k, v in info.properties.items()}
                found[name.split(".")[0]] = (info.parsed_addresses()[0], info.port, txt)

        update_service = add_service

        def remove_service(self, *args):
            pass

    zc = Zeroconf()
    ServiceBrowser(zc, SERVICE, Listener())
    time.sleep(wait)
    zc.close()
    return found


def key_image(deck, key, pressed):
    """A whole set-key frame: [0x01][key][image]."""
    return bytes([0x01, key]) + render(deck, key, pressed)


def cache_frame(image):
    """A cache frame, [0x02][hash][image], and the hash as hex."""
    digest = hashlib.sha256(image).digest()[:16]
    return bytes([0x02]) + digest + image, digest.hex()


def render(deck, key, pressed):
    size = deck["keySize"]
    color = COLORS[key % len(COLORS)]
    image = Image.new("RGB", (size, size), "white" if pressed else color)
    draw = ImageDraw.Draw(image)
    try:
        font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", size // 2)
    except OSError:
        font = ImageFont.load_default()
    draw.text((size / 2, size / 2), str(key), fill=color if pressed else "white", font=font, anchor="mm")

    transform = TRANSFORMS[deck["transform"]]
    if transform is not None:
        image = image.transpose(transform)
    out = io.BytesIO()
    if deck["format"] == "bmp":
        image.save(out, "BMP", dpi=(72, 72))
    else:
        image.save(out, "JPEG", quality=90)
    return out.getvalue()


async def run(host):
    async with websockets.connect(f"ws://{host}/ws", max_size=None, ping_interval=10) as ws:
        hello = json.loads(await ws.recv())
        print("hello:", json.dumps(hello, indent=1))
        deck = hello.get("deck")
        hashes = {}          # (key, pressed) -> hash
        pressed_at = {}      # key -> when its press or release arrived

        async def upload_all():
            started = time.time()
            for key in range(deck["keys"]):
                for pressed in (False, True):
                    frame, digest = cache_frame(render(deck, key, pressed))
                    hashes[key, pressed] = digest
                    if digest not in hello.get("cached", []):
                        await ws.send(frame)
            for key in range(deck["keys"]):
                await ws.send(json.dumps({"type": "show", "key": key, "hash": hashes[key, False]}))
            print(f"uploaded and showed {deck['keys']} keys in {time.time() - started:.2f} s")

        if deck:
            await upload_all()
        await ws.send(json.dumps({"type": "ping"}))

        async for message in ws:
            event = json.loads(message)
            if event["type"] == "cached":
                continue
            note = ""
            if event["type"] == "drawn" and event["key"] in pressed_at:
                note = f"  <- {1000 * (time.time() - pressed_at.pop(event['key'])):.0f} ms after the key event"
            print(time.strftime("%H:%M:%S"), event, note)
            if event["type"] == "key" and deck:
                pressed_at[event["key"]] = time.time()
                await ws.send(json.dumps({"type": "show", "key": event["key"], "hash": hashes[event["key"], event["down"]]}))
            elif event["type"] == "deck":
                deck = event.get("deck") if event["connected"] else None
                if deck:
                    await upload_all()


def main():
    if len(sys.argv) > 1:
        host = sys.argv[1]
        if "." not in host and not host.replace(":", "").isdigit():
            host += ".local"
    else:
        bridges = find_bridges()
        if not bridges:
            sys.exit("No bridges found on the network.")
        for name, (address, port, txt) in bridges.items():
            print(f"found {name} at {address}:{port} {txt}")
        address, port, _ = next(iter(bridges.values()))
        host = f"{address}:{port}"
    try:
        asyncio.run(run(host))
    except KeyboardInterrupt:
        pass
    except (OSError, socket.gaierror, websockets.WebSocketException) as error:
        sys.exit(f"Couldn't talk to {host}: {error}")


if __name__ == "__main__":
    main()
