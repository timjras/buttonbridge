"""Read and check streamdeck.yaml.

The file format is described in docs/CONFIG.md. load_config() returns
a Config object, or raises ConfigError with one message per problem, each
naming the line in the file where it is.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Literal, Union

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

# --- The shape of the file -------------------------------------------------

# Keys per model, as (columns, rows), and the models with dials.
SIZES = {"mini": (3, 2), "module6": (3, 2), "module15": (5, 3), "module32": (8, 4), "plus": (4, 2)}
DIALS = {"plus": 4}

COLOR_NAMES = ("black", "grey", "dark", "white", "amber", "blue", "green", "red", "purple")
Color = Annotated[str, Field(pattern=r"^(" + "|".join(COLOR_NAMES) + r"|#[0-9a-fA-F]{6})$")]
Icon = Annotated[str, Field(pattern=r"^(mdi:[a-z0-9-]+|.+\.(png|svg))$")]
CLOCK_FONTS = ("montserrat", "archivo", "roboto", "oswald", "bebas")
Level = Annotated[int, Field(ge=0, le=100)]


class Strict(BaseModel):
    # Unknown settings are almost always typos, so reject them.
    model_config = ConfigDict(extra="forbid")


class Look(Strict):
    icon: Icon | None = None
    background: Color | None = None
    icon_color: Color | None = None


class Style(Look):
    text: str | None = None
    text_position: Literal["bottom", "top", "none"] = "bottom"


class GoTo(Strict):
    go_to: str


class CallAction(Strict):
    action: Annotated[str, Field(pattern=r"^[a-z0-9_]+\.[a-z0-9_]+$")]
    target: dict | None = None
    data: dict | None = None


# toggle / turn_on / turn_off act on the button's entity, or on its room or floor.
# dim / brighten / dim_brighten are for hold: only; they step the lights while the key is held.
DIM_ACTIONS = ("dim", "brighten", "dim_brighten")
Action = Union[Literal["toggle", "turn_on", "turn_off", "back", "none", "dim", "brighten", "dim_brighten"], GoTo, CallAction]


class Lights(Strict):
    """All the lights in one HA area (room) or floor."""

    area: str | None = None
    floor: str | None = None

    @model_validator(mode="after")
    def _one_place(self):
        if (self.area is None) == (self.floor is None):
            raise ValueError("give either area: or floor:, not both or neither")
        return self


class Button(Style):
    key: Annotated[int, Field(ge=1)]
    entity: str | None = None
    lights: Lights | None = None
    style: str | None = None
    tap: Action | None = None
    hold: Action | None = None  # long press
    double: Action | None = None  # double press; only these keys wait to see if a second press follows
    confirm: bool = False
    asleep: bool = False  # runs even when it wakes a sleeping panel that's showing another page (a clock)
    countdown: bool = True  # a timer key shows the time left while it runs
    when: dict[str, Look] = {}

    @field_validator("when", mode="before")
    @classmethod
    def _state_names_as_text(cls, value):
        # YAML reads a bare on/off as true/false; HA states are text.
        if isinstance(value, dict):
            return {_state_name(k): v for k, v in value.items()}
        return value


class Dial(Strict):
    """One of a Stream Deck +'s knobs, and the part of the touch strip above it."""

    dial: Annotated[int, Field(ge=1)]
    entity: str | None = None
    text: str | None = None
    icon: Icon | None = None
    color: Color | None = None  # the level bar's colour
    step: Annotated[float, Field(gt=0)] | None = None  # how much one click of the dial changes the value
    press: Action | None = None  # pushing the dial in, or tapping the strip above it


class ClockImage(Strict):
    type: Literal["clock"]
    style: Literal["digit_per_key", "across"] = "digit_per_key"
    font: Literal[CLOCK_FONTS] = "montserrat"
    hours: Literal[12, 24] = 24  # 12: 7:41 with AM/PM; 24: 07:41 and 19:41


class PictureImage(Strict):
    type: Literal["picture"]
    file: str
    fit: Literal["cover", "contain"] = "cover"


class CameraImage(Strict):
    type: Literal["camera"]
    entity: Annotated[str, Field(pattern=r"^camera\.")]
    refresh: Annotated[float, Field(ge=1)] = 10


Image = Annotated[Union[ClockImage, PictureImage, CameraImage], Field(discriminator="type")]


class Page(Strict):
    buttons: list[Button] = []
    dials: list[Dial] = []
    image: Image | None = None
    tap_any: Action | None = None
    brightness: Level | None = None  # overrides the panel's brightness while this page shows


TimeOfDay = Annotated[str, Field(pattern=r"^(([01][0-9]|2[0-3]):[0-5][0-9]|sunrise|sunset)$")]


class ScheduleStep(Strict):
    at: TimeOfDay  # each step lasts until the next one starts
    level: Level


class Brightness(Strict):
    default: Level = 70
    schedule: list[ScheduleStep] = []
    follow: Annotated[str, Field(pattern=r"^input_number\.[a-z0-9_]+$")] | None = None  # an HA slider helper


class Sleep(Strict):
    """Sleep mode: show another page, dimmed. Either trigger, or both, puts the panel to sleep."""

    after_minutes: Annotated[int, Field(ge=1)] | None = None  # sleep after this long with no presses
    between: tuple[TimeOfDay, TimeOfDay] | None = None  # sleep during this window, e.g. ["22:00", "07:00"]
    awake_seconds: Annotated[int, Field(ge=5, le=3600)] = 60  # woken during `between`, sleep again after this long with no presses
    page: str | None = None  # page to show while asleep, e.g. a clock
    brightness: Level = 5

    @model_validator(mode="after")
    def _has_trigger(self):
        if self.after_minutes is None and self.between is None:
            raise ValueError("give after_minutes:, between:, or both, to say when it sleeps")
        return self


class Panel(Strict):
    id: Annotated[str, Field(pattern=r"^sd-[0-9a-f]{6}$")]
    size: Literal[tuple(SIZES)]
    brightness: Level | Brightness = 70
    sleep: Sleep | None = None
    start_page: str = "home"
    pages: Annotated[dict[str, Page], Field(min_length=1)]

    @property
    def key_count(self) -> int:
        cols, rows = SIZES[self.size]
        return cols * rows

    @property
    def dial_count(self) -> int:
        return DIALS.get(self.size, 0)


class Config(Strict):
    panels: dict[str, Panel] = {}
    styles: dict[str, Style] = {}


def _state_name(key) -> str:
    if key is True:
        return "on"
    if key is False:
        return "off"
    return str(key)


# --- Loading and checking --------------------------------------------------


class ConfigError(Exception):
    def __init__(self, problems: list[str]):
        self.problems = problems
        super().__init__("\n".join(problems))


def load_config(path: str | Path) -> Config:
    text = Path(path).read_text(encoding="utf-8")
    return parse_config(text)


def parse_config(text: str) -> Config:
    try:
        root = yaml.compose(text)
    except yaml.YAMLError as err:
        raise ConfigError([f"The file isn't valid YAML: {err}"]) from None
    problems = _duplicate_keys(root)
    if problems:
        raise ConfigError(problems)

    data = yaml.safe_load(text) or {}
    if not isinstance(data, dict):
        raise ConfigError(["The file should start with sections like 'panels:' and 'pages:'."])
    try:
        config = Config.model_validate(data)
    except ValidationError as err:
        raise ConfigError(_describe_all(root, err.errors())) from None

    problems = [_at(root, loc) + msg for loc, msg in _cross_check(config)]
    if problems:
        raise ConfigError(problems)
    return config


# Plain-English messages for settings that accept several forms, where
# pydantic would otherwise list one complaint per form.
_EXPECTED = {
    "id": "should look like sd-3c71bf, the ID a new panel shows on its keys.",
    "key": "should be a key number, counting from 1.",
    "dial": "should be a dial number, counting from 1 at the left.",
    "size": "should be one of: " + ", ".join(SIZES) + ".",
    "tap": "should be toggle, turn_on, turn_off, back, none, { go_to: page_name } or { action: domain.name }.",
    "image": "needs a type: clock, picture or camera.",
    "background": "should be a colour name (" + ", ".join(COLOR_NAMES) + ") or #rrggbb.",
    "font": "should be one of: " + ", ".join(CLOCK_FONTS) + ".",
    "at": "should be a time like 07:30, or sunrise or sunset.",
    "follow": "should be an HA number helper, like input_number.panel_brightness.",
    "icon": "should be an icon name like mdi:lightbulb, or a .png or .svg file.",
}
_EXPECTED["icon_color"] = _EXPECTED["background"]
_EXPECTED["double"] = _EXPECTED["tap_any"] = _EXPECTED["press"] = _EXPECTED["tap"]
_EXPECTED["hold"] = _EXPECTED["tap"].replace(" or {", ", dim, brighten, dim_brighten or {", 1)
_EXPECTED["color"] = _EXPECTED["background"]
_IMAGE_TYPES = {"clock", "picture", "camera"}


def _field_path(loc) -> tuple:
    """The part of a pydantic error location that names places in the file."""
    path = []
    for part in loc:
        text = str(part)
        if "[" in text or text.startswith(("function-", "constrained-")) or text in ("GoTo", "CallAction"):
            break  # a union branch, not a place in the file
        if path and path[-1] == "image" and text in _IMAGE_TYPES:
            continue
        path.append(part)
    return tuple(path)


def _describe_all(root, errors: list[dict]) -> list[str]:
    problems, seen = [], set()
    for error in errors:
        # A setting that takes a number or a section (brightness: 70 or brightness: {...}): when a
        # section was given, the complaint that it isn't a number is noise.
        if error["type"] in ("int_type", "int_parsing") and isinstance(error.get("input"), dict) and len(errors) > 1:
            continue
        path = _field_path(error["loc"])
        name = str(path[-1]) if path else ""
        in_branch = len(path) < len(error["loc"]) and str(error["loc"][len(path)]) not in _IMAGE_TYPES
        if name in _EXPECTED and (in_branch or error["type"] in ("string_pattern_mismatch", "union_tag_invalid", "literal_error")):
            message = f"{name} {_EXPECTED[name]}"
        elif error["type"] == "extra_forbidden":
            message = f"'{name}' isn't a setting here. Check the spelling."
        elif error["type"] == "missing":
            message = f"'{name}' is required here."
        else:
            message = f"{name}: {error['msg'].removeprefix('Value error, ')}"
        problem = _at(root, path) + message
        if problem not in seen:
            seen.add(problem)
            problems.append(problem)
    return problems


def _cross_check(config: Config) -> list[tuple[tuple, str]]:
    """Problems that involve more than one part of the file."""
    problems: list[tuple[tuple, str]] = []

    seen_ids: dict[str, str] = {}
    for name, panel in config.panels.items():
        if panel.id in seen_ids:
            problems.append(
                (("panels", name, "id"), f"Panels '{seen_ids[panel.id]}' and '{name}' have the same id {panel.id}.")
            )
        seen_ids.setdefault(panel.id, name)

    for pname, panel in config.panels.items():
        ploc = ("panels", pname)
        if panel.start_page not in panel.pages:
            where = ploc + ("start_page",) if "start_page" in panel.model_fields_set else ploc
            problems.append((where, f"Panel '{pname}' has no page called '{panel.start_page}'."))

        if panel.sleep and panel.sleep.page and panel.sleep.page not in panel.pages:
            problems.append((ploc + ("sleep", "page"), f"Panel '{pname}' has no page called '{panel.sleep.page}'."))

        for page_name, page in panel.pages.items():
            gloc = ploc + ("pages", page_name)
            used_keys: set[int] = set()
            actions = [(("tap_any",), page.tap_any)]
            for i, button in enumerate(page.buttons):
                loc = gloc + ("buttons", i)
                if button.key in used_keys:
                    problems.append((loc + ("key",), f"Key {button.key} is used twice on page '{page_name}'."))
                used_keys.add(button.key)
                if button.key > panel.key_count:
                    problems.append(
                        (loc + ("key",), f"Key {button.key} doesn't exist: a {panel.size} panel has {panel.key_count} keys.")
                    )
                if button.entity and button.lights:
                    problems.append((loc, "A button can have entity: or lights:, not both."))
                if button.when and not (button.entity or button.lights):
                    problems.append((loc + ("when",), "when: needs an entity: or lights: to watch."))
                if button.lights and set(button.when) - {"any_on", "all_off"}:
                    problems.append((loc + ("when",), "For lights:, the states are any_on and all_off."))
                for which in ("tap", "hold", "double"):
                    action = getattr(button, which)
                    if action in ("toggle", "turn_on", "turn_off") and not (button.entity or button.lights):
                        problems.append((loc + (which,), f"{action} needs an entity: or lights: to act on."))
                    if action in DIM_ACTIONS and which != "hold":
                        problems.append((loc + (which,), f"{action} works on a long press (hold:) only."))
                    elif action in DIM_ACTIONS and not (button.lights or (button.entity or "").startswith("light.")):
                        problems.append((loc + (which,), f"{action} needs a light (entity: light.x) or lights: to dim."))
                    if (isinstance(action, CallAction) and action.action in ("script.turn_on", "scene.turn_on") and not action.target
                            and (button.entity or "").split(".")[0] != action.action.split(".")[0]):
                        problems.append((loc + (which,), f"Pick the {action.action.split('.')[0]} to run."))
                if button.style and button.style not in config.styles:
                    problems.append((loc + ("style",), f"There's no style called '{button.style}' under 'styles:'."))
                actions += [(("buttons", i, w), getattr(button, w)) for w in ("tap", "hold", "double")]
            used_dials: set[int] = set()
            for i, dial in enumerate(page.dials):
                loc = gloc + ("dials", i)
                if not panel.dial_count:
                    problems.append((loc, f"A {panel.size} panel has no dials; only a Stream Deck + (size: plus) does."))
                    break
                if dial.dial in used_dials:
                    problems.append((loc + ("dial",), f"Dial {dial.dial} is used twice on page '{page_name}'."))
                used_dials.add(dial.dial)
                if dial.dial > panel.dial_count:
                    problems.append((loc + ("dial",), f"Dial {dial.dial} doesn't exist: a {panel.size} panel has {panel.dial_count} dials."))
                if dial.press in ("toggle", "turn_on", "turn_off") and not dial.entity:
                    problems.append((loc + ("press",), f"{dial.press} needs an entity: to act on."))
                actions.append((("dials", i, "press"), dial.press))
            for aloc, action in actions:
                if isinstance(action, GoTo) and action.go_to not in panel.pages:
                    problems.append((gloc + aloc, f"Panel '{pname}' has no page called '{action.go_to}'."))

    return problems


def unused_pages(config: Config) -> list[str]:
    """Pages, as 'panel/page', that no start page leads to. Not an error, but probably a mistake."""
    unused = []
    for pname, panel in config.panels.items():
        reached = _reachable(panel, panel.start_page)
        if panel.sleep and panel.sleep.page:
            reached |= _reachable(panel, panel.sleep.page)
        unused += [f"{pname}/{name}" for name in panel.pages if name not in reached]
    return unused


def _reachable(panel: Panel, start: str) -> set[str]:
    seen: set[str] = set()
    todo = [start]
    while todo:
        name = todo.pop()
        if name in seen or name not in panel.pages:
            continue
        seen.add(name)
        page = panel.pages[name]
        for action in [page.tap_any] + [a for b in page.buttons for a in (b.tap, b.hold, b.double)] + [d.press for d in page.dials]:
            if isinstance(action, GoTo):
                todo.append(action.go_to)
    return seen


# --- Line numbers -----------------------------------------------------------


def _at(root: yaml.Node | None, loc) -> str:
    """'line N: ' for the deepest part of loc that exists in the file."""
    node = root
    for part in loc:
        child = None
        if isinstance(node, yaml.MappingNode):
            for k, v in node.value:
                if k.value == str(part):
                    child = v
                    break
        elif isinstance(node, yaml.SequenceNode) and isinstance(part, int) and part < len(node.value):
            child = node.value[part]
        if child is None:
            break
        node = child
    if node is None:
        return ""
    return f"line {node.start_mark.line + 1}: "


def _duplicate_keys(node: yaml.Node | None) -> list[str]:
    problems = []
    if isinstance(node, yaml.MappingNode):
        seen = set()
        for k, v in node.value:
            if k.value in seen:
                problems.append(f"line {k.start_mark.line + 1}: '{k.value}' appears twice in the same section.")
            seen.add(k.value)
            problems += _duplicate_keys(v)
    elif isinstance(node, yaml.SequenceNode):
        for item in node.value:
            problems += _duplicate_keys(item)
    return problems
