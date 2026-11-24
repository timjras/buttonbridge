"""One panel: which page it shows, how each key looks, and what pressing a key does.

Not built yet (they need more than this first version): page images (clock, picture,
camera), brightness schedules and Sleep Mode. Pages with an image show their buttons on black.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import io
import logging
import re
import time
from pathlib import Path

from PIL import Image

from . import config as cfg
from . import dials
from .dials import format_left, timer_left  # noqa: F401 - used here and by tests
from .bridges import Bridge, Deck
from . import images
from .draw import DialLook, KeyLook, render_dial, render_key, to_native
from .ha import HomeAssistant

log = logging.getLogger(__name__)

HOLD_TIME = 0.5  # seconds a key is held before it counts as a long press
DOUBLE_TIME = 0.3  # seconds after a release that a second press counts as a double press
TICK = 2.0  # seconds between checks of the clock, schedules and Sleep Mode
COUNTDOWN_TICK = 0.25  # seconds between checks of running timers' countdowns
CONFIRM_TIME = 3.0  # seconds a confirm button waits for its second press
CONFIRM_BORDER = "white"
DIM_STEP = 10  # percent of full brightness per step while a dimming key is held
DIM_EVERY = 0.35  # seconds between steps
DIM_LOWEST = 1  # dimming stops here rather than turning the light off

TURN_SETTLE = 0.15  # seconds between updates sent to Home Assistant while a dial turns
TURN_HOLD = 2.0  # seconds the strip shows the value a dial was turned to, while HA catches up

# A dial's icon and bar colour when the config doesn't give them.
DIAL_DEFAULTS = {
    "light": ("mdi:lightbulb", "amber"),
    "media_player": ("mdi:volume-high", "blue"),
    "cover": ("mdi:window-shutter", "green"),
    "climate": ("mdi:thermostat", "red"),
    "fan": ("mdi:fan", "blue"),
    "input_number": ("mdi:tune-variant", "purple"),
    "number": ("mdi:tune-variant", "purple"),
    "timer": ("mdi:timer-outline", "red"),
}


def _lights_template(lights: cfg.Lights) -> str:
    """A template that's True while any light in the area or floor is on."""
    if lights.area:
        entities = f"area_entities({lights.area!r})"
    else:
        entities = f"(floor_areas({lights.floor!r}) | map('area_entities') | sum(start=[]))"
    return "{{ (" + entities + " | select('match', 'light\\\\.') | select('is_state', 'on') | list | count) > 0 }}"


def _lights_target(lights: cfg.Lights) -> dict:
    return {"area_id": lights.area} if lights.area else {"floor_id": lights.floor}


def _lights_list_template(lights: cfg.Lights) -> str:
    """A template listing every light in the area or floor."""
    if lights.area:
        entities = f"area_entities({lights.area!r})"
    else:
        entities = f"(floor_areas({lights.floor!r}) | map('area_entities') | sum(start=[]))"
    return "{{ " + entities + " | select('match', 'light\\\\.') | list | join(',') }}"


def dim_steps(levels: dict[str, int | None], direction: int, step: int = DIM_STEP) -> dict[str, int]:
    """One step of dimming: each light's new brightness in percent. `levels` is each light's
    brightness now (None when off). Dimming leaves lights that are off alone and stops at
    DIM_LOWEST; brightening starts the lights that are off from 0, but only when none are on."""
    any_on = any(level is not None for level in levels.values())
    new = {}
    for entity, level in levels.items():
        if level is None and (direction < 0 or any_on):
            continue
        target = max(DIM_LOWEST, min(100, (level or 0) + direction * step))
        if target != level:
            new[entity] = target
    return new


async def perform(ha: HomeAssistant, action, button: cfg.Button | cfg.Dial | None, state: str | None = None) -> None:
    """Runs a button's or dial's Home Assistant action (anything but page changes). `state` is the
    button's current state ("any_on"/"all_off" for lights:), if known; a lights: toggle asks HA otherwise."""
    if isinstance(action, cfg.CallAction):
        domain, service = action.action.split(".", 1)
        await ha.call(domain, service, action.target or ({"entity_id": button.entity} if button and button.entity else None), action.data)
    elif action in cfg.DIM_ACTIONS:
        return  # these only run while a key is held; see PanelController._dim
    elif action in ("toggle", "turn_on", "turn_off") and button:
        if getattr(button, "lights", None):
            if action == "toggle" and state is None:
                value = await ha.evaluate(_lights_template(button.lights))
                state = "any_on" if (value or "").strip().lower() == "true" else "all_off"
            service = action if action != "toggle" else ("turn_off" if state == "any_on" else "turn_on")
            await ha.call("light", service, _lights_target(button.lights))
        elif button.entity:
            await _entity_action(ha, button.entity, action)


async def _entity_action(ha: HomeAssistant, entity_id: str, action: str) -> None:
    domain = entity_id.split(".")[0]
    target = {"entity_id": entity_id}
    if domain in ("scene", "script") or (domain == "button" and action != "turn_off"):
        await ha.call(domain, "press" if domain == "button" else "turn_on", target)
    elif domain == "lock":
        locked = ha.state(entity_id) == "locked"
        service = {"turn_on": "lock", "turn_off": "unlock"}.get(action, "unlock" if locked else "lock")
        await ha.call("lock", service, target)
    elif domain == "cover":
        service = {"turn_on": "open_cover", "turn_off": "close_cover"}.get(action, "toggle")
        await ha.call("cover", service, target)
    elif domain == "timer":
        # Toggle starts an idle timer, pauses a running one and resumes a paused one; turn_off cancels.
        running = ha.state(entity_id) == "active"
        service = {"turn_on": "start", "turn_off": "cancel"}.get(action, "pause" if running else "start")
        length = dials.TIMER_LENGTHS.get(entity_id)
        if service == "start" and length and ha.state(entity_id) == "idle":
            await ha.call("timer", "start", target, {"duration": round(length)})
        else:
            await ha.call("timer", service, target)
    else:
        await ha.call("homeassistant", action, target)


class PanelController:
    def __init__(self, name: str, panel: cfg.Panel, styles: dict[str, cfg.Style], bridge: Bridge, ha: HomeAssistant,
                 config_dir: Path | None = None):
        self.name = name
        self.panel = panel
        self.styles = styles
        self.bridge = bridge
        self.ha = ha
        self.page_name = panel.start_page
        self.history: list[str] = []
        self._pressed_at: dict[int, float] = {}
        self._hold_tasks: dict[int, asyncio.Task] = {}
        self._held: set[int] = set()
        self._tap_later: dict[int, asyncio.Task] = {}  # double-press keys waiting to see if a second press comes
        self._second: set[int] = set()  # keys whose current press is the second of a double press
        self._confirming: dict[int, asyncio.Task] = {}
        self._confirm_for: dict[int, str] = {}  # which press (tap, hold, double) a confirm is waiting on
        self._dimmed_up: dict[int, bool] = {}  # dim_brighten keys: whether the last hold brightened
        self._templates: set[str] = set()
        self._closed = False
        self.config_dir = config_dir or Path(".")
        self.asleep = False
        self._wake_page: str | None = None
        self._swallow: set[int] = set()  # keys whose press woke the panel: their release does nothing
        self._last_activity = time.monotonic()
        self._level: int | None = None  # the brightness last sent
        self._tiles: tuple[tuple, dict] | None = None  # (what they show, key -> picture) for the page's image
        self._pictures: dict[str, tuple[float, Image.Image]] = {}
        self._tick_task: asyncio.Task | None = None
        self._countdown_task: asyncio.Task | None = None
        self._drawn_countdowns: dict[int, str] = {}  # key -> the countdown it shows now
        self._drawn_dials: dict[int, str] = {}  # dial -> the timer countdown its strip shows now
        self._turned: dict[int, tuple[float, float]] = {}  # dial -> (value turned to, when)
        self._turn_tasks: dict[int, asyncio.Task] = {}

    # --- Lifecycle ----------------------------------------------------------

    def attach(self) -> None:
        self.bridge.on_key = self._on_key
        self.bridge.on_deck = self._on_deck
        self.bridge.on_dial = self._on_dial
        self.bridge.on_touch = self._on_touch
        self.ha.state_listeners.append(self._on_state)
        self._watch_templates()
        if self.bridge.deck:
            asyncio.create_task(self._refresh())
        self._tick_task = asyncio.create_task(self._ticker(), name=f"panel {self.name}")
        self._countdown_task = asyncio.create_task(self._count_down(), name=f"countdowns {self.name}")

    async def _refresh(self) -> None:
        """Brightness and every key, for a new or reloaded config."""
        await self.apply_brightness(force=True)
        await self.draw_all()

    def detach(self) -> None:
        self._closed = True
        if self._on_state in self.ha.state_listeners:
            self.ha.state_listeners.remove(self._on_state)
        for task in list(self._hold_tasks.values()) + list(self._confirming.values()) + list(self._turn_tasks.values()) + list(self._tap_later.values()) + [self._tick_task, self._countdown_task]:
            if task:
                task.cancel()

    @property
    def page(self) -> cfg.Page:
        return self.panel.pages[self.page_name]

    def _button(self, key: int, page: str | None = None) -> cfg.Button | None:
        for button in (self.panel.pages[page] if page else self.page).buttons:
            if button.key == key:
                return button
        return None

    def _dial(self, number: int) -> cfg.Dial | None:
        for dial in self.page.dials:
            if dial.dial == number:
                return dial
        return None

    # --- Looks --------------------------------------------------------------

    def _watch_templates(self) -> None:
        """Subscribes to every template on every page, so switching pages draws at once."""
        for page in self.panel.pages.values():
            for button in [*page.buttons, *page.dials]:
                for template in self._templates_of(button):
                    if template not in self._templates:
                        self._templates.add(template)
                        self.ha.watch_template(template, lambda _value, t=template: self._template_changed(t))

    @staticmethod
    def _templates_of(button: cfg.Button | cfg.Dial) -> list[str]:
        found = []
        if button.text and ("{{" in button.text or "{%" in button.text):
            found.append(button.text)
        if getattr(button, "lights", None):
            found.append(_lights_template(button.lights))
        return found

    def _state_of(self, button: cfg.Button) -> str | None:
        if button.lights:
            value = self.ha.template_value(_lights_template(button.lights))
            return None if value is None else ("any_on" if value.strip().lower() == "true" else "all_off")
        if button.entity:
            return self.ha.state(button.entity)
        return None

    def look_for(self, button: cfg.Button) -> KeyLook:
        layers: list[cfg.Look | cfg.Style] = []
        if button.style and button.style in self.styles:
            layers.append(self.styles[button.style])
        layers.append(button)
        state = self._state_of(button)
        if state is not None and state in button.when:
            layers.append(button.when[state])

        merged: dict = {"icon": None, "background": None, "icon_color": None}
        text, text_position = "", "bottom"
        for layer in layers:
            for field in merged:
                value = getattr(layer, field, None)
                if value is not None:
                    merged[field] = value
            if isinstance(layer, cfg.Style):
                if layer.text is not None:
                    text = layer.text
                if "text_position" in layer.model_fields_set:
                    text_position = layer.text_position
        if text and ("{{" in text or "{%" in text):
            text = self.ha.template_value(text) or ""
        countdown, progress = "", None
        if button.countdown and button.entity and button.entity.startswith("timer."):
            left = timer_left(self.ha.states.get(button.entity))
            if left is not None:
                countdown = format_left(left[0])
                progress = left[0] / left[1] if left[1] else None
        if state == "unavailable":
            merged["icon_color"] = "grey"

        background = merged["background"] or "black"
        light_background = background in ("amber", "white") or (background.startswith("#") and _is_light(background))
        return KeyLook(
            icon=merged["icon"],
            background=background,
            icon_color=merged["icon_color"] or ("dark" if light_background else "white"),
            text=text,
            text_position=text_position,
            text_color="dark" if light_background else "white",
            border=CONFIRM_BORDER if button.key in self._confirming else None,
            long_press=button.hold not in (None, "none"),
            double_press=button.double not in (None, "none"),
            countdown=countdown,
            progress=progress,
        )

    async def draw_key(self, key: int) -> None:
        deck = self.bridge.deck
        if not deck or self._closed or key > deck.keys:
            return
        button = self._button(key)
        look = self.look_for(button) if button else KeyLook()
        self._drawn_countdowns[key] = look.countdown
        tiles = await self._background()
        image = render_key(look, deck.key_size, tiles.get(key))
        await self.bridge.show(key - 1, to_native(image, deck.format, deck.transform))

    async def draw_all(self) -> None:
        deck = self.bridge.deck
        if not deck:
            return
        for key in range(1, deck.keys + 1):
            await self.draw_key(key)
        for number in range(1, deck.dials + 1):
            await self.draw_dial(number)

    # --- Dials (Stream Deck +) ----------------------------------------------

    def _reading(self, dial: cfg.Dial) -> dials.Reading:
        return dials.read(dial.entity, self.ha.states.get(dial.entity) if dial.entity else None, dial.step)

    def _turned_to(self, number: int) -> float | None:
        """The value a dial was just turned to, until Home Assistant has had time to catch up."""
        turned = self._turned.get(number)
        return turned[0] if turned and time.monotonic() - turned[1] < TURN_HOLD else None

    def dial_look(self, dial: cfg.Dial) -> DialLook:
        domain = (dial.entity or "").split(".")[0]
        icon, bar = DIAL_DEFAULTS.get(domain, (None, "amber"))
        reading = self._reading(dial)
        value, text, on = reading.value, reading.text, reading.on
        turned = self._turned_to(dial.dial)
        if turned is not None and reading.show:
            value, text = turned, reading.show(turned)
            on = turned > reading.low if domain in ("light", "fan", "cover") else reading.on
        name = dial.text
        if name is None:
            state = self.ha.states.get(dial.entity or "", {})
            name = state.get("attributes", {}).get("friendly_name", dial.entity or "")
        elif "{{" in name or "{%" in name:
            name = self.ha.template_value(name) or ""
        return DialLook(icon=dial.icon or icon, text=name, value=text, level=reading.level(value), color=dial.color or bar, on=on)

    async def draw_dial(self, number: int) -> None:
        deck = self.bridge.deck
        if not deck or self._closed or not deck.dials or not deck.screen_width or number > deck.dials:
            return
        width = deck.screen_width // deck.dials
        dial = self._dial(number)
        image = render_dial(self.dial_look(dial) if dial else None, width, deck.screen_height)
        await self.bridge.show_screen((number - 1) * width, 0, width, deck.screen_height, to_native(image, "jpeg", deck.screen_transform))

    async def _turn(self, dial: cfg.Dial, detents: int) -> None:
        reading = self._reading(dial)
        if reading.value is None:
            return
        start = self._turned_to(dial.dial)
        value = reading.after(reading.value if start is None else start, detents)
        self._turned[dial.dial] = (value, time.monotonic())
        await self.draw_dial(dial.dial)
        task = self._turn_tasks.get(dial.dial)
        if task is None or task.done():
            self._turn_tasks[dial.dial] = asyncio.create_task(self._send_turns(dial))

    async def _send_turns(self, dial: cfg.Dial) -> None:
        """Sends the value a dial is turned to, a few times a second while it keeps turning."""
        sent = None
        while True:
            await asyncio.sleep(TURN_SETTLE)
            value = self._turned_to(dial.dial)
            if value is None or value == sent:
                break
            sent = value
            if dial.entity.startswith("timer.") and self.ha.state(dial.entity) not in ("active", "paused"):
                dials.TIMER_LENGTHS[dial.entity] = value * 60   # the next start runs this long
                self._redraw_timer(dial.entity)
                continue
            domain, service, data = dials.call_for(dial.entity, value)
            paused = domain == "timer" and self.ha.state(dial.entity) == "paused"
            await self.ha.call(domain, service, {"entity_id": dial.entity}, data)
            if paused:   # HA only sets a timer's time left by starting it, so pause it again
                await self.ha.call("timer", "pause", {"entity_id": dial.entity})
        if self._turn_tasks.get(dial.dial) is asyncio.current_task():
            del self._turn_tasks[dial.dial]   # the next turn starts a new sender
        await asyncio.sleep(TURN_HOLD)
        await self.draw_dial(dial.dial)   # from Home Assistant's state again

    async def _press_dial(self, dial: cfg.Dial | None) -> None:
        if dial is None:
            await self.run(self.page.tap_any, None)
            return
        action = dial.press if dial.press is not None else dials.default_press(dial.entity)
        if isinstance(action, str) and "." in action:
            action = cfg.CallAction(action=action)
        if action == "back" or isinstance(action, cfg.GoTo):
            await self.run(action, None)
        elif action not in (None, "none"):
            await perform(self.ha, action, dial)

    def brightness_now(self) -> int:
        if self.asleep and self.panel.sleep:
            return self.panel.sleep.brightness
        if self.page.brightness is not None:
            return self.page.brightness
        setting = self.panel.brightness
        if isinstance(setting, int):
            return setting
        if setting.follow:
            value = self.ha.state(setting.follow)
            try:
                return max(0, min(100, int(float(value))))
            except (TypeError, ValueError):
                return setting.default
        if setting.schedule:
            return self._scheduled(setting)
        return setting.default

    def _minutes(self, at: str) -> int | None:
        """Minutes after midnight for 07:30, sunrise or sunset (today's, from Home Assistant)."""
        if at in ("sunrise", "sunset"):
            sun = self.ha.states.get("sun.sun", {}).get("attributes", {})
            iso = sun.get("next_rising" if at == "sunrise" else "next_setting")
            if not iso:
                return None
            when = dt.datetime.fromisoformat(iso).astimezone(self.ha.now().tzinfo)
            return when.hour * 60 + when.minute
        hours, minutes = at.split(":")
        return int(hours) * 60 + int(minutes)

    def _scheduled(self, setting: cfg.Brightness) -> int:
        steps = sorted(((m, s.level) for s in setting.schedule if (m := self._minutes(s.at)) is not None))
        if not steps:
            return setting.default
        now = self.ha.now()
        minute = now.hour * 60 + now.minute
        current = [level for at, level in steps if at <= minute]
        return current[-1] if current else steps[-1][1]   # before the first step: the last one, from yesterday

    async def apply_brightness(self, force: bool = False) -> None:
        if not self.bridge.deck:
            return
        level = self.brightness_now()
        if force or level != self._level:
            self._level = level
            await self.bridge.brightness(level)

    # --- Page pictures ------------------------------------------------------

    async def _background(self) -> dict[int, Image.Image]:
        """Pieces of the page's picture or clock, by key; empty if the page has none."""
        deck = self.bridge.deck
        image = self.page.image
        if not deck or image is None:
            return {}
        now = self.ha.now()
        if isinstance(image, cfg.ClockImage):
            stamp = (self.page_name, "clock", image.style, image.font, image.hours, now.strftime("%Y%m%d%H%M"), deck.keys, deck.key_size)
        elif isinstance(image, cfg.PictureImage):
            stamp = (self.page_name, "picture", image.file, image.fit, deck.keys, deck.key_size)
        else:
            stamp = (self.page_name, "camera", image.entity, int(time.time() // image.refresh), deck.keys, deck.key_size)
        if self._tiles and self._tiles[0] == stamp:
            return self._tiles[1]

        tiles: dict[int, Image.Image] = {}
        try:
            if isinstance(image, cfg.ClockImage):
                if image.style == "across":
                    tiles = await asyncio.to_thread(images.clock_across, deck.cols, deck.rows, deck.key_size, now, image.font, image.hours)
                else:
                    cells = images.clock_cells(deck.cols, deck.rows, now, image.hours)
                    tiles = await asyncio.to_thread(lambda: {k: images.clock_key(cells.get(k), deck.key_size, image.font) for k in range(1, deck.keys + 1)})
            elif isinstance(image, cfg.PictureImage):
                picture = self._picture(image.file)
                if picture:
                    tiles = await asyncio.to_thread(images.picture_tiles, picture, deck.cols, deck.rows, deck.key_size, image.fit)
            else:
                data = await self.ha.camera_image(image.entity)
                if data:
                    picture = Image.open(io.BytesIO(data))
                    tiles = await asyncio.to_thread(images.picture_tiles, picture, deck.cols, deck.rows, deck.key_size, "cover")
                elif self._tiles and self._tiles[0][:3] == stamp[:3]:
                    tiles = self._tiles[1]   # keep the last picture until the camera answers
        except Exception:  # noqa: BLE001 - a bad picture shouldn't stop the panel
            log.exception("%s: couldn't draw page %s's picture", self.name, self.page_name)
        self._tiles = (stamp, tiles)
        return tiles

    def _picture(self, file: str) -> Image.Image | None:
        """A page picture. Paths are in Home Assistant's config folder: /config/x.png, or relative."""
        path = Path(file)
        if not path.is_absolute():
            path = self.config_dir / path
        elif not path.exists() and file.startswith("/config/"):
            path = self.config_dir / file.removeprefix("/config/")
        try:
            mtime = path.stat().st_mtime
            cached = self._pictures.get(str(path))
            if cached and cached[0] == mtime:
                return cached[1]
            picture = Image.open(path)
            picture.load()
            self._pictures[str(path)] = (mtime, picture)
            return picture
        except OSError as error:
            log.warning("%s: can't open %s: %s", self.name, path, error)
            return None

    # --- Clock, schedules and Sleep Mode ------------------------------------

    async def _ticker(self) -> None:
        while not self._closed:
            await asyncio.sleep(TICK)
            if not self.bridge.deck:
                continue
            try:
                await self._tick()
            except Exception:  # noqa: BLE001
                log.exception("%s: timer", self.name)

    def _redraw_timer(self, entity_id: str) -> None:
        for dial in self.page.dials:
            if dial.entity == entity_id:
                asyncio.create_task(self.draw_dial(dial.dial))

    async def _count_down(self) -> None:
        """Redraws keys showing a running timer whenever the seconds shown change."""
        while not self._closed:
            await asyncio.sleep(COUNTDOWN_TICK)
            if not self.bridge.deck:
                continue
            try:
                for button in self.page.buttons:
                    if button.countdown and button.entity and button.entity.startswith("timer.") and self.ha.state(button.entity) == "active":
                        left = timer_left(self.ha.states.get(button.entity))
                        if left is not None and format_left(left[0]) != self._drawn_countdowns.get(button.key):
                            await self.draw_key(button.key)
                for dial in self.page.dials:
                    if dial.entity and dial.entity.startswith("timer.") and self.ha.state(dial.entity) == "active" \
                            and self._turned_to(dial.dial) is None:
                        shown = self._reading(dial).text
                        if shown != self._drawn_dials.get(dial.dial):
                            self._drawn_dials[dial.dial] = shown
                            await self.draw_dial(dial.dial)
            except Exception:  # noqa: BLE001
                log.exception("%s: countdown", self.name)

    def _in_window(self) -> bool:
        sleep = self.panel.sleep
        if not sleep or not sleep.between:
            return False
        start, end = (self._minutes(t) for t in sleep.between)
        if start is None or end is None:
            return False
        now = self.ha.now()
        minute = now.hour * 60 + now.minute
        return start <= minute < end if start <= end else (minute >= start or minute < end)

    def _should_sleep(self) -> bool:
        sleep = self.panel.sleep
        if not sleep:
            return False
        idle = time.monotonic() - self._last_activity
        if self._in_window():
            return idle >= sleep.awake_seconds
        return bool(sleep.after_minutes) and idle >= sleep.after_minutes * 60

    async def _tick(self) -> None:
        if not self.asleep and self._should_sleep():
            await self.fall_asleep()
            return
        if self.asleep and not self._should_sleep():
            await self.wake(pressed=False)   # the sleep hours are over
            return
        if self.page.image is not None:
            before = self._tiles[0] if self._tiles else None
            await self._background()
            if self._tiles and self._tiles[0] != before:
                await self.draw_all()
        await self.apply_brightness()

    async def fall_asleep(self) -> None:
        log.info("%s: asleep", self.name)
        self.asleep = True
        self._wake_page = self.page_name
        if self.panel.sleep.page and self.panel.sleep.page in self.panel.pages:
            self.page_name = self.panel.sleep.page
        await self.apply_brightness()
        await self.draw_all()

    async def wake(self, pressed: bool = True) -> None:
        log.info("%s: awake", self.name)
        self.asleep = False
        if pressed:
            self._last_activity = time.monotonic()
        if self._wake_page:
            self.page_name = self._wake_page
        await self.apply_brightness()
        await self.draw_all()

    # --- Events -------------------------------------------------------------

    async def _on_deck(self, deck: Deck | None) -> None:
        if deck:
            log.info("%s: %s with %d keys", self.name, deck.model, deck.keys)
            if deck.keys != self.panel.key_count:
                log.warning("%s: the config says %s (%d keys) but a %s is attached", self.name, self.panel.size, self.panel.key_count, deck.model)
            self._tiles = None
            await self.apply_brightness(force=True)
            await self.draw_all()

    def _on_state(self, entity_id: str, _state: dict | None) -> None:
        brightness = self.panel.brightness
        if not isinstance(brightness, int) and brightness.follow == entity_id:
            asyncio.create_task(self.apply_brightness())
        for button in self.page.buttons:
            if button.entity == entity_id:
                asyncio.create_task(self.draw_key(button.key))
        for dial in self.page.dials:
            if dial.entity == entity_id:
                asyncio.create_task(self.draw_dial(dial.dial))

    def _template_changed(self, template: str) -> None:
        for button in self.page.buttons:
            if template in self._templates_of(button):
                asyncio.create_task(self.draw_key(button.key))
        for dial in self.page.dials:
            if template in self._templates_of(dial):
                asyncio.create_task(self.draw_dial(dial.dial))

    async def _on_dial(self, index: int, turn: int, down: bool | None) -> None:
        """A dial turned, or was pushed in (down) or let go. Pushing acts at once; there's no hold."""
        self._last_activity = time.monotonic()
        if self.asleep:
            if turn or down:
                await self.wake()   # waking takes the turn or press
            return
        dial = self._dial(index + 1)
        if turn and dial:
            await self._turn(dial, turn)
        elif down and dial:
            await self._press_dial(dial)

    async def _on_touch(self, kind: str, x: int, _y: int) -> None:
        """A tap on the strip does what pushing the dial below it does. Swipes do nothing."""
        self._last_activity = time.monotonic()
        if self.asleep:
            await self.wake()
            return
        deck = self.bridge.deck
        if kind == "swipe" or not deck or not deck.dials or not deck.screen_width:
            return
        number = min(deck.dials, x * deck.dials // deck.screen_width + 1)
        await self._press_dial(self._dial(number))

    async def _on_key(self, index: int, down: bool) -> None:
        key = index + 1
        self._last_activity = time.monotonic()
        if down and self.asleep:
            # A sleeping panel still showing its buttons runs whatever was pressed. One showing
            # another page (a clock) only wakes, unless the key under the finger says `asleep: true`.
            wake_page = self._wake_page or self.page_name
            showing_buttons = self.page_name == wake_page
            button = self._button(key, wake_page)
            await self.wake()
            if not button or not (showing_buttons or button.asleep):
                log.info("%s: key %d woke the panel", self.name, key)
                self._swallow.add(key)
                return
            log.info("%s: key %d woke the panel and runs", self.name, key)
        if not down and key in self._swallow:
            self._swallow.discard(key)
            return
        button = self._button(key)
        if down:
            self._swallow.discard(key)   # in case a waking press's release never arrived
            self._pressed_at[key] = time.monotonic()
            waiting = self._tap_later.pop(key, None)
            if waiting:
                waiting.cancel()   # a second press: this one is a double press, not two taps
                self._second.add(key)
            if button and button.hold:
                self._hold_tasks[key] = asyncio.create_task(self._hold_after(key, button))
            return

        hold_task = self._hold_tasks.pop(key, None)
        if hold_task:
            hold_task.cancel()
        second = key in self._second
        self._second.discard(key)
        if key in self._held:
            self._held.discard(key)
            return
        if button is None:
            await self.run(self.page.tap_any, None)
        elif second and button.double:
            await self._press(button, button.double, "double")
        elif button.double:
            # Only keys with a double press wait; every other key runs at once.
            self._tap_later[key] = asyncio.create_task(self._tap_after(key, button))
        else:
            await self._tap(button)

    async def _tap(self, button: cfg.Button) -> None:
        await self._press(button, button.tap if button.tap is not None else self._default_tap(button), "tap")

    async def _tap_after(self, key: int, button: cfg.Button) -> None:
        await asyncio.sleep(DOUBLE_TIME)
        self._tap_later.pop(key, None)
        await self._tap(button)

    async def _hold_after(self, key: int, button: cfg.Button) -> None:
        await asyncio.sleep(HOLD_TIME)
        self._held.add(key)
        if button.hold in cfg.DIM_ACTIONS:
            await self._dim(key, button)   # runs until the key is let go, which cancels this task
        else:
            await self._press(button, button.hold, "hold")

    # --- Dimming while held -------------------------------------------------

    async def _light_entities(self, button: cfg.Button) -> list[str]:
        if button.entity:
            return [button.entity]
        value = await self.ha.evaluate(_lights_list_template(button.lights)) or ""
        return re.findall(r"light\.[a-z0-9_]+", value)

    def _brightness(self, entity: str) -> int | None:
        """A light's brightness in percent, or None if it's off (or unavailable, or can't dim)."""
        state = self.ha.states.get(entity) or {}
        if state.get("state") != "on":
            return None
        raw = (state.get("attributes") or {}).get("brightness")
        return max(1, round(raw * 100 / 255)) if isinstance(raw, (int, float)) else None

    async def _dim(self, key: int, button: cfg.Button) -> None:
        """Steps the lights brighter or dimmer every DIM_EVERY seconds until the key is let go.
        Keeps its own count of each light's level, since HA's state lags behind the steps."""
        entities = await self._light_entities(button)
        levels = {e: self._brightness(e) for e in entities}
        # Lights that are on but report no brightness can't dim, and unavailable ones can't change: leave them out.
        levels = {e: v for e, v in levels.items() if v is not None or self.ha.state(e) == "off"}
        if button.hold == "dim_brighten":
            on = [v for v in levels.values() if v is not None]
            if not on:
                up = True
            elif key in self._dimmed_up:
                up = not self._dimmed_up[key]
            else:
                up = sum(on) / len(on) < 50
            self._dimmed_up[key] = up
        else:
            up = button.hold == "brighten"
        direction = 1 if up else -1
        while True:
            new = dim_steps(levels, direction)
            if not new:
                return
            by_level: dict[int, list[str]] = {}
            for entity, level in new.items():
                by_level.setdefault(level, []).append(entity)
            started = time.monotonic()
            for level, group in by_level.items():
                await self.ha.call("light", "turn_on", {"entity_id": group}, {"brightness_pct": level, "transition": DIM_EVERY})
            levels.update(new)
            await asyncio.sleep(max(0.05, DIM_EVERY - (time.monotonic() - started)))

    @staticmethod
    def _default_tap(button: cfg.Button):
        if button.entity or button.lights:
            domain = button.entity.split(".")[0] if button.entity else "light"
            if domain in ("sensor", "binary_sensor", "weather", "sun", "camera", "person", "device_tracker"):
                return "none"
            return "toggle"
        return "none"

    async def _press(self, button: cfg.Button, action, which: str = "tap") -> None:
        if action in (None, "none"):
            return
        if button.confirm:
            # The same kind of press again runs it; a different one starts over, waiting for its own second press.
            pending = self._confirming.pop(button.key, None)
            if pending:
                pending.cancel()
            if pending is None or self._confirm_for.get(button.key) != which:
                self._confirming[button.key] = asyncio.create_task(self._confirm_timeout(button.key))
                self._confirm_for[button.key] = which
                await self.draw_key(button.key)
                return
            await self.draw_key(button.key)
        await self.run(action, button)

    async def _confirm_timeout(self, key: int) -> None:
        await asyncio.sleep(CONFIRM_TIME)
        self._confirming.pop(key, None)
        await self.draw_key(key)

    # --- Actions ------------------------------------------------------------

    async def run(self, action, button: cfg.Button | None) -> None:
        if action in (None, "none"):
            return
        log.info("%s: key %s runs %s", self.name, button.key if button else "-", _describe(action, button))
        if action == "back":
            if self.history:
                await self.show_page(self.history.pop(), remember=False)
            elif self.page_name != self.panel.start_page:
                await self.show_page(self.panel.start_page, remember=False)
        elif isinstance(action, cfg.GoTo):
            await self.show_page(action.go_to)
        else:
            await perform(self.ha, action, button, self._state_of(button) if button else None)

    async def show_page(self, name: str, remember: bool = True) -> None:
        if name not in self.panel.pages or name == self.page_name:
            return
        if remember:
            self.history.append(self.page_name)
            del self.history[:-20]
        for task in self._confirming.values():
            task.cancel()
        self._confirming.clear()
        self.page_name = name
        log.info("%s: page %s", self.name, name)
        await self.apply_brightness()
        await self.draw_all()


def _describe(action, button: cfg.Button | None) -> str:
    """An action, short enough for a log line."""
    if isinstance(action, cfg.CallAction):
        return f"{action.action} {action.target or (button.entity if button else '')}".strip()
    if isinstance(action, cfg.GoTo):
        return f"go_to {action.go_to}"
    what = button.entity if button and button.entity else (f"lights in {button.lights.area or button.lights.floor}" if button and button.lights else "")
    return f"{action} {what}".strip()


def _is_light(hex_color: str) -> bool:
    match = re.fullmatch(r"#([0-9a-fA-F]{2})([0-9a-fA-F]{2})([0-9a-fA-F]{2})", hex_color)
    if not match:
        return False
    r, g, b = (int(part, 16) for part in match.groups())
    return 0.299 * r + 0.587 * g + 0.114 * b > 160
