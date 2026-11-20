"""Open-window detection from a fast fall of the room temperature."""

from __future__ import annotations

import logging
import time
from collections import deque

_LOGGER = logging.getLogger(__name__)

# Tolerance so a fall that equals the threshold still fires despite float rounding.
_EPSILON = 1e-9


class WindowDropDetector:
    """Detects an open window per room from a fast temperature fall.

    Keeps the room temperature samples of the last ``window_s`` seconds and fires
    when the highest of them minus the newest reaches ``drop``.  After firing the
    room counts as window open for at most ``pause_s`` seconds.  With ``recovery``
    set the pause ends earlier, as soon as the room is that much warmer than its
    lowest reading since the detection: heating is off during the pause, so such
    a rise means the window was closed.  When the pause ends the samples start
    over, so the colder room does not fire again by itself.
    """

    def __init__(self) -> None:
        self._samples: dict[str, deque[tuple[float, float]]] = {}
        self._active_until: dict[str, float] = {}
        self._lowest: dict[str, float] = {}
        self._cap_reached: set[str] = set()

    def update(
        self,
        area_id: str,
        temp: float | None,
        *,
        enabled: bool,
        drop: float,
        window_s: float,
        pause_s: float,
        recovery: float | None = None,
        now: float | None = None,
    ) -> bool:
        """Feed one sample; return True while the room counts as window open."""
        if not enabled:
            self.remove_room(area_id)
            return False

        if now is None:
            now = time.time()

        active_until = self._active_until.get(area_id)
        if active_until is not None:
            # Bound the pause by the current setting from now, so a lowered pause or
            # a wall clock stepping back cannot stretch it past pause_s.
            active_until = min(active_until, now + pause_s)
            self._active_until[area_id] = active_until
            recovered = False
            if now < active_until:
                if temp is None or recovery is None:
                    return True
                lowest = min(self._lowest.get(area_id, temp), temp)
                self._lowest[area_id] = lowest
                if temp - lowest < recovery - _EPSILON:
                    return True
                recovered = True
            self._active_until.pop(area_id)
            self._samples.pop(area_id, None)
            self._lowest.pop(area_id, None)
            if recovered:
                _LOGGER.info("Room %s: temperature recovered, treating window as closed", area_id)
            else:
                self._cap_reached.add(area_id)
                _LOGGER.info("Room %s: detected open window pause ended", area_id)

        samples = self._samples.setdefault(area_id, deque())
        if temp is None:
            samples.clear()
            return False

        samples.append((now, temp))
        cutoff = now - window_s
        while samples and samples[0][0] < cutoff:
            samples.popleft()

        if len(samples) < 2:
            return False
        fall = max(t for _, t in samples) - temp
        if fall < drop - _EPSILON:
            return False

        self._active_until[area_id] = now + pause_s
        self._lowest[area_id] = temp
        samples.clear()
        _LOGGER.info(
            "Room %s: temperature fell %.2f C within %ds, treating window as open for up to %ds",
            area_id,
            fall,
            window_s,
            pause_s,
        )
        return True

    def is_active(self, area_id: str) -> bool:
        """Return True while a detected pause runs for the room.

        Time-aware, so it turns False at the pause end even when ``update`` is no
        longer called; the recorded pause itself is cleared by the next ``update``.
        """
        until = self._active_until.get(area_id)
        return until is not None and time.time() < until

    def pop_cap_reached(self, area_id: str) -> bool:
        """Return True once after a pause ran its full length without a recovery.

        That is a window still open, or a room that does not warm back up by
        itself; either way heating resumes and someone should know.
        """
        if area_id in self._cap_reached:
            self._cap_reached.discard(area_id)
            return True
        return False

    def remove_room(self, area_id: str) -> None:
        """Clean up state for a removed room."""
        self._samples.pop(area_id, None)
        self._active_until.pop(area_id, None)
        self._lowest.pop(area_id, None)
        self._cap_reached.discard(area_id)
