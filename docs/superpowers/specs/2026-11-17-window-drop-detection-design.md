# Open-window detection from a temperature drop

Date: 2026-11-17. Status: waiting for approval.

## Why

RoomMind pauses a room only when a contact sensor reports an open window. This home has no
contact sensors, and with the tado cloud blocked nothing acts on the valves' own detection.
The heating therefore keeps running while a window is open.

## Goal

A room with a room temperature sensor pauses its heating by itself when the temperature falls
fast, as it does when a window opens, and resumes after a set time.

Non-goals: contact sensors (already supported), notifications, humidity-based detection,
rooms without a room temperature sensor, any change to the tado devices or the bridge.

Must not happen: a room that stays paused for good, or a pause that fires in normal operation
(a radiator that switches off, a schedule that drops to eco).

## Evidence

Ten days of history (7–17 Nov 2026) from the room sensors in use:

| Sensor | Typical update | Falls of 0.3 C or more within 5 min |
|---|---|---|
| Govee bedroom | 10 s | 1 |
| Govee study | 6 s | 2 |
| Govee bathroom | 4 s | 1 |
| ESP-AirMate living room | 60 s | 6 |
| tado wall thermostat, playroom | 19 min | 1 |

A fall of this size is rare, so the rule separates it from normal cooling. The playroom sensor
updates too slowly for a 5-minute rule: detection there is weak until the room has a faster
sensor. Which of these events were real windows is not known; the first days of use will tell.

The rule follows the detector in the tado valve firmware (a fall of 0.2 to 0.3 C between
consecutive readings), moved to the room sensor and expressed over time instead of over
samples, because the room sensors report at different rates.

## Approaches

1. **Detector inside RoomMind (chosen).** A per-room detector feeds the existing window pause.
   One place to configure, shows in the RoomMind panel, uses the pause path that already
   exists and is tested.
2. Template binary sensor in Home Assistant per room, added to the room as a window sensor.
   No fork change, but the hold time and the reset after a pause are awkward in a template,
   and the logic lives outside RoomMind in six copies.
3. Freedo and the valves' own detection. Needs new hardware and a re-pair of every device, and
   no valve has been seen detecting a window. Set aside with the bridge replacement.

## Design

- **Room options** (stored with the room, editable in the room's window section of the panel):
  - `window_detection_enabled`, default off.
  - `window_detection_drop`, default 0.3 C.
  - `window_detection_window`, default 5 min: the time span the fall is measured over.
  - `window_detection_pause`, default 15 min: how long the room stays paused.
- **Detector** (new class beside the window manager, pure logic, no Home Assistant calls):
  - Keeps the room temperature samples of the last `window_detection_window` minutes, one per
    control cycle (30 s).
  - Fires when the highest sample in that span minus the newest is at least the drop.
  - On firing, the room counts as window open for the pause time. When the pause ends, the
    samples are cleared, so the colder room does not fire again by itself. A new fall after
    that fires again.
  - A missing or stale room temperature clears the samples. Nothing fires without at least
    two samples.
  - State is kept in memory only. After a restart the detector starts empty.
- **Effect:** while detected, the room behaves exactly as with an open contact sensor: idle,
  no model training, `window_open: true`. A new attribute `window_detected: true` tells the
  two sources apart. The open and close delays of contact sensors do not apply to a detection.
- **Ending a pause early:** switching the option off for the room ends it. No separate reset
  action in this version.
- **Panel:** a switch and three number fields in the room settings, with English and Italian
  texts. The room card shows the window state as it does today.
- **Tests:** unit tests for the detector (fires, does not fire on slow cooling, hold and
  re-arm, stale data, disabled), and coordinator tests that a detection idles the room and
  sets both attributes.

## Left to the builder

File and class names, the exact panel layout, how samples are stored.

## Rollout

Fork branch from `trv-learned-offset-1.8.1`, release `v1.8.1-trvoffset.3` through HACS, then
the option switched on for bedroom, study, bathroom and living room. The playroom and kitchen
stay off. The release and the Home Assistant restart need the user's go.
