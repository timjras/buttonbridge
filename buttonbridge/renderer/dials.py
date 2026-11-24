"""What a Stream Deck + dial controls: reading an entity's level, and setting a new one.

Each kind of entity has one number a dial turns: a light's brightness, a speaker's volume, a
shade's position, a thermostat's target temperature, a fan's speed, or a number helper's value.
Anything else just shows its state.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Callable

# The length the next start of each timer runs for, in seconds, once a dial has set it
# (otherwise the timer's own duration). Shared by every panel, like the timers themselves.
TIMER_LENGTHS: dict[str, float] = {}
TIMER_MOST = 600  # minutes: the longest a dial sets a timer to

# Pieces of HA state that only show what's there: turning them does nothing.
OFF_STATES = ("off", "unavailable", "unknown", "closed", "idle", "standby")


@dataclass
class Reading:
    """An entity as a dial sees it."""

    value: float | None  # what turning changes, in the entity's own units; None if it can't be turned
    low: float = 0
    high: float = 100
    step: float = 1  # one detent
    on: bool = True  # False draws the dial dimmed
    text: str = ""  # the value as the strip shows it
    show: Callable[[float], str] | None = None  # formats another value the same way
    most: float | None = None  # the highest a turn goes, when that isn't `high`
    bar: bool = True  # False shows no level bar

    def level(self, value: float | None = None) -> int | None:
        """0-100, for the bar under the value; None if there's nothing to show."""
        value = self.value if value is None else value
        if value is None or not self.bar or self.high <= self.low:
            return None
        return round(max(0.0, min(1.0, (value - self.low) / (self.high - self.low))) * 100)

    def after(self, start: float, detents: int) -> float:
        """The value `detents` clicks from `start`, on a whole step, within low and high."""
        value = round((start + detents * self.step - self.low) / self.step) * self.step + self.low
        value = max(self.low, min(self.high if self.most is None else self.most, value))
        return round(value, 4)


def _duration(value) -> float | None:
    """Seconds in a timer attribute like "0:25:00" (or "1 day, 0:25:00")."""
    if not isinstance(value, str):
        return None
    days = 0
    if "day" in value:
        first, _, value = value.partition(",")
        days = int(first.split()[0])
    try:
        parts = [float(p) for p in value.strip().split(":")]
    except ValueError:
        return None
    seconds = 0.0
    for part in parts:
        seconds = seconds * 60 + part
    return days * 86400 + seconds


def timer_left(state: dict | None, now: dt.datetime | None = None) -> tuple[float, float | None] | None:
    """Seconds left on an active or paused HA timer, and its full duration (if known)."""
    if not state or state.get("state") not in ("active", "paused"):
        return None
    attributes = state.get("attributes") or {}
    total = _duration(attributes.get("duration"))
    if state["state"] == "active" and attributes.get("finishes_at"):
        try:
            finishes = dt.datetime.fromisoformat(attributes["finishes_at"])
        except ValueError:
            return None
        now = now or dt.datetime.now(dt.timezone.utc)
        return max(0.0, (finishes - now).total_seconds()), total
    left = _duration(attributes.get("remaining"))
    return (left, total) if left is not None else None


def format_left(seconds: float) -> str:
    """25:00, 4:59, 1:02:03. Rounds up, so a timer shows 0:00 only when it has finished."""
    whole = int(-(-seconds // 1))
    hours, rest = divmod(whole, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes}:{secs:02d}"


def _number(value, default: float | None = None) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _percent(value: float) -> str:
    return f"{round(value)}%"


def _degrees(value: float) -> str:
    return f"{value:g}°"


def read(entity_id: str | None, state: dict | None, step: float | None = None) -> Reading:
    """How a dial shows `entity_id`, given its HA state object. `step` overrides one detent."""
    if not entity_id or state is None:
        return Reading(None, on=False, text="?" if entity_id else "")
    domain = entity_id.split(".")[0]
    status = state.get("state")
    attrs = state.get("attributes", {})
    if status == "unavailable":
        return Reading(None, on=False, text="Unavailable")
    if domain == "timer":
        return _timer(entity_id, state, step)

    reading: Reading | None = None
    if domain == "light":
        on = status == "on"
        dimmable = attrs.get("supported_color_modes") != ["onoff"]
        pct = round(_number(attrs.get("brightness"), 255) / 255 * 100) if on else 0
        reading = Reading(pct, step=step or (5 if dimmable else 100), on=on, show=lambda v: _percent(v) if v else "Off")
    elif domain == "media_player":
        volume = _number(attrs.get("volume_level"))
        if volume is not None:
            reading = Reading(volume * 100, step=step or 2, on=status not in ("off", "standby", "unknown"),
                              show=lambda v: "Muted" if attrs.get("is_volume_muted") else _percent(v))
    elif domain == "cover":
        position = _number(attrs.get("current_position"))
        if position is not None:
            reading = Reading(position, step=step or 5, on=position > 0, show=lambda v: "Closed" if v == 0 else _percent(v))
    elif domain == "climate":
        target = _number(attrs.get("temperature"))
        if target is not None:
            reading = Reading(target, low=_number(attrs.get("min_temp"), 7), high=_number(attrs.get("max_temp"), 35),
                              step=step or _number(attrs.get("target_temp_step"), 0.5), on=status != "off", show=_degrees)
    elif domain == "fan":
        on = status == "on"
        pct = _number(attrs.get("percentage"), 100 if on else 0) if on else 0
        reading = Reading(pct, step=step or _number(attrs.get("percentage_step"), 10), on=on, show=lambda v: _percent(v) if v else "Off")
    elif domain in ("input_number", "number"):
        value = _number(status)
        if value is not None:
            unit = attrs.get("unit_of_measurement") or ""
            reading = Reading(value, low=_number(attrs.get("min"), 0), high=_number(attrs.get("max"), 100),
                              step=step or _number(attrs.get("step"), 1), show=lambda v: f"{v:g}{(' ' + unit) if unit and unit != '%' else unit}")

    if reading is None:  # shows its state, and turning does nothing
        unit = attrs.get("unit_of_measurement")
        return Reading(None, on=status not in OFF_STATES, text=f"{status} {unit}" if unit else str(status).capitalize())
    reading.text = reading.show(reading.value)
    return reading


def _timer(entity_id: str, state: dict, step: float | None) -> Reading:
    """A timer, in minutes. Running: the time left, and turning adds or takes away minutes.
    Idle: how long the next start runs, which turning sets."""
    left = timer_left(state)
    if left is not None:
        seconds, total = left
        reading = Reading(seconds / 60, low=1, high=(total or seconds) / 60, most=TIMER_MOST, step=step or 1,
                          show=lambda v: format_left(v * 60))
    else:
        length = TIMER_LENGTHS.get(entity_id) or _duration((state.get("attributes") or {}).get("duration")) or 25 * 60
        reading = Reading(length / 60, low=1, high=TIMER_MOST, step=step or 1, on=False, bar=False,
                          show=lambda v: f"{v:g} min")
    reading.text = reading.show(reading.value)
    return reading


def call_for(entity_id: str, value: float) -> tuple[str, str, dict]:
    """The HA action that sets `entity_id` to `value`: (domain, service, data)."""
    domain = entity_id.split(".")[0]
    if domain == "timer":
        # Restarts a running timer with the new time left; panel.py keeps an idle timer's length instead.
        return "timer", "start", {"duration": round(value * 60)}
    if domain == "light":
        return ("light", "turn_off", {}) if value <= 0 else ("light", "turn_on", {"brightness_pct": round(value)})
    if domain == "media_player":
        return "media_player", "volume_set", {"volume_level": round(value / 100, 3)}
    if domain == "cover":
        return "cover", "set_cover_position", {"position": round(value)}
    if domain == "climate":
        return "climate", "set_temperature", {"temperature": value}
    if domain == "fan":
        return ("fan", "turn_off", {}) if value <= 0 else ("fan", "set_percentage", {"percentage": round(value)})
    return domain, "set_value", {"value": value}


def default_press(entity_id: str | None):
    """What pushing a dial does when the config doesn't say: toggle it, play or pause, or nothing."""
    domain = (entity_id or "").split(".")[0]
    if domain == "media_player":
        return "media_player.media_play_pause"
    if not domain or domain in ("sensor", "binary_sensor", "weather", "sun", "input_number", "number", "person", "device_tracker"):
        return "none"
    return "toggle"
