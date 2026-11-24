import asyncio
import datetime as dt
from types import SimpleNamespace

from renderer import config as cfg
from renderer import panel as panel_mod
from renderer.draw import render_key
from renderer.panel import PanelController, format_left, timer_left

NOW = dt.datetime(2026, 11, 23, 20, 0, 0, tzinfo=dt.timezone.utc)


def active(seconds_left: float, duration: str = "0:25:00") -> dict:
    finishes = (NOW + dt.timedelta(seconds=seconds_left)).isoformat()
    return {"state": "active", "attributes": {"duration": duration, "remaining": duration, "finishes_at": finishes}}


def test_time_left_on_a_running_timer():
    left, total = timer_left(active(24 * 60 + 13.4), NOW)
    assert round(left, 1) == 1453.4 and total == 1500
    assert format_left(left) == "24:14"   # rounds up, like a kitchen timer


def test_paused_and_idle_timers():
    assert timer_left({"state": "paused", "attributes": {"duration": "0:25:00", "remaining": "0:10:05"}}) == (605, 1500)
    assert timer_left({"state": "idle", "attributes": {"duration": "0:25:00"}}) is None
    assert timer_left(None) is None


def test_formats():
    assert format_left(1500) == "25:00"
    assert format_left(0.2) == "0:01"
    assert format_left(0) == "0:00"
    assert format_left(3723) == "1:02:03"
    assert timer_left({"state": "paused", "attributes": {"remaining": "1 day, 0:00:05"}})[0] == 86405


class FakeHA:
    def __init__(self, state: dict):
        self.states = {"timer.focus": {"entity_id": "timer.focus", **state}}
        self.calls = []
        self.state_listeners = []

    def state(self, entity_id):
        return self.states.get(entity_id, {}).get("state")

    async def call(self, domain, service, target=None, data=None):
        self.calls.append((domain, service, target) if data is None else (domain, service, target, data))


def test_tap_starts_pauses_and_resumes_a_timer():
    async def go():
        button = cfg.Button(key=1, entity="timer.focus")
        assert PanelController._default_tap(button) == "toggle"
        ha = FakeHA({"state": "idle", "attributes": {}})
        await panel_mod.perform(ha, "toggle", button)
        ha.states["timer.focus"]["state"] = "active"
        await panel_mod.perform(ha, "toggle", button)
        ha.states["timer.focus"]["state"] = "paused"
        await panel_mod.perform(ha, "toggle", button)
        await panel_mod.perform(ha, "turn_off", button)
        assert [c[1] for c in ha.calls] == ["start", "pause", "start", "cancel"]
    asyncio.run(go())


def test_a_running_timer_key_shows_the_countdown():
    ha = FakeHA(active(600))
    ha.states["timer.focus"]["attributes"]["finishes_at"] = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=600)).isoformat()
    button = cfg.Button(key=1, entity="timer.focus", text="Focus", icon="mdi:timer-outline")
    c = PanelController("test", SimpleNamespace(start_page="home", pages={"home": cfg.Page(buttons=[button])}, sleep=None),
                        {}, SimpleNamespace(deck=None), ha)
    look = c.look_for(button)
    assert look.countdown == "10:00" and 0.39 < look.progress <= 0.4
    image = render_key(look, 120)   # draws without error
    assert image.size == (120, 120)
    ha.states["timer.focus"] = {"entity_id": "timer.focus", "state": "idle", "attributes": {}}
    assert c.look_for(button).countdown == ""


def test_a_dial_on_an_idle_timer_sets_the_next_length():
    from renderer import dials
    dials.TIMER_LENGTHS.clear()
    idle = {"state": "idle", "attributes": {"duration": "0:25:00"}}
    reading = dials.read("timer.focus", idle)
    assert reading.value == 25 and reading.text == "25 min" and reading.level() is None
    assert reading.after(25, 5) == 30 and reading.after(2, -5) == 1
    dials.TIMER_LENGTHS["timer.focus"] = 45 * 60
    assert dials.read("timer.focus", idle).text == "45 min"

    async def go():
        ha = FakeHA({"state": "idle", "attributes": {"duration": "0:25:00"}})
        await panel_mod.perform(ha, "toggle", cfg.Button(key=1, entity="timer.focus"))
        assert ha.calls == [("timer", "start", {"entity_id": "timer.focus"}, {"duration": 2700})]
    asyncio.run(go())
    dials.TIMER_LENGTHS.clear()


def test_a_dial_on_a_running_timer_adds_whole_minutes():
    from renderer import dials
    state = active(10 * 60 + 20, "0:25:00")
    reading = dials.read("timer.focus", {**state, "attributes": {**state["attributes"],
                         "finishes_at": (dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=620)).isoformat()}})
    assert reading.text == "10:20" and reading.level() == 39
    assert reading.after(reading.value, 1) == 11 and reading.after(reading.value, 30) == 40   # past the old length
    assert reading.after(reading.value, -20) == 1   # never down to 0
    assert dials.call_for("timer.focus", 11) == ("timer", "start", {"duration": 660})


def test_countdown_can_be_turned_off():
    ha = FakeHA(active(600))
    ha.states["timer.focus"]["attributes"]["finishes_at"] = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=600)).isoformat()
    button = cfg.Button(key=1, entity="timer.focus", text="Focus", countdown=False)
    c = PanelController("test", SimpleNamespace(start_page="home", pages={"home": cfg.Page(buttons=[button])}, sleep=None),
                        {}, SimpleNamespace(deck=None), ha)
    assert c.look_for(button).countdown == ""
