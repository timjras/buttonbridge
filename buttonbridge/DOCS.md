# ButtonBridge

This app draws Home Assistant buttons on Stream Decks that are connected to the network through ESP32
bridges (see the repository's `firmware/bridge`).

1. Start the app. Every bridge it finds shows **New panel** and its ID, like `sd-a1b2c3`, on its keys.
2. Open **ButtonBridge** in Home Assistant's sidebar. New panels are listed on the left: name one and
   add it, then click its keys to set them up, and press **Save**.
3. Everything is saved to `/config/streamdeck.yaml`, which you can also edit by hand. The format is
   in `docs/CONFIG.md` in the repository. Saving updates the panels within a couple of
   seconds; a file with a mistake leaves them on the last good version, and the log says what's
   wrong and on which line.

## Options

- `config_file`: the file in Home Assistant's config folder to read. Default `streamdeck.yaml`.
- `debug`: more detail in the log.
