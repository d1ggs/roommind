"""Tests for WindowDropDetector."""

from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest

from custom_components.roommind.managers import window_drop_detector as detector_module
from custom_components.roommind.managers.window_drop_detector import WindowDropDetector

ROOM = "living_room"
STEP = 30.0

# The detector's wall clock; each _update moves it to that update's ``now`` so
# is_active is evaluated at the time of the last update unless a test moves it.
_clock = SimpleNamespace(now=0.0)


@pytest.fixture(autouse=True)
def _fake_clock(monkeypatch):
    _clock.now = 0.0
    monkeypatch.setattr(detector_module, "time", SimpleNamespace(time=lambda: _clock.now))


def _update(det: WindowDropDetector, temp: float | None, now: float, *, area_id: str = ROOM, **kwargs) -> bool:
    params = {"enabled": True, "drop": 0.3, "window_s": 300, "pause_s": 900}
    params.update(kwargs)
    _clock.now = now
    return det.update(area_id, temp, now=now, **params)


def _feed(det: WindowDropDetector, temps: list[float | None], start: float = 1000.0, **kwargs) -> list[bool]:
    """Feed temps 30 s apart starting at *start*; return each result."""
    return [_update(det, t, start + i * STEP, **kwargs) for i, t in enumerate(temps)]


def test_fast_fall_fires():
    """A 0.3 C fall within the window fires on the sample that completes it."""
    det = WindowDropDetector()
    results = _feed(det, [20.0, 19.9, 19.8, 19.7])
    assert results == [False, False, False, True]
    assert det.is_active(ROOM) is True


def test_slow_cooling_does_not_fire():
    """0.05 C lower every 60 s for 30 min never reaches 0.3 C within 5 min."""
    det = WindowDropDetector()
    for i in range(31):
        assert _update(det, 20.0 - 0.05 * i, 1000.0 + i * 60) is False
    assert det.is_active(ROOM) is False


def test_single_sample_does_not_fire():
    """One sample cannot fire, even with a zero drop threshold."""
    det = WindowDropDetector()
    assert _update(det, 15.0, 1000.0, drop=0.0) is False
    assert det.is_active(ROOM) is False


def test_pause_holds_then_rearms():
    """The pause holds for pause_s, ends without refiring, and a fresh fall fires again."""
    det = WindowDropDetector()
    _feed(det, [20.0, 19.9, 19.8, 19.7])
    t = 1000.0 + 3 * STEP

    assert _update(det, 19.0, t + 899) is True
    assert det.is_active(ROOM) is True

    assert _update(det, 19.0, t + 900) is False
    assert det.is_active(ROOM) is False

    results = _feed(det, [19.0, 18.9, 18.8, 18.7], start=t + 900 + STEP)
    assert results == [False, False, False, True]
    assert det.is_active(ROOM) is True


def test_cold_level_after_pause_does_not_refire():
    """After the pause, steady readings 1 C below the pre-fall level stay False."""
    det = WindowDropDetector()
    _feed(det, [20.0, 19.9, 19.8, 19.7])
    t = 1000.0 + 3 * STEP

    results = _feed(det, [19.0] * 20, start=t + 900)
    assert results == [False] * 20
    assert det.is_active(ROOM) is False


def test_none_clears_samples():
    """A missing temperature clears the samples, so the fall across it is not seen."""
    det = WindowDropDetector()
    assert _feed(det, [20.0, None, 19.6]) == [False, False, False]
    assert det.is_active(ROOM) is False


def test_disabled_ends_pause():
    """Disabling ends a running pause; enabling again starts with no samples."""
    det = WindowDropDetector()
    _feed(det, [20.0, 19.9, 19.8, 19.7])
    t = 1000.0 + 3 * STEP

    assert _update(det, 19.7, t + STEP, enabled=False) is False
    assert det.is_active(ROOM) is False

    # A sample from before the disable would make this a 0.7 C fall; empty start means no fire.
    assert _update(det, 19.0, t + 2 * STEP) is False
    assert det.is_active(ROOM) is False


def test_shorter_window_drops_old_samples():
    """Shrinking the window drops samples older than the new span."""
    det = WindowDropDetector()
    # 20.0 at 1000 s, then 19.9 flat until 1270 s: no fire over the 5 min window.
    results = _feed(det, [20.0] + [19.9] * 9)
    assert results == [False] * 10

    # 19.7 is 0.3 below the 20.0 sample from 300 s ago, but only 0.2 below the last 60 s.
    assert _update(det, 19.7, 1300.0, window_s=60) is False
    assert det.is_active(ROOM) is False


def test_rooms_are_independent():
    """A fall in one room does not affect another."""
    det = WindowDropDetector()
    for i, (a, b) in enumerate([(20.0, 21.0), (19.9, 21.0), (19.8, 21.0), (19.7, 21.0)]):
        now = 1000.0 + i * STEP
        res_a = _update(det, a, now, area_id="room_a")
        res_b = _update(det, b, now, area_id="room_b")
    assert res_a is True
    assert res_b is False
    assert det.is_active("room_a") is True
    assert det.is_active("room_b") is False


def test_remove_room():
    """remove_room drops the pause and the samples of that room only."""
    det = WindowDropDetector()
    _feed(det, [20.0, 19.9, 19.8, 19.7], area_id="room_a")
    _feed(det, [21.0, 20.9], area_id="room_b")

    det.remove_room("room_a")
    assert det.is_active("room_a") is False
    # No old samples left: a lower reading right after removal does not fire.
    assert _update(det, 19.0, 2000.0, area_id="room_a") is False
    # Room b kept its samples: 21.0 -> 20.7 within the window fires.
    assert _update(det, 20.7, 1000.0 + 2 * STEP, area_id="room_b") is True


def test_remove_unknown_room_is_noop():
    """Removing a room the detector never saw does not raise."""
    det = WindowDropDetector()
    det.remove_room("ghost")
    assert det.is_active("ghost") is False


def test_is_active_ends_without_update():
    """is_active turns False once the pause end passes, even with no further update."""
    det = WindowDropDetector()
    _feed(det, [20.0, 19.9, 19.8, 19.7])
    t = 1000.0 + 3 * STEP

    _clock.now = t + 899
    assert det.is_active(ROOM) is True
    _clock.now = t + 900
    assert det.is_active(ROOM) is False


def test_lowered_pause_bounds_running_pause():
    """Lowering pause_s during a pause ends it pause_s after the next update."""
    det = WindowDropDetector()
    _feed(det, [20.0, 19.9, 19.8, 19.7], pause_s=14400)
    t1 = 1000.0 + 3 * STEP + 60

    assert _update(det, 19.7, t1, pause_s=900) is True
    assert _update(det, 19.7, t1 + 899, pause_s=900) is True
    assert det.is_active(ROOM) is True
    assert _update(det, 19.7, t1 + 900, pause_s=900) is False
    assert det.is_active(ROOM) is False


def test_clock_stepping_back_bounds_pause():
    """A wall clock stepping back a day does not stretch the pause."""
    det = WindowDropDetector()
    _feed(det, [20.0, 19.9, 19.8, 19.7], start=100000.0)
    t = 100000.0 + 3 * STEP
    back = t - 86400

    assert _update(det, 19.7, back) is True
    assert _update(det, 19.7, back + 899) is True
    assert _update(det, 19.7, back + 900) is False
    assert det.is_active(ROOM) is False


def test_pause_end_is_logged(caplog):
    """The end of a pause is logged at INFO with the room."""
    det = WindowDropDetector()
    _feed(det, [20.0, 19.9, 19.8, 19.7])
    t = 1000.0 + 3 * STEP

    with caplog.at_level(logging.INFO, logger=detector_module.__name__):
        _update(det, 19.7, t + 900)

    ended = [r for r in caplog.records if r.levelno == logging.INFO and "ended" in r.getMessage()]
    assert len(ended) == 1
    assert ROOM in ended[0].getMessage()


# --- Recovery: the pause ends early once the room warms back up ---


def _fire(det: WindowDropDetector) -> float:
    """Fire a detection ending at 19.7 C; return the time of the firing sample."""
    assert _feed(det, [20.0, 19.9, 19.8, 19.7], recovery=0.3)[-1] is True
    return 1000.0 + 3 * STEP


def test_recovery_ends_pause_early():
    """A rise of ``recovery`` above the lowest reading since the detection ends the pause."""
    det = WindowDropDetector()
    t = _fire(det)
    assert _update(det, 19.2, t + 60, recovery=0.3) is True
    assert _update(det, 19.4, t + 120, recovery=0.3) is True
    assert _update(det, 19.5, t + 180, recovery=0.3) is False
    assert det.is_active(ROOM) is False


def test_recovery_measured_from_lowest_reading():
    """The room keeps falling after the detection; recovery counts from the bottom."""
    det = WindowDropDetector()
    t = _fire(det)
    assert _update(det, 18.5, t + 60, recovery=0.3) is True
    assert _update(det, 18.0, t + 120, recovery=0.3) is True
    assert _update(det, 18.2, t + 180, recovery=0.3) is True
    assert _update(det, 18.3, t + 240, recovery=0.3) is False


def test_no_recovery_holds_until_cap():
    """A room that stays cold is paused for the full pause_s, then released."""
    det = WindowDropDetector()
    t = _fire(det)
    assert _update(det, 19.0, t + 60, recovery=0.3) is True
    assert _update(det, 19.1, t + 899, recovery=0.3) is True
    assert _update(det, 19.1, t + 900, recovery=0.3) is False


def test_missing_temperature_keeps_pause_and_lowest():
    """A sample without temperature neither ends the pause nor resets the lowest reading."""
    det = WindowDropDetector()
    t = _fire(det)
    assert _update(det, 19.0, t + 60, recovery=0.3) is True
    assert _update(det, None, t + 120, recovery=0.3) is True
    assert _update(det, 19.2, t + 180, recovery=0.3) is True
    assert _update(det, 19.3, t + 240, recovery=0.3) is False


def test_fresh_fall_after_recovery_fires_again():
    """After a recovery the detector is armed again and a new fall fires."""
    det = WindowDropDetector()
    t = _fire(det)
    assert _update(det, 20.0, t + 60, recovery=0.3) is False
    results = _feed(det, [20.0, 19.9, 19.8, 19.7], start=t + 90, recovery=0.3)
    assert results[-1] is True


def test_recovery_sample_does_not_refire_by_itself():
    """The colder samples from the pause are gone, so the warm sample starts a fresh window."""
    det = WindowDropDetector()
    t = _fire(det)
    assert _update(det, 19.0, t + 60, recovery=0.3) is True
    assert _update(det, 19.3, t + 120, recovery=0.3) is False
    assert _update(det, 19.3, t + 150, recovery=0.3) is False


def test_without_recovery_argument_pause_is_fixed():
    """Callers that pass no recovery keep the fixed pause."""
    det = WindowDropDetector()
    _feed(det, [20.0, 19.9, 19.8, 19.7])
    t = 1000.0 + 3 * STEP
    assert _update(det, 21.0, t + 60) is True


# --- Cap reached: reported once so the coordinator can tell the user ---


def test_cap_reached_reported_once():
    det = WindowDropDetector()
    t = _fire(det)
    assert _update(det, 19.0, t + 899, recovery=0.3) is True
    assert det.pop_cap_reached(ROOM) is False
    assert _update(det, 19.0, t + 900, recovery=0.3) is False
    assert det.pop_cap_reached(ROOM) is True
    assert det.pop_cap_reached(ROOM) is False


def test_recovery_does_not_report_cap():
    det = WindowDropDetector()
    t = _fire(det)
    assert _update(det, 20.0, t + 60, recovery=0.3) is False
    assert det.pop_cap_reached(ROOM) is False


def test_disabling_does_not_report_cap():
    det = WindowDropDetector()
    t = _fire(det)
    assert _update(det, 19.0, t + 60, enabled=False) is False
    assert det.pop_cap_reached(ROOM) is False
