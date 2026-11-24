"""A small Home Assistant WebSocket API client: states, templates and actions.

https://developers.home-assistant.io/docs/api/websocket
"""

from __future__ import annotations

import asyncio
import itertools
import json
import logging
from typing import Any, Callable

import websockets

log = logging.getLogger(__name__)

RECONNECT_DELAY = 5.0  # seconds

StateListener = Callable[[str, dict | None], None]  # entity_id, new state object (None if removed)
TemplateListener = Callable[[str], None]


class AuthError(Exception):
    pass


class HomeAssistant:
    def __init__(self, url: str, token: str):
        # http://homeassistant.local:8123 -> ws://homeassistant.local:8123/api/websocket
        # http://supervisor/core (inside an add-on) -> ws://supervisor/core/websocket
        base = url.rstrip("/")
        path = "/websocket" if base.endswith("://supervisor/core") else "/api/websocket"
        self._ws_url = base.replace("https://", "wss://").replace("http://", "ws://") + path
        self._http_url = base + "/api"   # REST, for camera pictures (also through the Supervisor)
        self.time_zone: str | None = None
        self._token = token
        self._ws = None
        self._ids = itertools.count(1)
        self._pending: dict[int, asyncio.Future] = {}
        self._templates: dict[str, list[TemplateListener]] = {}  # template -> listeners
        self._template_values: dict[str, str] = {}
        self._template_ids: dict[int, str] = {}  # subscription id -> template
        self.states: dict[str, dict] = {}
        self.state_listeners: list[StateListener] = []
        self.connected = asyncio.Event()
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        self._task = asyncio.create_task(self._run(), name="home assistant")

    def state(self, entity_id: str) -> str | None:
        found = self.states.get(entity_id)
        return found["state"] if found else None

    async def call(self, domain: str, service: str, target: dict | None = None, data: dict | None = None) -> None:
        message: dict[str, Any] = {"type": "call_service", "domain": domain, "service": service}
        if target:
            message["target"] = target
        if data:
            message["service_data"] = data
        try:
            await self._request(message)
        except Exception as error:  # noqa: BLE001 - reported, not fatal
            log.warning("%s.%s failed: %s", domain, service, error)

    def now(self):
        """The time where Home Assistant is."""
        import datetime as dt

        if self.time_zone:
            try:
                from zoneinfo import ZoneInfo

                return dt.datetime.now(ZoneInfo(self.time_zone))
            except Exception:  # noqa: BLE001 - unknown zone or no tz database
                pass
        return dt.datetime.now().astimezone()

    async def camera_image(self, entity_id: str) -> bytes | None:
        """A camera's current picture, through Home Assistant."""
        import aiohttp

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(f"{self._http_url}/camera_proxy/{entity_id}", headers={"Authorization": f"Bearer {self._token}"},
                                       timeout=aiohttp.ClientTimeout(total=15)) as response:
                    if response.status == 200:
                        return await response.read()
                    log.warning("Camera %s: HTTP %s", entity_id, response.status)
        except Exception as error:  # noqa: BLE001
            log.warning("Camera %s: %s", entity_id, error)
        return None

    def watch_template(self, template: str, listener: TemplateListener) -> None:
        """Calls listener with the template's value now (if known) and whenever it changes."""
        listeners = self._templates.setdefault(template, [])
        listeners.append(listener)
        if template in self._template_values:
            listener(self._template_values[template])
        elif len(listeners) == 1 and self._ws:
            asyncio.create_task(self._subscribe_template(template))

    async def evaluate(self, template: str) -> str | None:
        """Renders a template once."""
        if template in self._template_values:
            return self._template_values[template]
        future = asyncio.get_running_loop().create_future()

        def done(value: str) -> None:
            if not future.done():
                future.set_result(value)

        self.watch_template(template, done)
        try:
            return await asyncio.wait_for(future, 10)
        except asyncio.TimeoutError:
            return None
        finally:
            listeners = self._templates.get(template, [])
            if done in listeners:
                listeners.remove(done)

    async def registries(self) -> dict:
        """Areas, floors, and which lights are in each area (directly or through their device)."""
        areas = await self._request({"type": "config/area_registry/list"})
        try:
            floors = await self._request({"type": "config/floor_registry/list"})
        except Exception:  # noqa: BLE001 - older Home Assistant
            floors = []
        entities = await self._request({"type": "config/entity_registry/list"})
        devices = await self._request({"type": "config/device_registry/list"})
        device_area = {d["id"]: d.get("area_id") for d in devices}
        lights: dict[str, list[str]] = {}
        for entity in entities:
            if entity["entity_id"].startswith("light.") and not entity.get("disabled_by"):
                area = entity.get("area_id") or device_area.get(entity.get("device_id"))
                if area:
                    lights.setdefault(area, []).append(entity["entity_id"])
        return {
            "areas": [{"id": a["area_id"], "name": a["name"], "floor": a.get("floor_id"), "lights": lights.get(a["area_id"], [])} for a in areas],
            "floors": [{"id": f["floor_id"], "name": f["name"]} for f in floors],
        }

    def template_value(self, template: str) -> str | None:
        return self._template_values.get(template)

    def clear_templates(self) -> None:
        """Forgets every template listener (on a config reload). Subscriptions lapse on reconnect."""
        for listeners in self._templates.values():
            listeners.clear()

    # --- Connection ---------------------------------------------------------

    async def _request(self, message: dict) -> Any:
        if not self._ws:
            raise ConnectionError("not connected to Home Assistant")
        message["id"] = next(self._ids)
        future = asyncio.get_running_loop().create_future()
        self._pending[message["id"]] = future
        await self._ws.send(json.dumps(message))
        result = await asyncio.wait_for(future, 15)
        return result

    async def _subscribe_template(self, template: str) -> None:
        message = {"type": "render_template", "template": template, "report_errors": True}
        message["id"] = next(self._ids)
        self._template_ids[message["id"]] = template
        future = asyncio.get_running_loop().create_future()
        self._pending[message["id"]] = future
        await self._ws.send(json.dumps(message))
        try:
            await asyncio.wait_for(future, 15)
        except Exception as error:  # noqa: BLE001
            log.warning("Template %r: %s", template, error)

    async def _run(self) -> None:
        while True:
            try:
                async with websockets.connect(self._ws_url, max_size=None, ping_interval=20) as ws:
                    await self._authenticate(ws)
                    self._ws = ws
                    reader = asyncio.create_task(self._read(ws))
                    await self._request({"type": "subscribe_events", "event_type": "state_changed"})
                    try:
                        self.time_zone = (await self._request({"type": "get_config"})).get("time_zone")
                    except Exception:  # noqa: BLE001
                        pass
                    states = await self._request({"type": "get_states"})
                    self.states = {s["entity_id"]: s for s in states}
                    log.info("Connected to Home Assistant (%d entities)", len(self.states))
                    for entity_id, state in self.states.items():
                        self._notify_state(entity_id, state)
                    self._template_ids.clear()
                    for template, listeners in self._templates.items():
                        if listeners:
                            asyncio.create_task(self._subscribe_template(template))
                    self.connected.set()
                    await reader
            except AuthError as error:
                log.error("Home Assistant refused the token: %s", error)
                await asyncio.sleep(60)
            except (OSError, asyncio.TimeoutError, websockets.WebSocketException) as error:
                log.warning("Home Assistant connection lost (%s); retrying", error)
            finally:
                self._ws = None
                self.connected.clear()
                for future in self._pending.values():
                    if not future.done():
                        future.set_exception(ConnectionError("disconnected"))
                self._pending.clear()
            await asyncio.sleep(RECONNECT_DELAY)

    async def _authenticate(self, ws) -> None:
        hello = json.loads(await ws.recv())
        if hello.get("type") != "auth_required":
            raise AuthError(f"unexpected greeting {hello}")
        await ws.send(json.dumps({"type": "auth", "access_token": self._token}))
        reply = json.loads(await ws.recv())
        if reply.get("type") != "auth_ok":
            raise AuthError(reply.get("message", reply.get("type")))

    async def _read(self, ws) -> None:
        async for raw in ws:
            message = json.loads(raw)
            kind = message.get("type")
            if kind == "result":
                future = self._pending.pop(message["id"], None)
                if future and not future.done():
                    if message.get("success"):
                        future.set_result(message.get("result"))
                    else:
                        future.set_exception(RuntimeError(message.get("error", {}).get("message", "failed")))
            elif kind == "event":
                self._handle_event(message)

    def _handle_event(self, message: dict) -> None:
        event = message["event"]
        template = self._template_ids.get(message["id"])
        if template is not None:
            if "result" in event:
                value = str(event["result"])
                self._template_values[template] = value
                for listener in list(self._templates.get(template, [])):
                    listener(value)
            elif "error" in event:
                log.warning("Template %r: %s", template, event["error"])
            return
        if event.get("event_type") == "state_changed":
            data = event["data"]
            new_state = data.get("new_state")
            if new_state:
                self.states[data["entity_id"]] = new_state
            else:
                self.states.pop(data["entity_id"], None)
            self._notify_state(data["entity_id"], new_state)

    def _notify_state(self, entity_id: str, state: dict | None) -> None:
        for listener in list(self.state_listeners):
            try:
                listener(entity_id, state)
            except Exception:  # noqa: BLE001
                log.exception("State listener failed for %s", entity_id)
