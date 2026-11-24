# Step 2: Stream Deck Mini on a Raspberry Pi Zero 2 W

By the end of this guide, key 1 on your Mini shows whether a Home Assistant light is on or off, and pressing it toggles the light.

## What you need

- Raspberry Pi Zero 2 W
- microSD card, 16 GB or larger
- 5 V / 2.5 A micro-USB power supply
- A micro-USB OTG adapter or cable to plug the Mini's USB cable into the Pi
- Your Stream Deck Mini
- A computer on the same network (Mac, Windows or Linux)

> The Zero 2 W only does **2.4 GHz** Wi-Fi. Make sure your network offers it.

## 1. Flash the SD card

1. Install **Raspberry Pi Imager** from <https://www.raspberrypi.com/software/> on your computer.
2. Open it and choose:
   - **Device:** Raspberry Pi Zero 2 W
   - **OS:** Raspberry Pi OS (other) → **Raspberry Pi OS Lite (64-bit)**
   - **Storage:** your SD card
3. When it asks about OS customisation, click **Edit settings** and set:
   - **Hostname:** `streamdeck`
   - **Username and password:** pick your own (this guide assumes the user `pi`)
   - **Wireless LAN:** your Wi-Fi name, password and country
   - **Services tab:** tick **Enable SSH** and choose password authentication
4. Write the card, put it in the Pi and plug power into the port labelled **PWR**.
5. Wait about 2 minutes for the first boot.

## 2. Log in to the Pi

On your computer, open a terminal (Mac: Terminal; Windows: PowerShell) and run:

```bash
ssh pi@streamdeck.local
```

Type `yes` if it asks about a fingerprint, then your password. Every command below is typed into this SSH session unless the guide says otherwise.

> If `streamdeck.local` isn't found, look up the Pi's IP address in your router's device list and use `ssh pi@<that-ip>`.

## 3. Install the system packages

```bash
sudo apt update && sudo apt full-upgrade -y
sudo apt install -y python3-venv libhidapi-libusb0
```

This takes a while on a Zero 2 W the first time.

## 4. Let your user talk to the Stream Deck

By default only root can access USB devices. This rule allows any Elgato device (vendor id `0fd9`) for users in the `plugdev` group, which `pi` is already in:

```bash
sudo tee /etc/udev/rules.d/70-streamdeck.rules > /dev/null <<'EOF'
SUBSYSTEM=="usb", ATTRS{idVendor}=="0fd9", MODE="0660", GROUP="plugdev"
KERNEL=="hidraw*", ATTRS{idVendor}=="0fd9", MODE="0660", GROUP="plugdev"
EOF
sudo udevadm control --reload-rules && sudo udevadm trigger
```

Now plug the Mini into the Pi's **USB** port (the one in the middle, not PWR) using the OTG adapter. Check that the Pi sees it:

```bash
lsusb | grep -i elgato
```

You should see a line with `Elgato Systems GmbH`.

## 5. Set up Python

A virtual environment keeps this project's Python packages separate from the system's:

```bash
python3 -m venv ~/deck-venv
~/deck-venv/bin/pip install streamdeck pillow websockets
```

## 6. Copy the test script to the Pi

Download `hello_deck.py` from this thread. Then, **in a new terminal on your computer** (not the SSH one), from the folder you saved it in:

```bash
scp hello_deck.py pi@streamdeck.local:~/
```

## 7. Create a Home Assistant token

1. In Home Assistant, click your name at the bottom left to open your profile.
2. Open the **Security** tab and scroll to **Long-lived access tokens**.
3. Click **Create token**, name it `streamdeck`, and copy the token. It is only shown once.
4. Find the entity id of a light to test with: go to **Settings → Devices & services → Entities**, open a light, and copy its id (it looks like `light.living_room`).

## 8. Run it

Back in the SSH session:

```bash
export HA_URL="ws://homeassistant.local:8123/api/websocket"
export HA_TOKEN="paste-your-token-here"
export HA_ENTITY="light.living_room"
~/deck-venv/bin/python ~/hello_deck.py
```

You should see:

```
Using Stream Deck Mini with 6 keys
Connected to Home Assistant
light.living_room is off
```

The top-left key now shows the light's name and state (yellow when on). Press it and the light toggles; toggle the light from the HA app and the key updates too. Press **Ctrl+C** to stop.

## Troubleshooting

| What you see | What to do |
| --- | --- |
| `No Stream Deck found` | Check `lsusb`. Make sure the Mini is in the **USB** port, not PWR, and that you ran the udev commands in step 4. Unplug and replug the Mini. |
| `Permission denied` or `Could not open HID device` | Re-run step 4, then unplug and replug the Mini. |
| `Home Assistant rejected the token` | Copy the token again; make sure there are no spaces around it. |
| Connection refused / name not found | Use your HA Green's IP address instead of `homeassistant.local` in `HA_URL`. |
| `Warning: light.x not found` | Check the entity id spelling in HA. |
| The key stays grey with `?` | Home Assistant hasn't sent a state for that entity yet; check the warning above. |

Once this works, step 3 replaces this test script with the real panel software: a config file for every key, icons, pages, and starting automatically on boot.
