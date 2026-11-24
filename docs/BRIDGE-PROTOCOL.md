# Bridge protocol (version 3)

How the renderer talks to an ESP32-S3 bridge. The bridge is deliberately thin: it shows the
images it's sent and reports key presses. Everything else (Home Assistant, icons, pages, one
image across several panels) happens in the renderer.

## Finding bridges

Each bridge advertises `_sdbridge._tcp` on mDNS, port 80, with the instance and host name
`sdbridge-xxxxxx` (the last three bytes of its MAC). TXT records:

| Key | Value |
|---|---|
| `protocol` | this document's version, `3` |
| `fw` | firmware version |
| `mac` | Wi-Fi MAC address |
| `model` | the attached deck's model, or `none` |
| `keys` | the attached deck's key count, or `0` |

## HTTP

- `GET /info`: the bridge and its deck, as in `hello` below without `type`.
- `POST /update`: body is a new app image (`build/sdbridge.bin`). The bridge installs it and
  restarts. If the new image can't get back onto the network it rolls back on the next restart.
  There's no authentication yet; it's meant for a trusted home network only.
- `POST /coprocessor` (ESP32-P4 board, firmware 0.5.2 on): body is an ESP-Hosted co-processor
  image for the board's ESP32-C6 Wi-Fi chip. The bridge passes it to the C6 and restarts.

## WebSocket `/ws`

One renderer at a time; a new connection replaces the old one.

### Bridge to renderer (text, JSON)

On connecting:

```json
{"type":"hello","protocol":3,"firmware":"0.1.0","hostname":"sdbridge-19c9a4",
 "mac":"90:70:69:19:c9:a4","maxImage":65536,
 "deck":{"model":"Stream Deck Mini","pid":99,"rows":2,"cols":3,"keys":6,"keySize":80,
         "format":"bmp","transform":"rotate90","serial":"...","firmware":"...",
         "dials":0,"screen":null}}
```

`dials` is the Stream Deck +'s 4, and `screen` describes a screen besides the keys:
`{"width":800,"height":100,"transform":"none"}` for the +'s touch strip (the Neo's info bar
is 248 × 58, `rotate180`). Version 2 bridges leave both out.

`deck` is `null` when no deck is attached. The hello also carries `"cached":["<hash>", ...]`,
the images the bridge already holds (see Image cache), and `usb` (the native port's `mode`
and the `lastDevice` seen on it, for diagnosing a deck that doesn't appear).

From firmware 0.5.1 it also carries `network`: what the board `has` (`"ethernet"`, `"wifi"`),
which it's set to `use` (`"auto"`, `"ethernet"` or `"wifi"`), the one that's `active` (or
`null`), and on Wi-Fi the saved `ssid` and the signal `rssi` in dBm. On the P4 board,
`coprocessor` is the Wi-Fi chip's ESP-Hosted firmware version (`null` until Wi-Fi has started):
`"network":{"has":["ethernet","wifi"],"use":"auto","active":"wifi","ssid":"home","rssi":-61}`.

When a deck is plugged in or out: `{"type":"deck","connected":true,"deck":{...}}` or
`{"type":"deck","connected":false}`. After a deck of the same model comes back, the bridge
redraws what it last showed, so the renderer only needs to resend if the model changed.

Key presses: `{"type":"key","key":3,"down":true}`, then `"down":false` on release. Keys are
numbered row by row from the top left, as seen from the front.

On a Stream Deck +, dials and the touch strip (dials count from 0 at the left; the strip's
pixels from its top left):

- `{"type":"dial","dial":1,"turn":-2}`: turned 2 clicks counterclockwise (positive is clockwise)
- `{"type":"dial","dial":1,"down":true}`, then `"down":false`: pushed in and let go
- `{"type":"touch","kind":"tap","x":250,"y":40}`: `kind` is `tap`, `long` (a long press) or
  `swipe`, which also has `toX` and `toY`

After each key image reaches the deck: `{"type":"drawn","key":3,"ok":true,"ms":319}`, where
`ms` is how long the deck took over USB (with `"error"` when `ok` is false). A Mini takes
about 320 ms per key: its 19 KB BMP goes over a full-speed interrupt endpoint at 64 bytes
per millisecond, so that's its hardware floor.

### Renderer to bridge

**Key image** (binary): `[0x01][key][image bytes]`. The image must already be in the deck's
own format: `keySize` square, encoded as `format` (`bmp` or `jpeg`), with `transform`
applied. `PILHelper.to_native_key_format()` in python-elgato-streamdeck produces exactly
this. If several images for one key arrive faster than the deck can draw them, only the
newest is drawn.

### Image cache

The bridge keeps up to 4 MB of key images in memory, dropping the least recently used first.
They're lost on restart; the hello says what's still there. Upload each image once, then
switch keys between them with `show`. A press then costs a short message over Wi-Fi instead
of a whole image: on a Mini, about 0.34 s from `show` to `drawn`, almost all of it USB.

**Cache an image** (binary): `[0x02][hash][image bytes]`, where `hash` is the first 16 bytes
of the image's SHA-256 and the image is in the deck's own format as above. The bridge checks
the hash and answers `{"type":"cached","hash":"<32 hex>","ok":true}`.

**Show a cached image** (text): `{"type":"show","key":3,"hash":"<32 hex>"}`. If the bridge
doesn't have it: `{"type":"missing","key":3,"hash":"<32 hex>"}`, and the renderer uploads it
again.

### Other messages to the bridge

**Screen image** (binary): `[0x03][x][y][width][height][image bytes]`, each number 16-bit
little-endian: a JPEG of `width` × `height` with the screen's `transform` applied, drawn at
(`x`, `y`). The +'s strip takes any part of itself, so the renderer draws it in four 200 × 100
pieces, one above each dial; the Neo's info bar only takes the whole bar. The bridge answers
`{"type":"drawn","screen":true,"x":200,"y":0,"ok":true,"ms":3}` and redraws the strip with the
keys when the deck is plugged back in.

**Text (JSON)**:
- `{"type":"brightness","percent":60}`
- `{"type":"ping"}`, answered with `{"type":"pong"}`

## Serial (setup)

Over the USB cable, one JSON line in, a line starting `@@` back:
- `{"cmd":"info"}`: the same JSON as `GET /info`.
- `{"cmd":"wifi","ssid":"...","password":"..."}`: saves the network and restarts.
  `firmware/bridge/tools/set_wifi.py` does this interactively.
- `{"cmd":"network","use":"auto"}`: on a board with Ethernet and Wi-Fi, picks between them
  (`"auto"`, `"ethernet"` or `"wifi"`) and restarts. `"auto"`, the default, uses Ethernet
  when the cable has a link within 6 s of starting and Wi-Fi otherwise. `set_wifi.py --use`
  sends it.
