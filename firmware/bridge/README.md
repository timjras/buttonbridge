# Bridge firmware (ESP32-P4 or ESP32-S3)

Carries key images and button presses between the network and a Stream Deck plugged into the
board's USB host port, including a Stream Deck +'s dials and touch strip. Protocol: [docs/BRIDGE-PROTOCOL.md](../../docs/BRIDGE-PROTOCOL.md).
ESP-IDF 5.5.

| Board | Network | Deck port | Mini key redraw |
|---|---|---|---|
| Waveshare ESP32-P4-WIFI6-POE-ETH | Ethernet with PoE, or Wi-Fi through its ESP32-C6 | USB-A, high speed | 2 ms (5 ms from the renderer with a cached image) |
| ESP32-S3 N16R8 dev kit | Wi-Fi | native USB port through an OTG adapter, full speed | ~320 ms |

Stream Decks from the Mini onwards are USB 2.0 high-speed devices. The S3 only does full
speed, so it's limited to 64 bytes per millisecond: a Mini's 19 KB key image takes 320 ms.
A Mac sends the same image in 8 ms.

## ESP32-P4 (Waveshare ESP32-P4-WIFI6-POE-ETH)

```bash
. ~/esp/esp-idf-v5.5/export.sh
cd firmware/bridge
idf.py -B build-p4 -D SDKCONFIG=sdkconfig.p4 set-target esp32p4     # once
idf.py -B build-p4 -D SDKCONFIG=sdkconfig.p4 build
idf.py -B build-p4 -D SDKCONFIG=sdkconfig.p4 -p /dev/cu.usbmodem* flash   # its USB-C port
```

Plug in Ethernet (PoE powers the board) and the deck's cable into the USB-A port. The board
gets an address by DHCP and advertises itself over mDNS as usual. The build targets v3.x
silicon; for a v1.x chip add `sdkconfig.defaults.esp32p4-rev1` (see
`sdkconfig.defaults.esp32p4`).

If `build-p4` and `sdkconfig.p4` predate Wi-Fi support (firmware 0.5.0 or older), delete both
once so the build picks up the Wi-Fi settings from `sdkconfig.defaults.esp32p4`.

### Wi-Fi instead of Ethernet

The board's Wi-Fi is a separate ESP32-C6 (2.4 GHz only) on SDIO, running Espressif's
ESP-Hosted firmware; the bridge reaches it through the `esp_hosted` and `esp_wifi_remote`
components. Power the board from a USB-C charger instead of PoE (the deck's USB-A port is fed
from the same 5 V rail), and save a network with the board's USB-C port plugged into the Mac:

```bash
python3 tools/set_wifi.py                 # asks for the network name and password
python3 tools/set_wifi.py --use wifi      # optional: never use Ethernet
python3 tools/set_wifi.py --use ethernet  # back to Ethernet only
python3 tools/set_wifi.py --use auto      # the default
```

In `auto` the board uses Ethernet when the cable has a link within 6 s of starting, and
Wi-Fi otherwise; it doesn't switch while running. With no network saved it behaves exactly
as before. The hostname (`sdbridge-xxxxxx`) stays the same either way, so the renderer finds
it at its new address without any config change. `GET /info` shows the choice under
`network`, with the signal strength on Wi-Fi.

The SDIO link starts even on Ethernet, but a C6 that doesn't answer only logs errors: it
can't restart the bridge.

### Updating the Wi-Fi chip's firmware

Boards ship with an early ESP-Hosted build on the C6 (it reports version 0.0.0). It works,
but ESP-Hosted warns about RPC timeouts with it, and Wi-Fi response times swing from a few
milliseconds to several hundred. `GET /info` shows the C6's version as
`network.coprocessor` once Wi-Fi has started.

The update goes from the P4 to the C6 over their SDIO link, so it needs no extra cable. Get
`coprocessor-esp32c6-esp-hosted-<version>.bin` from the release (or the **Firmware** workflow's
artifacts), or build it yourself:

```bash
idf.py create-project-from-example "espressif/esp_hosted==2.13.1:slave"   # the version in main/idf_component.yml
cd slave && idf.py set-target esp32c6 build                               # build/network_adapter.bin
```

Then send it to the bridge, over Ethernet if it has a cable:

```bash
curl --data-binary @coprocessor-esp32c6-esp-hosted-2.13.1.bin http://sdbridge-xxxxxx.local/coprocessor
```

It takes about half a minute, then the bridge restarts. The C6 writes the new firmware to its
spare slot and only switches once it's all there, so a failed upload leaves the old one
running.

## ESP32-S3

```bash
. ~/esp/esp-idf-v5.5/export.sh
cd firmware/bridge
idf.py build
idf.py -p /dev/cu.usbmodem* flash      # the board's USB port, plugged into the Mac
python3 tools/set_wifi.py              # once, to join Wi-Fi
```

With a computer on the native USB port the board stays a serial port and doesn't look for a
deck. With the deck there (through the OTG adapter), it starts the USB host instead. Logs
always go to the COM (UART) port, and to the USB port too while a computer is on it.

After the first flash, update over the network (either board):

```bash
curl --data-binary @build/sdbridge.bin http://sdbridge-xxxxxx.local/update
```

## Diagnostics over the network

- `GET /log`: the most recent log output (32 KB), for a board with no cable to it.
- `GET /coredump`: the crash dump from the last panic, if any. Decode it with
  `esp-coredump info_corefile -t raw -c dump.bin build-p4/sdbridge.elf`.
- `GET /info` includes `resetReason` (ESP-IDF's `esp_reset_reason_t`; 4 is a panic) and `uptime`.

## Credits

`components/usb` is ESP-IDF's USB Host Library 1.6.0 with one fix; see
[components/usb/BUTTONBRIDGE.md](components/usb/BUTTONBRIDGE.md).

`main/StreamDeck.{h,cpp}` and `main/Timing.h` come from
[ESPDeck](https://github.com/jangellx/ESPDeck) by Joe Angell (MIT License), whose model
layouts and report formats derive from
[python-elgato-streamdeck](https://github.com/abcminiuser/python-elgato-streamdeck) by Dean
Camera (MIT License). See [THIRD-PARTY-NOTICES.md](THIRD-PARTY-NOTICES.md).

## Releases

GitHub Actions (`.github/workflows/firmware.yml`) builds three images on every pull request that
touches the firmware: `esp32p4` (silicon v3.x), `esp32p4-rev1` (silicon v1.x) and `esp32s3`, each
as a `-full.bin` to flash at 0x0 and an `-update.bin` for `POST /update`.

To release, bump `PROJECT_VER` in `CMakeLists.txt`, merge, then push a tag:

```bash
git tag v0.6.0 && git push origin v0.6.0
```

The tag's run attaches the files to a GitHub release and publishes the browser installer
(`docs/flasher/`) to GitHub Pages. Pages needs **Settings > Pages > Source: GitHub Actions** once,
and a public repository (or a paid plan).
