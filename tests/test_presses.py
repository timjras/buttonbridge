import asyncio
from types import SimpleNamespace

from renderer import config as cfg
from renderer import panel as panel_mod
from renderer.panel import PanelController


def controller(button: cfg.Button) -> tuple[PanelController, list]:
    """A PanelController with one button that records the actions it runs instead of calling HA."""
    c = PanelController("test", SimpleNamespace(start_page="home", pages={"home": cfg.Page(buttons=[button])}, sleep=None),
               {}, SimpleNamespace(deck=None), SimpleNamespace(state_listeners=[]))
    ran = []

    async def run(action, _button):
        ran.append(action)

    async def draw_key(_key):
        pass

    c.run, c.draw_key = run, draw_key
    return c, ran


async def tap(c: PanelController, hold: float = 0.0, gap: float = 0.0) -> None:
    await c._on_key(0, True)
    await asyncio.sleep(hold)
    await c._on_key(0, False)
    await asyncio.sleep(gap)


def button(**actions) -> cfg.Button:
    return cfg.Button(key=1, entity="light.x", **actions)


def test_a_key_without_double_press_runs_at_once():
    async def go():
        c, ran = controller(button(tap="toggle", hold="turn_off"))
        await tap(c)
        assert ran == ["toggle"]   # no waiting for a possible second press
    asyncio.run(go())


def test_long_press():
    async def go():
        c, ran = controller(button(tap="toggle", hold="turn_off"))
        await tap(c, hold=panel_mod.HOLD_TIME + 0.1)
        assert ran == ["turn_off"]
    asyncio.run(go())


def test_double_press_and_single_tap_on_a_double_key():
    async def go():
        c, ran = controller(button(tap="toggle", double="turn_on"))
        await tap(c)
        assert ran == []   # waits to see if a second press comes
        await asyncio.sleep(panel_mod.DOUBLE_TIME + 0.1)
        assert ran == ["toggle"]

        ran.clear()
        await tap(c, gap=0.05)
        await tap(c)
        await asyncio.sleep(panel_mod.DOUBLE_TIME + 0.1)
        assert ran == ["turn_on"]
    asyncio.run(go())


def test_all_three_on_one_key():
    async def go():
        c, ran = controller(button(tap="toggle", hold="turn_off", double="turn_on"))
        await tap(c, hold=panel_mod.HOLD_TIME + 0.1)
        await asyncio.sleep(panel_mod.DOUBLE_TIME + 0.1)
        assert ran == ["turn_off"]
    asyncio.run(go())


def test_confirm_waits_for_the_same_kind_of_press():
    async def go():
        c, ran = controller(button(tap="toggle", hold="turn_off", confirm=True))
        await tap(c)
        await tap(c, hold=panel_mod.HOLD_TIME + 0.1)   # a different press: starts over
        assert ran == []
        await tap(c, hold=panel_mod.HOLD_TIME + 0.1)
        assert ran == ["turn_off"]
        c.detach()
    asyncio.run(go())


def test_double_is_checked_like_tap():
    config = """
panels:
  p:
    id: sd-000001
    size: mini
    pages:
      home:
        buttons:
          - { key: 1, double: { go_to: nowhere } }
          - { key: 2, hold: { action: script.turn_on } }
          - { key: 3, entity: script.night, double: { action: script.turn_on } }
"""
    try:
        cfg.parse_config(config)
        raise AssertionError("expected problems")
    except cfg.ConfigError as error:
        text = "\n".join(error.problems)
        assert "no page called 'nowhere'" in text
        assert "Pick the script to run" in text
        assert len(error.problems) == 2


# --- Dimming while held ---------------------------------------------------------

from renderer.panel import DIM_LOWEST, dim_steps


def test_dim_steps():
    assert dim_steps({"light.a": 50, "light.b": None}, -1) == {"light.a": 40}      # off lights stay off
    assert dim_steps({"light.a": 5}, -1) == {"light.a": DIM_LOWEST}                  # stops at the bottom, not off
    assert dim_steps({"light.a": DIM_LOWEST}, -1) == {}
    assert dim_steps({"light.a": 95, "light.b": None}, 1) == {"light.a": 100}       # only the ones that are on
    assert dim_steps({"light.a": None, "light.b": None}, 1) == {"light.a": 10, "light.b": 10}  # all off: all come up
    assert dim_steps({"light.a": 100}, 1) == {}


def dimmer(hold: str, states: dict) -> tuple[PanelController, list]:
    calls = []

    async def call(domain, service, target=None, data=None):
        calls.append((target["entity_id"], data["brightness_pct"]))

    ha = SimpleNamespace(state_listeners=[], states=states, call=call, state=lambda e: states.get(e, {}).get("state"))
    b = cfg.Button(key=1, entity="light.lamp", hold=hold)
    c = PanelController("test", SimpleNamespace(start_page="home", pages={"home": cfg.Page(buttons=[b])}, sleep=None),
                        {}, SimpleNamespace(deck=None), ha)
    c.draw_key = lambda _key: asyncio.sleep(0)
    return c, calls


def lamp(brightness_pct: int) -> dict:
    return {"light.lamp": {"state": "on", "attributes": {"brightness": round(brightness_pct * 255 / 100)}}}


def test_holding_dims_step_by_step_until_let_go(monkeypatch):
    monkeypatch.setattr(panel_mod, "DIM_EVERY", 0.05)

    async def go():
        c, calls = dimmer("dim", lamp(80))
        ran = []
        c.run = lambda action, _b: ran.append(action) or asyncio.sleep(0)
        await tap(c, hold=panel_mod.HOLD_TIME + 0.12)
        await asyncio.sleep(0.1)
        levels = [level for _, level in calls]
        assert levels[:2] == [70, 60] and 2 <= len(levels) <= 4   # stopped when the key was let go
        assert ran == []   # no tap after a hold
    asyncio.run(go())


def test_dim_brighten_alternates(monkeypatch):
    monkeypatch.setattr(panel_mod, "DIM_EVERY", 0.05)

    async def go():
        c, calls = dimmer("dim_brighten", lamp(80))
        await tap(c, hold=panel_mod.HOLD_TIME + 0.02)
        assert calls[0][1] == 70           # bright, so the first hold dims
        calls.clear()
        await tap(c, hold=panel_mod.HOLD_TIME + 0.02)
        assert calls[0][1] == 90           # then the next one brightens
    asyncio.run(go())


def test_dim_is_hold_only_and_needs_a_light():
    config = """
panels:
  p:
    id: sd-000001
    size: mini
    pages:
      home:
        buttons:
          - { key: 1, entity: light.a, double: dim }
          - { key: 2, entity: switch.a, hold: brighten }
          - { key: 3, lights: { area: kitchen }, hold: dim_brighten }
"""
    try:
        cfg.parse_config(config)
        raise AssertionError("expected problems")
    except cfg.ConfigError as error:
        assert len(error.problems) == 2, error.problems
        assert "long press (hold:) only" in error.problems[0]
        assert "needs a light" in error.problems[1]


def sleeping(buttons: list[cfg.Button], sleep_page: str | None) -> tuple[PanelController, list]:
    """A panel asleep on `sleep_page` (or on its own buttons if None), recording what runs."""
    pages = {"home": cfg.Page(buttons=buttons), "clock": cfg.Page()}
    sleep = cfg.Sleep(between=("21:00", "09:00"), page=sleep_page)
    c = PanelController("test", SimpleNamespace(start_page="home", pages=pages, sleep=sleep),
                        {}, SimpleNamespace(deck=None), SimpleNamespace(state_listeners=[]))
    ran = []

    async def run(action, _button):
        ran.append(action)

    async def nothing(*_args, **_kwargs):
        pass

    c.run, c.draw_key, c.draw_all, c.apply_brightness = run, nothing, nothing, nothing
    c.asleep, c._wake_page, c.page_name = True, "home", sleep_page or "home"
    return c, ran


def test_a_press_on_a_panel_asleep_on_its_own_buttons_runs():
    async def go():
        c, ran = sleeping([button(tap="toggle")], None)
        await tap(c)
        assert ran == ["toggle"] and not c.asleep
    asyncio.run(go())


def test_a_press_on_a_panel_asleep_on_a_clock_only_wakes_it():
    async def go():
        c, ran = sleeping([button(tap="toggle")], "clock")
        await tap(c)
        assert ran == [] and not c.asleep and c.page_name == "home"
        await tap(c)
        assert ran == ["toggle"]
    asyncio.run(go())


def test_an_asleep_key_runs_even_on_a_clock():
    async def go():
        c, ran = sleeping([button(tap="toggle", asleep=True)], "clock")
        await tap(c)
        assert ran == ["toggle"] and c.page_name == "home"
    asyncio.run(go())


def test_a_lost_release_doesnt_swallow_the_next_press():
    async def go():
        c, ran = sleeping([button(tap="toggle")], "clock")
        await c._on_key(0, True)   # wakes; its release never arrives
        await tap(c)
        assert ran == ["toggle"]
    asyncio.run(go())
