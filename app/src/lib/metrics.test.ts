import { describe, expect, it } from "vitest";

import {
  NO_AUDIO_MS,
  applyMetrics,
  dismissMetricsWarning,
  emptyMetrics,
  levelFraction,
  metricsSessionState,
  metricsView,
  noAudioDueAt,
  type MetricsState,
} from "./metrics";
import type { MetricsEvent } from "./types";

function m(level_dbfs: number, backlog = 0, latency_ms_median: number | null = null, session_id: number | null = 1): MetricsEvent {
  return { session_id, level_dbfs, backlog, latency_ms_median };
}

function running(sessionId = 1): MetricsState {
  return metricsSessionState(emptyMetrics(), "running", sessionId);
}

/** Applies readings one second apart starting at `t0`; returns the state and the last time. */
function feed(state: MetricsState, readings: MetricsEvent[], t0 = 0): { state: MetricsState; now: number } {
  let now = t0;
  for (const r of readings) {
    state = applyMetrics(state, r, now);
    now += 1000;
  }
  return { state, now: now - 1000 };
}

describe("levelFraction", () => {
  it("maps -60..0 dBFS onto the bar and clamps outside", () => {
    expect(levelFraction(0)).toBe(1);
    expect(levelFraction(-30)).toBe(0.5);
    expect(levelFraction(-60)).toBe(0);
    expect(levelFraction(-120)).toBe(0);
    expect(levelFraction(6)).toBe(1);
    expect(levelFraction(Number.NaN)).toBe(0);
  });
});

describe("metrics state", () => {
  it("shows nothing while no session runs and ignores readings then", () => {
    const idle = emptyMetrics();
    expect(metricsView(idle, 0).visible).toBe(false);
    expect(applyMetrics(idle, m(-10), 0)).toBe(idle);
  });

  it("shows the level and median latency of a running session", () => {
    const s = applyMetrics(running(), m(-30, 0, 1200), 0);
    expect(metricsView(s, 0)).toEqual({ visible: true, level: 0.5, levelDbfs: -30, latencyMs: 1200, warnings: [] });
    expect(metricsView(running(), 0).level).toBeNull();
  });

  it("warns when more than 3 sentences wait, and clears only at 1 or fewer", () => {
    let s = feed(running(), [m(-20, 3)]).state;
    expect(metricsView(s, 0).warnings).toEqual([]);
    s = applyMetrics(s, m(-20, 4), 1000);
    expect(metricsView(s, 1000).warnings).toEqual(["backlog"]);
    s = applyMetrics(s, m(-20, 2), 2000);
    expect(metricsView(s, 2000).warnings).toEqual(["backlog"]);
    s = applyMetrics(s, m(-20, 1), 3000);
    expect(metricsView(s, 3000).warnings).toEqual([]);
  });

  it("hints at missing audio after 15 s below -60 dBFS", () => {
    const quiet = Array.from({ length: 15 }, () => m(-80));
    let { state: s, now } = feed(running(), quiet);
    expect(now).toBe(14_000);
    expect(metricsView(s, now).warnings).toEqual([]);
    expect(noAudioDueAt(s, now)).toBe(NO_AUDIO_MS);
    // Due even when no further reading arrives.
    expect(metricsView(s, NO_AUDIO_MS).warnings).toEqual(["no_audio"]);
    expect(noAudioDueAt(s, NO_AUDIO_MS)).toBeNull();
    // Any sound resets the clock.
    s = applyMetrics(s, m(-40), NO_AUDIO_MS + 1000);
    expect(metricsView(s, NO_AUDIO_MS + 1000).warnings).toEqual([]);
    expect(noAudioDueAt(s, NO_AUDIO_MS + 1000)).toBeNull();
  });

  it("does not count exactly -60 dBFS as silence", () => {
    const { state } = feed(running(), Array.from({ length: 20 }, () => m(-60)));
    expect(metricsView(state, 60_000).warnings).toEqual([]);
  });

  it("keeps a closed warning closed until its condition clears", () => {
    let s = applyMetrics(running(), m(-20, 5), 0);
    s = dismissMetricsWarning(s, "backlog");
    expect(metricsView(s, 0).warnings).toEqual([]);
    s = applyMetrics(s, m(-20, 6), 1000);
    expect(metricsView(s, 1000).warnings).toEqual([]);
    s = applyMetrics(s, m(-20, 0), 2000);
    s = applyMetrics(s, m(-20, 5), 3000);
    expect(metricsView(s, 3000).warnings).toEqual(["backlog"]);
    expect(dismissMetricsWarning(dismissMetricsWarning(s, "no_audio"), "no_audio").dismissed.size).toBe(1);
  });

  it("starts afresh with each session and forgets everything when it ends", () => {
    let s = applyMetrics(running(1), m(-80, 9, 500), 0);
    expect(metricsSessionState(s, "running", 1)).toBe(s);
    s = metricsSessionState(s, "idle", 1);
    expect(metricsView(s, 99_000)).toEqual({ visible: false, level: null, levelDbfs: null, latencyMs: null, warnings: [] });
    expect(metricsSessionState(s, "error", null)).toBe(s);
    const next = metricsSessionState(applyMetrics(running(1), m(-80, 9), 0), "running", 2);
    expect(next.sessionId).toBe(2);
    expect(next.backlogHigh).toBe(false);
    expect(next.quietSince).toBeNull();
  });

  it("drops late readings from an earlier session", () => {
    const s = running(3);
    expect(applyMetrics(s, m(-10, 9, 1, 2), 0)).toBe(s);
    expect(applyMetrics(s, m(-10, 0, null, null), 0).levelDbfs).toBe(-10);
  });

  it("stops the no-audio clock when the session stops running", () => {
    const { state } = feed(running(), Array.from({ length: 16 }, () => m(-90)));
    expect(metricsView(state, 15_000).warnings).toEqual(["no_audio"]);
    const stopped = metricsSessionState(state, "loading", 1);
    expect(metricsView(stopped, 30_000).warnings).toEqual([]);
    expect(noAudioDueAt(stopped, 0)).toBeNull();
  });
});
