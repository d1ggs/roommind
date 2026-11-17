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
    room counts as window open for ``pause_s`` seconds; when that pause ends the
    samples start over, so the colder room does not fire again by itself.
    """

    def __init__(self) -> None:
        self._samples: dict[str, deque[tuple[float, float]]] = {}
        self._active_until: dict[str, float] = {}

    def update(
        self,
        area_id: str,
        temp: float | None,
        *,
        enabled: bool,
        drop: float,
        window_s: float,
        pause_s: float,
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
            if now < active_until:
                return True
            self._active_until.pop(area_id)
            self._samples.pop(area_id, None)

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
        samples.clear()
        _LOGGER.info(
            "Room %s: temperature fell %.2f C within %ds, treating window as open for %ds",
            area_id,
            fall,
            window_s,
            pause_s,
        )
        return True

    def is_active(self, area_id: str) -> bool:
        """Return True while a detected pause is recorded for the room.

        The pause is cleared by the first ``update`` at or after its end.
        """
        return area_id in self._active_until

    def remove_room(self, area_id: str) -> None:
        """Clean up state for a removed room."""
        self._samples.pop(area_id, None)
        self._active_until.pop(area_id, None)
