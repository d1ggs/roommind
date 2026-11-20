/** Far-future sentinel timestamp (year 3000): vacation active indefinitely. */
export const VACATION_SENTINEL = 32503680000;

/** Minutes until an empty room drops to eco; mirrors DEFAULT_OCCUPANCY_ECO_DELAY_MINUTES (#180). */
export const DEFAULT_OCCUPANCY_ECO_DELAY = 15;
/** Lower bound of the delay; mirrors MIN_OCCUPANCY_ECO_DELAY_MINUTES. */
export const MIN_OCCUPANCY_ECO_DELAY = 5;

/** Temperature-drop window detection defaults and ranges; mirror DEFAULT_WINDOW_DETECTION_* and the rooms/save schema. */
export const WINDOW_DETECTION_DROP = { default: 0.3, min: 0.1, max: 5.0 }; // °C
export const WINDOW_DETECTION_WINDOW = { default: 5, min: 1, max: 60 }; // minutes
export const WINDOW_DETECTION_PAUSE = { default: 60, min: 1, max: 240 }; // minutes

/** Slider default; mirrors DEFAULT_COMFORT_WEIGHT. At and above it pre-heating is not limited. */
export const DEFAULT_COMFORT_WEIGHT = 70;
/** Longest pre-heat lead; mirrors PREHEAT_MAX_LEAD_HOURS (mpc_optimizer). */
export const PREHEAT_MAX_LEAD_HOURS = 12;

/** Pre-heat lead in hours the slider allows; mirrors preheat_max_blocks() (5-min blocks). */
export function preheatLeadHours(comfortWeight: number): number {
  const hours =
    (PREHEAT_MAX_LEAD_HOURS * Math.min(Math.max(comfortWeight, 0), DEFAULT_COMFORT_WEIGHT)) /
    DEFAULT_COMFORT_WEIGHT;
  return (Math.floor((hours * 60) / 5) * 5) / 60;
}
