"""Tests for WindowDropDetector."""

from __future__ import annotations

from custom_components.roommind.managers.window_drop_detector import WindowDropDetector

ROOM = "living_room"
STEP = 30.0


def _update(det: WindowDropDetector, temp: float | None, now: float, *, area_id: str = ROOM, **kwargs) -> bool:
    params = {"enabled": True, "drop": 0.3, "window_s": 300, "pause_s": 900}
    params.update(kwargs)
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
