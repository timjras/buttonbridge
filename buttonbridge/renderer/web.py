"""The setup page: a small web server inside the renderer.

In the Home Assistant app it's served through Ingress, so it appears in HA's sidebar and only
HA's own proxy can reach it. On a Mac, `python -m renderer run --web-port 8099` serves it at
http://localhost:8099 for development.

The page (web/index.html) keeps its own model of the panels and sends the config back as JSON.
This module checks it with the same rules as `python -m renderer check`, writes streamdeck.yaml,
and runs Home Assistant actions for the page's "Try it" mode.
"""

from __future__ import annotations

import ipaddress
import json
import logging
import re
import time
from pathlib import Path
from typing import TYPE_CHECKING

import yaml
from aiohttp import web
from pydantic import ValidationError

from . import config as cfg
from . import dials
from . import draw
from .panel import perform

if TYPE_CHECKING:
    from .app import Renderer

log = logging.getLogger(__name__)

HERE = Path(__file__).parent / "web"
INGRESS_PROXY = ipaddress.ip_address("172.30.32.2")  # Home Assistant's Ingress proxy
IMAGE_TYPES = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
MAX_UPLOAD = 8 * 1024 * 1024
SIZES_BY_KEYS = {6: "mini", 15: "module15", 32: "module32"}
TURNABLE = ("light", "media_player", "cover", "climate", "fan", "input_number", "number", "timer")


# --- Writing YAML ------------------------------------------------------------


class _Dumper(yaml.SafeDumper):
    pass


def _represent_dict(dumper: yaml.SafeDumper, data: dict):
    # Short sections of plain values read best on one line: { icon: mdi:x, background: amber }
    flow = 0 < len(data) <= 4 and all(not isinstance(v, (dict, list)) for v in data.values())
    return dumper.represent_mapping("tag:yaml.org,2002:map", data, flow_style=flow)


def _represent_list(dumper: yaml.SafeDumper, data: list):
    flow = 0 < len(data) <= 4 and all(isinstance(v, str) for v in data)
    return dumper.represent_sequence("tag:yaml.org,2002:seq", data, flow_style=flow)


_Dumper.add_representer(dict, _represent_dict)
_Dumper.add_representer(list, _represent_list)

HEADER = "# ButtonBridge panels. Written by the setup page; you can also edit it by hand.\n# The format is described in docs/CONFIG.md.\n\n"


def to_yaml(data: dict) -> str:
    return HEADER + yaml.dump(data, Dumper=_Dumper, sort_keys=False, allow_unicode=True, width=100, indent=2)


def check(data: dict) -> tuple[str, list[str]]:
    """The YAML that saving `data` would write, and its problems (empty if it's good)."""
    text = to_yaml(data)
    try:
        cfg.parse_config(text)
    except cfg.ConfigError as error:
        return text, error.problems
    return text, []


# --- The server --------------------------------------------------------------


class SetupPage:
    def __init__(self, renderer: "Renderer", port: int, ingress_only: bool):
        self.renderer = renderer
        self.port = port
        self.ingress_only = ingress_only
        self._registries: tuple[float, dict] | None = None

    async def start(self) -> None:
        app = web.Application(middlewares=[self._guard], client_max_size=MAX_UPLOAD)
        app.router.add_get("/", self.index)
        app.router.add_get("/api/state", self.state)
        app.router.add_get("/api/entities", self.entities)
        app.router.add_post("/api/preview", self.preview)
        app.router.add_post("/api/save", self.save)
        app.router.add_post("/api/press", self.press)
        app.router.add_post("/api/turn", self.turn)
        app.router.add_post("/api/upload", self.upload)
        app.router.add_get("/api/icons", self.icons)
        app.router.add_get("/api/picture", self.picture)
        app.router.add_get("/api/mdi.ttf", self.icon_font)
        runner = web.AppRunner(app, access_log=None)
        await runner.setup()
        await web.TCPSite(runner, "0.0.0.0", self.port).start()
        log.info("Setup page on port %d%s", self.port, " (Home Assistant sidebar)" if self.ingress_only else "")

    @web.middleware
    async def _guard(self, request: web.Request, handler):
        if self.ingress_only:
            try:
                allowed = ipaddress.ip_address(request.remote or "") == INGRESS_PROXY
            except ValueError:
                allowed = False
            if not allowed:
                raise web.HTTPForbidden(text="Open the setup page from Home Assistant's sidebar.")
        return await handler(request)

    # --- Page and data ---------------------------------------------------

    async def index(self, request: web.Request) -> web.Response:
        return web.FileResponse(HERE / "index.html", headers={"Cache-Control": "no-cache"})

    async def picture(self, request: web.Request) -> web.StreamResponse:
        """A page picture from the config folder, for the setup page's preview."""
        folder = self.renderer.config_path.parent.resolve()
        file = request.query.get("file", "").removeprefix("/config/")
        path = (folder / file).resolve()
        if folder not in path.parents or path.suffix.lower() not in IMAGE_TYPES or not path.is_file():
            raise web.HTTPNotFound()
        return web.FileResponse(path)

    async def icons(self, request: web.Request) -> web.Response:
        return web.json_response({name: ord(glyph) for name, glyph in draw._mdi_codepoints().items()},
                                 headers={"Cache-Control": "max-age=86400"})

    async def icon_font(self, request: web.Request) -> web.Response:
        return web.FileResponse(draw._download("fonts/materialdesignicons-webfont.ttf"),
                                headers={"Cache-Control": "max-age=86400"})

    async def _area_data(self) -> dict:
        if self._registries and time.monotonic() - self._registries[0] < 60:
            return self._registries[1]
        try:
            data = await self.renderer.ha.registries()
        except Exception as error:  # noqa: BLE001 - the page still works without rooms
            log.warning("Couldn't read areas and floors: %s", error)
            data = {"areas": [], "floors": []}
        self._registries = (time.monotonic(), data)
        return data

    def _entities(self) -> dict:
        found = {}
        for entity_id, state in self.renderer.ha.states.items():
            found[entity_id] = {"state": state["state"], "name": state.get("attributes", {}).get("friendly_name", "")}
            if entity_id.split(".")[0] in TURNABLE:   # how a dial shows it, for the preview
                reading = dials.read(entity_id, state)
                found[entity_id]["dial"] = {"text": reading.text, "level": reading.level(), "on": reading.on}
        return found

    def _bridges(self) -> list[dict]:
        configured = {p.id: name for name, p in (self.renderer.config.panels if self.renderer.config else {}).items()}
        found = []
        for bridge in self.renderer.bridges.values():
            deck = bridge.deck
            found.append({
                "id": bridge.id,
                "host": bridge.host,
                "connected": bridge.connected,
                "panel": configured.get(bridge.id),
                "deck": {"model": deck.model, "keys": deck.keys, "rows": deck.rows, "cols": deck.cols, "dials": deck.dials} if deck else None,
                "size": ("plus" if deck.dials else SIZES_BY_KEYS.get(deck.keys)) if deck else None,
            })
        return found

    def _sun(self) -> dict:
        sun = self.renderer.ha.states.get("sun.sun", {}).get("attributes", {})
        return {"sunrise": sun.get("next_rising"), "sunset": sun.get("next_setting")}

    async def state(self, request: web.Request) -> web.Response:
        path = self.renderer.config_path
        text = path.read_text(encoding="utf-8") if path.exists() else ""
        try:
            raw = yaml.safe_load(text) or {}
        except yaml.YAMLError:
            raw = {}
        problems: list[str] = []
        if text:
            try:
                cfg.parse_config(text)
            except cfg.ConfigError as error:
                problems = error.problems
        return web.json_response({
            "file": str(path),
            "config": _jsonable(raw),
            "problems": problems,
            "entities": self._entities(),
            **(await self._area_data()),
            "bridges": self._bridges(),
            "sun": self._sun(),
        })

    async def entities(self, request: web.Request) -> web.Response:
        return web.json_response({"entities": self._entities(), "bridges": self._bridges()})

    # --- Saving ----------------------------------------------------------

    async def preview(self, request: web.Request) -> web.Response:
        data = await request.json()
        text, problems = check(data.get("config") or {})
        return web.json_response({"yaml": text, "problems": problems})

    async def save(self, request: web.Request) -> web.Response:
        data = await request.json()
        text, problems = check(data.get("config") or {})
        if problems:
            return web.json_response({"ok": False, "yaml": text, "problems": problems})
        path = self.renderer.config_path
        if path.exists():
            path.with_suffix(path.suffix + ".bak").write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(text, encoding="utf-8")
        temporary.replace(path)
        log.info("Setup page saved %s", path)
        self.renderer.reload_soon()
        return web.json_response({"ok": True, "yaml": text, "problems": []})

    # --- Try it ----------------------------------------------------------

    async def press(self, request: web.Request) -> web.Response:
        """Runs one button's Home Assistant action, as the panel would. Page changes stay in the page."""
        data = await request.json()
        if "dial" in data:
            return await self._press_dial(data)
        try:
            button = cfg.Button.model_validate({"key": 1, **(data.get("button") or {})})
        except ValidationError as error:
            return web.json_response({"ok": False, "error": str(error)}, status=400)
        action = button.tap
        if action is None:
            domain = (button.entity or "").split(".")[0]
            action = "none" if domain in ("sensor", "binary_sensor", "weather", "sun", "camera") or not (button.entity or button.lights) else "toggle"
        if action in ("none", "back") or isinstance(action, cfg.GoTo):
            return web.json_response({"ok": True, "ran": False})
        await perform(self.renderer.ha, action, button)
        return web.json_response({"ok": True, "ran": True})

    async def _press_dial(self, data: dict) -> web.Response:
        try:
            dial = cfg.Dial.model_validate({"dial": 1, **(data.get("dial") or {})})
        except ValidationError as error:
            return web.json_response({"ok": False, "error": str(error)}, status=400)
        action = dial.press if dial.press is not None else dials.default_press(dial.entity)
        if isinstance(action, str) and "." in action:
            action = cfg.CallAction(action=action)
        if action in ("none", "back") or isinstance(action, cfg.GoTo):
            return web.json_response({"ok": True, "ran": False})
        await perform(self.renderer.ha, action, dial)
        return web.json_response({"ok": True, "ran": True})

    async def turn(self, request: web.Request) -> web.Response:
        """Turns a dial one or more clicks, as the panel would, from the entity's state now."""
        data = await request.json()
        try:
            dial = cfg.Dial.model_validate({"dial": 1, **(data.get("dial") or {})})
            detents = int(data.get("detents", 1))
        except (ValidationError, TypeError, ValueError) as error:
            return web.json_response({"ok": False, "error": str(error)}, status=400)
        reading = dials.read(dial.entity, self.renderer.ha.states.get(dial.entity or ""), dial.step)
        if reading.value is None:
            return web.json_response({"ok": True, "ran": False})
        value = reading.after(reading.value, detents)
        domain, service, extra = dials.call_for(dial.entity, value)
        await self.renderer.ha.call(domain, service, {"entity_id": dial.entity}, extra)
        return web.json_response({"ok": True, "ran": True, "text": reading.show(value) if reading.show else ""})

    async def upload(self, request: web.Request) -> web.Response:
        """Saves a picture for a page into streamdeck/images next to the config file."""
        reader = await request.multipart()
        part = await reader.next()
        if part is None or part.name != "file" or not part.filename:
            raise web.HTTPBadRequest(text="No file")
        name = re.sub(r"[^\w.-]+", "_", Path(part.filename).name)
        if Path(name).suffix.lower() not in IMAGE_TYPES:
            raise web.HTTPBadRequest(text="Pictures only: PNG, JPEG, GIF or WebP")
        folder = self.renderer.config_path.parent / "streamdeck" / "images"
        folder.mkdir(parents=True, exist_ok=True)
        size = 0
        with open(folder / name, "wb") as out:
            while chunk := await part.read_chunk():
                size += len(chunk)
                if size > MAX_UPLOAD:
                    raise web.HTTPRequestEntityTooLarge(max_size=MAX_UPLOAD, actual_size=size)
                out.write(chunk)
        # Relative to Home Assistant's config folder, where the panels look for it.
        return web.json_response({"file": f"streamdeck/images/{name}"})


def _jsonable(value):
    """YAML can hold things JSON can't (dates, non-string keys); turn them into text. A bare
    on/off key reads as true/false in YAML, but means the HA state "on"/"off"."""
    if isinstance(value, dict):
        return {("on" if k is True else "off" if k is False else str(k)): _jsonable(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_jsonable(v) for v in value]
    return json.loads(json.dumps(value, default=str))
