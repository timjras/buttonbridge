#!/usr/bin/env python3
"""Saves a Wi-Fi network on a bridge plugged into this computer by USB, then shows its address.

    python3 tools/set_wifi.py [serial port]
    python3 tools/set_wifi.py --use ethernet|wifi|auto [serial port]

The password is typed here and goes only to the board over the cable.

On a board with Ethernet too (the ESP32-P4 board), --use picks between them without touching
the saved network: "auto" (the default) uses Ethernet when the cable has a link at startup and
Wi-Fi otherwise, "ethernet" and "wifi" use only that one.
"""
import argparse
import getpass
import glob
import json
import sys
import time

try:
    import serial
except ImportError:
    sys.exit("Needs pyserial: python3 -m pip install pyserial")


def find_port():
    ports = sorted(glob.glob("/dev/cu.usbmodem*") + glob.glob("/dev/cu.usbserial*") +
                   glob.glob("/dev/cu.wchusbserial*") + glob.glob("/dev/ttyACM*") + glob.glob("/dev/ttyUSB*"))
    if not ports:
        sys.exit("No board found. Plug its USB port into this computer.")
    return ports[0]


heard = ""   # what came back from the port, for the error message


def command(port, payload, wait):
    global heard
    port.reset_input_buffer()
    port.write((json.dumps(payload) + "\n").encode())
    end, text = time.time() + wait, ""
    while time.time() < end:
        text += port.read(256).decode(errors="replace")
        lines = text.split("\n")
        for line in lines[:-1]:   # the last one may still be arriving
            if line.strip().startswith("@@"):
                try:
                    return json.loads(line.strip()[2:])
                except ValueError:
                    pass   # a log line landed in the middle of it; the caller asks again
        if lines[-1].lstrip().startswith("@@"):
            end = max(end, time.time() + 0.5)   # a long reply (the image cache) is mid-line
    heard += text
    return None


def find_bridge(port):
    # Opening the port can restart the board, which then misses the first question while it
    # boots, so keep asking for a while.
    end = time.time() + 15
    while time.time() < end:
        info = command(port, {"cmd": "info"}, 1.5)
        if info:
            return info
    return None


def open_port(name, timeout):
    # DTR and RTS stay low: on the P4 board's USB-C serial chip they reset the board.
    port = serial.Serial(timeout=timeout)
    port.port, port.baudrate, port.dtr, port.rts = name, 115200, False, False
    port.open()
    return port


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("port", nargs="?", help="serial port (found automatically if left out)")
    parser.add_argument("--use", choices=["auto", "ethernet", "wifi"],
                        help="switch between Ethernet and Wi-Fi instead of saving a network")
    args = parser.parse_args()

    name = args.port or find_port()
    port = open_port(name, 0.2)
    info = find_bridge(port)
    if not info:
        last = "\n".join(heard.strip().splitlines()[-10:]) or "(nothing at all)"
        sys.exit(f"No reply from a bridge on {name}. Is the bridge firmware on it?\n"
                 f"Last thing it printed:\n{last}")
    print(f"Found {info['hostname']} (firmware {info['firmware']}) on {name}")
    has = info.get("network", {}).get("has", ["wifi"])

    if args.use:
        if "ethernet" not in has or "wifi" not in has:
            sys.exit(f"This board only has {' and '.join(has)}; there's nothing to choose.")
        reply = command(port, {"cmd": "network", "use": args.use}, 3)
        if not reply or not reply.get("ok"):
            sys.exit(f"The board didn't save it: {reply}")
        print(f"Set to {args.use}. The board is restarting...")
    else:
        if "wifi" not in has:
            sys.exit("This board's firmware has no Wi-Fi. Flash the current firmware first.")
        ssid = input("Wi-Fi network name (2.4 GHz): ").strip()
        password = getpass.getpass("Wi-Fi password (hidden): ")
        reply = command(port, {"cmd": "wifi", "ssid": ssid, "password": password}, 3)
        if not reply or not reply.get("ok"):
            sys.exit(f"The board didn't save it: {reply}")
        use = info.get("network", {}).get("use", "wifi")
        if "ethernet" in has and use == "auto":
            print("Saved. The board uses it when no Ethernet cable has a link at startup.")
        elif use == "ethernet":
            print("Saved, but the board is set to Ethernet only. Run with --use auto or --use wifi.")
        print("The board is restarting...")
    port.close()

    time.sleep(2)
    for _ in range(20):
        try:
            port = open_port(name, 0.5)
            break
        except serial.SerialException:
            time.sleep(0.5)
    end, text = time.time() + 30, ""
    while time.time() < end:
        text += port.read(256).decode(errors="replace")
        for line in text.splitlines():
            if "On the network" in line:
                print(line.split("network:", 1)[-1].strip())
                return
            if "Wi-Fi disconnected" in line:
                print(line.split("network:", 1)[-1].strip())
                text = ""
    print("Not on the network yet. For Wi-Fi, check the name and password, and that it's a 2.4 GHz network.")


if __name__ == "__main__":
    main()
