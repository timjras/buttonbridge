# Setting up ButtonBridge

Each panel is an Elgato Stream Deck plugged into a small ESP32 board, the **bridge**, which
connects it to your network. A Home Assistant app draws the keys, runs what they do, and adds a
**ButtonBridge** page to Home Assistant's sidebar where you set the panels up.

```
 Home Assistant  ──  ButtonBridge app (draws the keys, runs the actions)
        │  your network
        ▼
 ESP32 bridge  ──USB──  Stream Deck        (one bridge per panel)
```

Setting up takes about half an hour for the first panel:

1. [Get the parts](#1-get-the-parts)
2. [Put the firmware on the bridge](#2-put-the-firmware-on-the-bridge)
3. [Connect the panel](#3-connect-the-panel)
4. [Install the Home Assistant app](#4-install-the-home-assistant-app)
5. [Set up the panel](#5-set-up-the-panel)

[Updating](#updating) and [When something doesn't work](#when-something-doesnt-work) are at the end.

## 1. Get the parts

### Recommended: one cable to the wall (PoE)

| Part | Notes |
|---|---|
| **Waveshare ESP32-P4-WIFI6-POE-ETH** | The bridge. About $25. Its USB-A port runs at USB 2.0 high speed, so a key changes in a few milliseconds. |
| A **Stream Deck** | Mini or Stream Deck + (tested), or a 6-key Module, 15-key (MK.2, Original or Module), or XL or 32-key Module. On a Stream Deck +, the dials and touch strip work too. |
| The deck's **USB cable** | USB-C (or micro-USB) at the deck, USB-A at the board. A short one fits a wall box best. |
| A **PoE network port** | A PoE switch or injector (802.3af). The one network cable powers the board and the deck. |

No PoE? Plug the board's USB-C port into any 5 V USB charger (2 A or more) and use an ordinary
network cable, or skip the cable and use the board's **Wi-Fi** (2.4 GHz). Wi-Fi is slower to
respond than a cable, so use Ethernet where you can.

### Alternative: an ESP32-S3 over Wi-Fi

An **ESP32-S3 dev kit with 16 MB flash and 8 MB PSRAM** (N16R8) also works, over 2.4 GHz Wi-Fi,
with a passive USB-C OTG adapter with a power input between its **USB** port and the deck. Its USB
is full speed only, though, so a Stream Deck Mini takes about a third of a second to redraw each
key. Use it for panels that rarely change.

## 2. Put the firmware on the bridge

You need a computer with **Chrome or Edge**.

1. Plug the board into the computer with a USB cable that carries data:
   - **ESP32-P4 board**: its USB-C port.
   - **ESP32-S3 board**: its port marked **USB** (not COM/UART).
2. Open the installer at <https://timjras.github.io/buttonbridge/> and press **Install**.
3. Pick the board's serial port in the list (on a Mac it's called something like `usbmodem…` or
   `USB Single Serial`), confirm, and wait a minute or two. Then unplug the board.

If the board isn't found, hold its **BOOT** button while you plug it in, then try again.

<details>
<summary>Flashing by hand instead</summary>

Download the files from the latest release on the repository's **Releases** page. Each board has a
`-full.bin` (for the first install) and an `-update.bin` (for updates over the network):

| Board | File |
|---|---|
| ESP32-P4, chip revision v3.x (current boards) | `bridge-esp32p4-<version>-full.bin` |
| ESP32-P4, chip revision v1.x (early boards) | `bridge-esp32p4-rev1-<version>-full.bin` |
| ESP32-S3 | `bridge-esp32s3-<version>-full.bin` |

The flasher shows the chip revision when it connects (for example "ESP32-P4 (revision v1.0)").

With Espressif's web flasher (<https://espressif.github.io/esptool-js/>): baud rate **921600**,
**Connect**, set **Flash Address** to **0x0**, choose the file, and press **Program**.

Or with Python 3: `pip install esptool`, then (replace the port and the file):

```
esptool --port /dev/cu.usbmodem1234 --baud 921600 write_flash 0x0 bridge-esp32p4-<version>-full.bin
```
</details>

### Joining Wi-Fi (ESP32-S3, or an ESP32-P4 without a network cable)

Skip this for an ESP32-P4 on Ethernet.

You need Python 3 and a copy of this repository (on GitHub, **Code > Download ZIP**, then unzip
it). Install the one library the script uses:

```
pip3 install pyserial
```

1. Leave the board plugged into the computer by the same port you flashed it through (the P4's
   **USB-C** port, the S3's **USB** port). For a P4, unplug its network cable first.
2. In a terminal, go to the unzipped folder and run:

   ```
   python3 firmware/bridge/tools/set_wifi.py
   ```

3. It finds the board (`Found sdbridge-xxxxxx …`), then asks:
   - **Wi-Fi network name (2.4 GHz):** type the network's name (SSID) exactly, capitals and
     spaces included, and press Return. The board can't join a 5 GHz-only network.
   - **Wi-Fi password (hidden):** type the password and press Return. Nothing appears while you
     type. It goes only to the board, over the cable.
4. The board restarts and the script shows its address, for example
   `On the network (wifi) as 192.168.1.211 (sdbridge-xxxxxx.local)`.

If it can't find the board, or picks the wrong port, give the port yourself (the same name the
installer showed), for example `python3 firmware/bridge/tools/set_wifi.py /dev/cu.usbmodem1234`.
If it says the board didn't join, run it again and check the name and password. To change
networks later, plug the board back into the computer and run the script again.

An ESP32-P4 with a network saved still uses **Ethernet** whenever a cable has a link when it
starts, and Wi-Fi otherwise. To choose for good, run the script with `--use wifi` or
`--use ethernet` (`--use auto` goes back to the default).

## 3. Connect the panel

- **ESP32-P4**: plug the Stream Deck into the board's **USB-A** port, then the network cable into
  its **Ethernet** port. With PoE, that's all; otherwise also plug a charger into its USB-C port.
  On Wi-Fi, there's no network cable: just the deck and a charger in the USB-C port.
- **ESP32-S3**: plug the Stream Deck and a 5 V charger into the OTG adapter, and the adapter into
  the board's **USB** port.

The deck lights up. Within a few seconds the bridge is on your network as `sdbridge-xxxxxx.local`,
where `xxxxxx` is the end of its hardware address. Its keys stay as they are until the app is running.

## 4. Install the Home Assistant app

The app runs on the Home Assistant machine itself (Home Assistant OS, such as a Home Assistant
Green or Yellow).

1. Press this button and confirm, or go to **Settings > Apps > App store**, open the **⋮** menu at
   the top right, choose **Repositories**, and add `https://github.com/timjras/buttonbridge`.

   [![Add repository to Home Assistant](https://my.home-assistant.io/badges/supervisor_add_addon_repository.svg)](https://my.home-assistant.io/redirect/supervisor_add_addon_repository/?repository_url=https%3A%2F%2Fgithub.com%2Ftimjras%2Fbuttonbridge)
2. **ButtonBridge** appears in the app store. Open it and press **Install**. Building it takes a
   few minutes the first time.
3. Turn on **Show in sidebar**, then press **Start**.

Each bridge on the network now shows **New panel** and its ID, like `sd-a1b2c3`, on its keys.

## 5. Set up the panel

1. Open **ButtonBridge** in Home Assistant's sidebar.
2. On the left, the panel appears under **New panel found** with the ID it's showing. Give it a
   name, check its size, and press **Add panel**.
3. Click a key to set it up: what it does (control a light or other device, a whole room or floor,
   run a scene, open another page, show a sensor, or any Home Assistant action), its label, and its
   icon and colours for each state.
4. Press **Save**. The panel shows your keys within a couple of seconds.

**Try it** at the top lets you press keys on the page as if it were the panel. It controls your real
devices.

Pages can also show a clock or a picture across their keys, have their own brightness, and the
panel can dim or change page on a schedule (**Panel > Brightness** and **Sleep Mode**).

Everything is saved in `streamdeck.yaml` in Home Assistant's config folder, which Home Assistant's
backups include. You can also edit it by hand; [CONFIG.md](CONFIG.md) describes the format, and
[examples/streamdeck.yaml](../examples/streamdeck.yaml) is a full example.

## Updating

**The app**: Home Assistant shows an update for **ButtonBridge** when there's a new version.
Press **Update**.

**A bridge**: send it the new `-update.bin` file over the network, from any computer on the same network:

```
curl --data-binary @bridge-esp32p4-<version>-update.bin http://sdbridge-xxxxxx.local/update
```

It installs the update and restarts. If the new firmware can't get back on the network, it goes
back to the previous version by itself. You can also repeat step 2 with the `-full.bin` file.

## When something doesn't work

| What you see | What to check |
|---|---|
| The panel's keys never change | Is the app started? Its **Log** tab (Settings > Apps > ButtonBridge) says which bridges it found. |
| The app doesn't find a bridge | Open `http://sdbridge-xxxxxx.local/info` in a browser. No answer: check its network cable or Wi-Fi. |
| The bridge answers but `"deck": null` | The deck isn't detected. Check its cable carries data. `"usb"` in the same page shows whether any USB device was seen. |
| A key does nothing | Check the key's entity in the setup page: an unknown entity is shown in red. |
| The setup page won't save | It lists what's wrong. The file on disk isn't changed until it's fixed. |

A bridge keeps its recent log at `http://sdbridge-xxxxxx.local/log`.
