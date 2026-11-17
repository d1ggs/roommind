# Open-window detection from a temperature drop: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A room with a room temperature sensor pauses itself for a set time when its temperature falls fast.

**Architecture:** A pure-logic `WindowDropDetector` (one instance on the coordinator, per-room state in memory) sits beside `WindowManager`. Each control cycle the coordinator feeds it the raw room temperature; while it reports active, the coordinator treats the room exactly as it treats an open contact sensor. Four new room options travel through the store, the websocket API and the panel.

**Tech Stack:** Python 3.14 (Home Assistant custom component), pytest, TypeScript + Lit frontend (vite).

**Spec:** `docs/superpowers/specs/2026-11-17-window-drop-detection-design.md`

## Global Constraints

- Branch `window-drop-detection` (already created from `trv-learned-offset-1.8.1`). Commit only files you changed for your task: `git add <paths>`, never `git add -A`. The working tree holds unrelated uncommitted files under `dev/sim/`; leave them alone.
- Tests: `.venv/bin/pytest -o asyncio_mode=auto <path>` from the repo root.
- Option names and defaults, exact: `window_detection_enabled` (bool, `False`), `window_detection_drop` (float C, `0.3`, allowed 0.1 to 5.0), `window_detection_window` (int minutes, `5`, allowed 1 to 60), `window_detection_pause` (int minutes, `15`, allowed 1 to 240).
- The contact-sensor path (`WindowManager`, `window_open_delay`, `window_close_delay`) keeps its behaviour. `WindowManager` is not modified.
- No push, no release, no version bump, no change on Home Assistant: the controller does those.
- Commit messages: conventional (`feat:`, `test:`), ending with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Review Focus

- Room temperature missing or stale for some cycles in the middle of a fall: no detection from samples on both sides of the gap (Task 1 test).
- Option switched off while a pause runs: the pause ends in the same cycle (Task 1 test).
- Detection while a contact sensor is also open, and contact sensor closing during a detection pause: room stays idle until both are clear (Task 2 test).
- Room without an external temperature sensor with the option on: never detects, since the valve's own reading falls when the valve closes (Task 2 test).
- Options changed while samples are stored (shorter window): old samples outside the new window are dropped, no crash (Task 1 test).

---

### Task 1: Detector

**Files:**
- Create: `custom_components/roommind/managers/window_drop_detector.py`
- Test: `tests/managers/test_window_drop_detector.py`

**Interfaces:**
- Produces:

```python
class WindowDropDetector:
    def update(self, area_id: str, temp: float | None, *, enabled: bool,
               drop: float, window_s: float, pause_s: float, now: float | None = None) -> bool:
        """Feed one sample; return True while the room counts as window open."""
    def is_active(self, area_id: str) -> bool: ...
    def remove_room(self, area_id: str) -> None: ...
```

Behaviour, in this order inside `update` (`now` defaults to `time.time()`):
1. `enabled` false: drop all state of the room, return False.
2. A pause is running (`now < active_until`): store no sample, return True.
3. A pause just ended: clear the samples, forget the pause, continue.
4. `temp` is None: clear the samples, return False.
5. Append `(now, temp)`, drop samples older than `now - window_s`.
6. With at least two samples, if `max(sample temps) - temp >= drop - 1e-9`: set `active_until = now + pause_s`, clear the samples, log at INFO with room, fall and pause, return True. Else False.

- [ ] **Step 1: Write failing tests** in `tests/managers/test_window_drop_detector.py`, each driving `update` with explicit `now` values 30 s apart and defaults `drop=0.3, window_s=300, pause_s=900`:
  - `test_fast_fall_fires`: 20.0, 19.9, 19.8, 19.7 → last call True, `is_active` True.
  - `test_slow_cooling_does_not_fire`: 0.05 C lower every 60 s for 30 min → always False.
  - `test_single_sample_does_not_fire`.
  - `test_pause_holds_then_rearms`: after firing at t, True at t+899; at t+900 with the lower temperature False; a later fresh fall of 0.3 fires again.
  - `test_cold_level_after_pause_does_not_refire`: after the pause, steady readings 1 C below the pre-fall level stay False.
  - `test_none_clears_samples`: 20.0, None, 19.6 → False.
  - `test_disabled_ends_pause`: fire, then `enabled=False` → False and `is_active` False; enabling again starts empty.
  - `test_shorter_window_drops_old_samples`: samples over 5 min, then `window_s=60` with a fall that only exists against the old samples → False.
  - `test_rooms_are_independent` and `test_remove_room`.
- [ ] **Step 2:** Run `.venv/bin/pytest -o asyncio_mode=auto tests/managers/test_window_drop_detector.py`; expect import failure.
- [ ] **Step 3:** Implement the class as specified.
- [ ] **Step 4:** Run the same command; expect all pass.
- [ ] **Step 5:** Commit `feat: add temperature-drop window detector`.

### Task 2: Options, coordinator wiring, attributes

**Files:**
- Modify: `custom_components/roommind/const.py` (four `DEFAULT_WINDOW_DETECTION_*` constants)
- Modify: `custom_components/roommind/store.py` (room defaults near line 258)
- Modify: `custom_components/roommind/websocket_api.py` (allowed keys near line 124, schema near line 424, room payload near line 301)
- Modify: `custom_components/roommind/coordinator.py` (detector instance; wiring after the window block near line 888 to 910; live state near lines 678 and 1631; room removal next to `self._window_manager.remove_room`)
- Modify: `custom_components/roommind/climate.py` (attributes near line 532)
- Modify: `custom_components/roommind/diagnostics.py` (window state builder, add the detector's active flag)
- Modify: `README.md` (feature list line under Window/Door Pause; attributes line)
- Test: `tests/coordinator/test_window_drop_detection.py`, plus additions to `tests/test_store_save_room.py`, `tests/test_websocket_api.py`, `tests/test_climate.py`

**Interfaces:**
- Consumes: `WindowDropDetector` from Task 1.
- Produces: room config keys from Global Constraints; live room state key `window_detected: bool`; climate attribute `window_detected: bool`.

Wiring rule in the coordinator, after the existing `window_open` / `window_pending` computation and before `if window_open or window_pending:`:

```python
detected = has_external_sensor and self._window_drop_detector.update(
    area_id, current_temp_raw,
    enabled=room.get("window_detection_enabled", DEFAULT_WINDOW_DETECTION_ENABLED),
    drop=..., window_s=minutes * 60, pause_s=minutes * 60)
if detected:
    raw_open = True
    window_open = True
```

When `has_external_sensor` is false, call `update(..., enabled=False)` so a stale pause cannot survive a sensor removal. `window_detected` goes into the live state wherever `window_open` does (False in the early-return state near line 678). Thread it to `_build_room_state` the way `window_open` is threaded.

Websocket schema: `vol.Optional("window_detection_enabled"): bool`, `window_detection_drop`: `vol.All(vol.Coerce(float), vol.Range(min=0.1, max=5.0))`, `window_detection_window`: `vol.All(vol.Coerce(int), vol.Range(min=1, max=60))`, `window_detection_pause`: `vol.All(vol.Coerce(int), vol.Range(min=1, max=240))`.

- [ ] **Step 1: Write failing tests.** Model the coordinator tests on `tests/coordinator/test_window_pause.py` (same fixtures and helpers):
  - `test_fast_fall_idles_room`: option on, room sensor readings fall 0.4 C across cycles → live state `mode` idle, `window_open` True, `window_detected` True.
  - `test_option_off_does_not_detect`: same readings, option absent → `window_open` False, `window_detected` False.
  - `test_no_external_sensor_never_detects`: option on, room without temperature sensor, device temperature falling → `window_detected` False.
  - `test_contact_and_detection_combine`: contact sensor open plus a detection → idle; contact closes during the pause → still idle, `window_detected` True; after the pause and contact closed → control resumes.
  - `test_detection_skips_model_training`: during a detection the EKF training call receives `window_open=True` (assert the same way the existing window tests do).
  - Store: a saved room without the keys loads with the four defaults; saved values round-trip.
  - Websocket: `roommind/rooms/save` accepts the four keys; rejects `window_detection_drop: 0`, `window_detection_window: 0`, `window_detection_pause: 500`.
  - Climate: `extra_state_attributes["window_detected"]` follows the live state.
- [ ] **Step 2:** Run the four test files; expect the new tests to fail.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** Run `.venv/bin/pytest -o asyncio_mode=auto tests -q`; expect the whole suite to pass (3164 tests passed before this work, plus the new ones).
- [ ] **Step 5:** Commit `feat: pause a room on a fast temperature drop`.

### Task 3: Panel

**Files:**
- Modify: `frontend/src/types/index.ts` (room config type: four optional fields)
- Modify: `frontend/src/components/rs-sensor-section.ts` and `frontend/src/components/rs-room-detail.ts` (follow how `window_open_delay` flows from the form to the save call)
- Modify: `frontend/src/locales/en.json`, `it.json`, `de.json`, `fr.json`

**Interfaces:**
- Consumes: the four room config keys and their ranges from Global Constraints.

Layout: inside the window part of the sensor section, below the open and close delay fields, shown whether or not contact sensors are configured: a switch "Detect an open window from a fast temperature drop", and, only while the switch is on, three number fields with units (C, min, min) limited to the allowed ranges, plus one helper line. The fields show the defaults when the room has no stored value. If the room has no room temperature sensor, the switch is disabled with a hint.

Texts (keys follow the naming next to the existing window delay keys):

| | en | it |
|---|---|---|
| switch | Detect an open window from a fast temperature drop | Rileva una finestra aperta da un calo rapido di temperatura |
| drop | Temperature drop | Calo di temperatura |
| window | Within | Entro |
| pause | Pause heating for | Sospendi il riscaldamento per |
| helper | The room pauses when its sensor falls by this amount within this time, then resumes after the pause. | La stanza va in pausa quando il sensore scende di questo valore entro questo tempo, poi riprende dopo la pausa. |
| no sensor hint | Needs a room temperature sensor. | Richiede un sensore di temperatura della stanza. |

Write natural German and French for the same six strings.

- [ ] **Step 1:** Implement the fields and texts.
- [ ] **Step 2:** In `frontend/`: `npm run lint` if the script exists, then `npm run build`; expect both to pass with no TypeScript errors. If the repo has frontend unit tests (`npm test`), run them.
- [ ] **Step 3:** Check with `git status` whether the build output is tracked. Commit the source files, and the built bundle only if the repository tracks it. Message: `feat: panel options for temperature-drop window detection`.
