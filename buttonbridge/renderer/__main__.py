"""Command line: python -m renderer check|run streamdeck.yaml"""

import argparse
import asyncio
import logging
import os
import sys
from pathlib import Path

from .config import ConfigError, load_config, unused_pages


def check(path: str) -> int:
    try:
        config = load_config(path)
    except FileNotFoundError:
        print(f"Can't find {path}")
        return 1
    except ConfigError as err:
        print(f"{path} has {len(err.problems)} problem(s):")
        for problem in err.problems:
            print(f"  {problem}")
        return 1
    for page in unused_pages(config):
        print(f"Note: no start page leads to page '{page}', so it will never be shown.")
    print(
        f"{path} looks good: {len(config.panels)} panel(s), "
        f"{sum(len(p.pages) for p in config.panels.values())} page(s)."
    )
    return 0


def run(args) -> int:
    from .app import Renderer
    from .ha import HomeAssistant

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, format="%(asctime)s %(name)s: %(message)s", datefmt="%H:%M:%S")
    logging.getLogger("websockets").setLevel(logging.WARNING)
    # Inside a Home Assistant add-on the Supervisor provides a token for its API proxy.
    token = os.environ.get("HA_TOKEN") or os.environ.get("SUPERVISOR_TOKEN")
    if not token:
        token_file = Path(args.token_file).expanduser()
        if not token_file.exists():
            print(f"Needs a Home Assistant token in {token_file} (or the HA_TOKEN environment variable).")
            return 1
        token = token_file.read_text().strip()
    renderer = Renderer(Path(args.config), HomeAssistant(args.ha, token), args.bridge, args.web_port, args.ingress, not args.no_bridges)
    try:
        asyncio.run(renderer.run())
    except KeyboardInterrupt:
        pass
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="python -m renderer")
    commands = parser.add_subparsers(dest="command", required=True)
    check_cmd = commands.add_parser("check", help="check a config file for mistakes")
    check_cmd.add_argument("config", nargs="?", default="streamdeck.yaml")
    run_cmd = commands.add_parser("run", help="drive the panels")
    run_cmd.add_argument("config", nargs="?", default="streamdeck.yaml")
    run_cmd.add_argument("--ha", default=os.environ.get("HA_URL", "http://homeassistant.local:8123"), help="Home Assistant's address")
    run_cmd.add_argument("--token-file", default="~/.config/streamdeck-ha/token")
    run_cmd.add_argument("--bridge", action="append", help="a bridge's address, if mDNS doesn't find it")
    run_cmd.add_argument("--web-port", type=int, help="serve the setup page on this port")
    run_cmd.add_argument("--ingress", action="store_true", help="only accept Home Assistant's Ingress proxy")
    run_cmd.add_argument("--no-bridges", action="store_true", help="don't look for bridges (to try the setup page without taking over the panels)")
    run_cmd.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()
    if args.command == "check":
        return check(args.config)
    if args.command == "run":
        return run(args)
    return 2


if __name__ == "__main__":
    sys.exit(main())
