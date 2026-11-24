# Renderer

The Python program inside the ButtonBridge app. It reads `streamdeck.yaml`, follows Home
Assistant's state over its WebSocket API, draws each key, sends the images to the bridges, acts on
presses, and serves the setup page. The config format is in [docs/CONFIG.md](../../docs/CONFIG.md).

It lives inside the app's folder because that folder is what Home Assistant builds the app from.

## Running it from a computer

Handy while developing. Only one renderer should drive a bridge at a time, so stop the Home
Assistant app first, or use `--no-bridges` to try only the setup page.

From the repository's top folder:

```bash
python3 -m venv venv && source venv/bin/activate
pip install -r requirements-dev.txt
cd buttonbridge
```

Create a long-lived access token in Home Assistant (your profile, **Security** tab) and save it in
`~/.config/streamdeck-ha/token`. Then:

```bash
python -m renderer check ../examples/streamdeck.yaml     # check a config file for mistakes
python -m renderer run streamdeck.yaml --ha http://homeassistant.local:8123 --web-port 8099
```

The setup page is then at <http://localhost:8099>. If mDNS doesn't find a bridge, add
`--bridge <its address>`.

## Running the tests

From the repository's top folder: `python -m pytest`.
