# ButtonBridge

Home Assistant buttons on Elgato Stream Decks, anywhere in the house, with no computer next to them.

Each panel is a Stream Deck plugged into a small ESP32 board, the **bridge**, which puts it on your
network. With a PoE board, one network cable powers both. A Home Assistant app draws every key,
runs what it does, and adds a setup page to Home Assistant's sidebar.

```
 Home Assistant ── ButtonBridge app (draws the keys, runs the actions, setup page)
        │  your network
        ▼
 ESP32 bridge ──USB── Stream Deck          (one bridge per panel)
```

## What it does

- **Live state on the keys**: lights, switches, covers, sensors, scenes and scripts, with your own
  icons ([Material Design Icons](https://pictogrammers.com/library/mdi/) or images) and colours for
  each state.
- **Whole rooms and floors**: one key for every light in an area, showing "any on" or "all off".
- **Long press, double press and hold to dim**, set per key.
- **Pages**, plus a **clock** or a picture spread across the keys.
- **Brightness schedules and Sleep Mode**: dim at night, show a clock, wake on a press.
- **Stream Deck +**: the four dials set brightness, volume, cover position and more, with their
  values on the touch strip.
- **A setup page in Home Assistant's sidebar** that writes one YAML file, `streamdeck.yaml`, which
  you can also edit by hand. Home Assistant's backups include it.
- **Fast**: on the ESP32-P4 board a key redraws in about 2 ms.

## Hardware

| | |
|---|---|
| Bridge (recommended) | Waveshare **ESP32-P4-WIFI6-POE-ETH**, about $25. Ethernet with PoE, high-speed USB host. |
| Bridge (alternative) | ESP32-S3 dev kit (N16R8) over Wi-Fi. USB is full speed only, so a Mini takes about 0.3 s per key. |
| Deck, tested | Stream Deck Mini, Stream Deck + |
| Deck, should work | Stream Deck Original, MK.2, XL, Modules 6/15/32 (same protocol, not yet tried on hardware) |

A 3D printable case for the ESP32-P4 board is in [hardware/enclosure](hardware/enclosure/).

The ESP32-P4 board can also run on USB power and Wi-Fi (through its ESP32-C6) instead of PoE;
see [firmware/bridge/README.md](firmware/bridge/README.md#wi-fi-instead-of-ethernet).

## Getting started

Follow [docs/INSTALL.md](docs/INSTALL.md): flash the bridge from your browser with the
[ButtonBridge installer](https://timjras.github.io/buttonbridge/), plug in the deck,
add this repository to Home Assistant, and set up your first panel. It takes about half an hour.

[![Add repository to Home Assistant](https://my.home-assistant.io/badges/supervisor_add_addon_repository.svg)](https://my.home-assistant.io/redirect/supervisor_add_addon_repository/?repository_url=https%3A%2F%2Fgithub.com%2Ftimjras%2Fbuttonbridge)

## In this repository

| Folder | What's in it |
|---|---|
| [buttonbridge/](buttonbridge/) | The Home Assistant app, including the renderer (Python) and the setup page |
| [firmware/bridge/](firmware/bridge/) | Bridge firmware (ESP-IDF 5.5) for the ESP32-P4 and ESP32-S3 |
| [docs/INSTALL.md](docs/INSTALL.md) | Setting up, updating, and troubleshooting |
| [docs/CONFIG.md](docs/CONFIG.md) | The `streamdeck.yaml` format |
| [docs/BRIDGE-PROTOCOL.md](docs/BRIDGE-PROTOCOL.md) | How the app talks to a bridge |
| [examples/](examples/) | A complete example config |
| [hello_deck/](hello_deck/) | A one-key test that drives a Stream Deck over USB from a computer |

## Developing

```bash
python3 -m venv venv && source venv/bin/activate
pip install -r requirements-dev.txt
python -m pytest
```

To run the renderer from a computer against your Home Assistant, see
[buttonbridge/renderer/README.md](buttonbridge/renderer/README.md). Firmware build steps are in
[firmware/bridge/README.md](firmware/bridge/README.md).

## Credits and license

MIT License, see [LICENSE](LICENSE).

The bridge's Stream Deck driver comes from [ESPDeck](https://github.com/jangellx/ESPDeck) by Joe
Angell (MIT), whose model tables derive from
[python-elgato-streamdeck](https://github.com/abcminiuser/python-elgato-streamdeck) by Dean Camera.
The firmware includes a patched copy of Espressif's USB Host Library (Apache 2.0). Details are in
[firmware/bridge/THIRD-PARTY-NOTICES.md](firmware/bridge/THIRD-PARTY-NOTICES.md).

ButtonBridge is not affiliated with or endorsed by Elgato, Corsair, or the Open Home Foundation.
Stream Deck is a trademark of Corsair Memory, Inc. Home Assistant is a trademark of the Open Home
Foundation.
