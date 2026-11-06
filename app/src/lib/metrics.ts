/**
 * Status-bar metrics of a running session: level meter, latency, warnings.
 *
 * Role: pure, time-injected state machine between the engine's `metrics`
 * events (about once a second; competitive-features.md F4) and what the main
 * window shows next to the engine status:
 *
 * - a small input level meter (dBFS mapped onto a bar) and the median
 *   latency from end of speech to the final subtitle;
 * - "recognition can't keep up" once more than `BACKLOG_WARN_ABOVE` complete
 *   sentences wait for the final pass. It clears only when the backlog is back
 *   to `BACKLOG_CLEAR_AT` or less, so a backlog hovering around the threshold
 *   does not make the warning blink;
 * - "no audio" after `NO_AUDIO_MS` of input below `NO_AUDIO_DBFS` while the
 *   session runs; any louder reading resets the clock.
 *
 * The caller passes the current time (`now`, ms) to every step and re-checks
 * at `noAudioDueAt` (metrics may stop arriving, the hint must still appear on
 * time). A warning the user closed stays closed until its condition clears.
 */

import type { EngineState, MetricsEvent } from "./types";

/** Bottom of the meter: quieter input shows an empty bar. */
export const METER_FLOOR_DBFS = -60;
export const NO_AUDIO_DBFS = -60;
export const NO_AUDIO_MS = 15_000;
export const BACKLOG_WARN_ABOVE = 3;
export const BACKLOG_CLEAR_AT = 1;

export type MetricsWarning = "backlog" | "no_audio";

export interface MetricsState {
  readonly sessionId: number | null;
  readonly running: boolean;
  /** Last level reading; null until the first `metrics` event of the session. */
  readonly levelDbfs: number | null;
  readonly latencyMedianMs: number | null;
  readonly backlog: number;
  /** When the input went (and stayed) below `NO_AUDIO_DBFS`; null while there is sound. */
  readonly quietSince: number | null;
  readonly backlogHigh: boolean;
  /** Warnings closed by the user (until their condition clears). */
  readonly dismissed: ReadonlySet<MetricsWarning>;
}

export function emptyMetrics(): MetricsState {
  return {
    sessionId: null,
    running: false,
    levelDbfs: null,
    latencyMedianMs: null,
    backlog: 0,
    quietSince: null,
    backlogHigh: false,
    dismissed: new Set(),
  };
}

/**
 * The session state changed. Leaving "running" forgets everything (the next
 * session starts with an empty meter and no warnings); a new session id does too.
 */
export function metricsSessionState(state: MetricsState, session: EngineState, sessionId: number | null): MetricsState {
  const running = session === "running";
  const sameSession = sessionId === null || sessionId === state.sessionId;
  if (running && state.running && sameSession) return state;
  if (!running && !state.running) return state;
  return { ...emptyMetrics(), running, sessionId: sessionId ?? state.sessionId };
}

/** Applies one `metrics` event received at `now`. */
export function applyMetrics(state: MetricsState, ev: MetricsEvent, now: number): MetricsState {
  if (!state.running) return state;
  // A late reading from a session that already ended.
  if (ev.session_id !== null && state.sessionId !== null && ev.session_id < state.sessionId) return state;
  const quiet = ev.level_dbfs < NO_AUDIO_DBFS;
  const backlogHigh = ev.backlog > BACKLOG_WARN_ABOVE ? true : ev.backlog <= BACKLOG_CLEAR_AT ? false : state.backlogHigh;
  const dismissed = new Set(state.dismissed);
  if (!backlogHigh) dismissed.delete("backlog");
  if (!quiet) dismissed.delete("no_audio");
  return {
    ...state,
    sessionId: ev.session_id ?? state.sessionId,
    levelDbfs: ev.level_dbfs,
    latencyMedianMs: ev.latency_ms_median,
    backlog: ev.backlog,
    quietSince: quiet ? (state.quietSince ?? now) : null,
    backlogHigh,
    dismissed,
  };
}

/** The user closed a warning: keep it closed until its condition clears. */
export function dismissMetricsWarning(state: MetricsState, warning: MetricsWarning): MetricsState {
  if (state.dismissed.has(warning)) return state;
  return { ...state, dismissed: new Set([...state.dismissed, warning]) };
}

/** When the no-audio hint becomes due (to re-check without new events); null when not pending. */
export function noAudioDueAt(state: MetricsState, now: number): number | null {
  if (!state.running || state.quietSince === null) return null;
  const due = state.quietSince + NO_AUDIO_MS;
  return due > now ? due : null;
}

/** dBFS as a 0–1 bar length (`METER_FLOOR_DBFS` and below = empty, 0 dBFS = full). */
export function levelFraction(dbfs: number): number {
  if (!Number.isFinite(dbfs)) return 0;
  return Math.min(1, Math.max(0, (dbfs - METER_FLOOR_DBFS) / -METER_FLOOR_DBFS));
}

/**
 * CSS transform for the meter's fill (0–1 bar length; null = no reading yet).
 * Rounded to hundredths: the bar is 96 px wide, and an unchanged value must not
 * restyle it every second.
 */
export function meterTransform(level: number | null): string {
  const v = level === null || !Number.isFinite(level) ? 0 : Math.min(1, Math.max(0, level));
  return `scaleX(${Math.round(v * 100) / 100})`;
}

export interface MetricsView {
  /** Show the meter and latency at all (a session is running). */
  visible: boolean;
  /** Bar length 0–1; null before the first reading. */
  level: number | null;
  levelDbfs: number | null;
  latencyMs: number | null;
  /** Warnings to show now (not closed by the user). */
  warnings: MetricsWarning[];
}

export function metricsView(state: MetricsState, now: number): MetricsView {
  const warnings: MetricsWarning[] = [];
  if (state.running && state.backlogHigh && !state.dismissed.has("backlog")) warnings.push("backlog");
  const silent = state.running && state.quietSince !== null && now - state.quietSince >= NO_AUDIO_MS;
  if (silent && !state.dismissed.has("no_audio")) warnings.push("no_audio");
  return {
    visible: state.running,
    level: state.levelDbfs === null ? null : levelFraction(state.levelDbfs),
    levelDbfs: state.levelDbfs,
    latencyMs: state.latencyMedianMs,
    warnings,
  };
}
