import datetime as dt
import time
from types import SimpleNamespace

from renderer.config import Sleep
from renderer.panel import PanelController


def controller(sleep: Sleep, hour: int, idle: float) -> PanelController:
    """Just enough of a PanelController to ask whether it should sleep."""
    c = object.__new__(PanelController)
    c.panel = SimpleNamespace(sleep=sleep)
    c.ha = SimpleNamespace(now=lambda: dt.datetime(2026, 10, 29, hour, 0), states={})
    c._last_activity = time.monotonic() - idle
    return c


def test_woken_in_sleep_hours_sleeps_again_after_awake_seconds():
    night = Sleep(between=("21:00", "09:00"), awake_seconds=120)
    assert not controller(night, 23, idle=100)._should_sleep()
    assert controller(night, 23, idle=130)._should_sleep()
    assert controller(Sleep(between=("21:00", "09:00")), 23, idle=61)._should_sleep()  # 60 s by default


def test_after_minutes_is_for_outside_the_sleep_hours():
    both = Sleep(after_minutes=10, between=("21:00", "09:00"), awake_seconds=30)
    assert controller(both, 23, idle=31)._should_sleep()        # night: awake_seconds
    assert not controller(both, 12, idle=300)._should_sleep()   # day: not yet 10 minutes
    assert controller(both, 12, idle=601)._should_sleep()
    assert not controller(Sleep(between=("21:00", "09:00")), 12, idle=10_000)._should_sleep()
