/**
 * The rail's 運算負荷 (docs/specs/compute-capacity.md 五): pure logic.
 *
 * Role: turns the `metrics` load hints (`backlog`, `final_busy`, `gpu`, `rtf`)
 * into what the left rail shows while subtitles run — a level (ok / tight /
 * over / unknown), one load percentage for the coloured bar, the last
 * `LOAD_HISTORY` readings for the line chart, the plain-words line and the
 * small print. Local and remote sessions look the same: a remote session's
 * metrics are the compute host's. `main.ts` only renders the view.
 *
 * The final pass's busy share leads (see the spec's 決策): the real-time factor
 * says little while speech is sparse and the backlog only grows once it is too
 * late, while the busy share warns early.
 */

import { t, type StringKey } from "./i18n";
import type { GpuUsage, MetricsEvent } from "./types";

export type LoadLevel = "ok" | "tight" | "over" | "unknown";

/** Readings kept for the chart: about two minutes at one `metrics` event a second. */
export const LOAD_HISTORY = 120;

/** Thresholds of the spec: at or above these the level is `over` / `tight`. */
export const OVER = { backlog: 3, busy: 0.9, vram: 0.95 } as const;
export const TIGHT = { backlog: 2, busy: 0.7, vram: 0.85 } as const;
/** `backlog / BACKLOG_FULL` counts towards the percentage: four waiting sentences = 100 %. */
const BACKLOG_FULL = 4;

/** The part of a `metrics` event the load is computed from. */
export type LoadInput = Pick<MetricsEvent, "backlog" | "final_busy" | "gpu">;

/**
 * One reading as the view needs it: a `metrics` event, or the compute host's
 * own load from a `serving` event (compute-capacity.md 八), which has the same fields.
 */
export type LoadReading = LoadInput & Pick<MetricsEvent, "rtf">;

/** Share of the GPU's memory in use (0–1), or null without a usable sample. */
export function vramShare(gpu: GpuUsage | null | undefined): number | null {
  if (!gpu || !(gpu.vram_total_mb > 0)) return null;
  return Math.min(1, gpu.vram_used_mb / gpu.vram_total_mb);
}

/**
 * The level of one reading. Any signal at its limit decides; with neither the
 * busy share nor a GPU sample (no final sentence yet, an older engine) and a
 * small backlog there is nothing to judge by yet.
 */
export function loadLevel(m: LoadInput | null): LoadLevel {
  if (!m) return "unknown";
  const busy = m.final_busy ?? null;
  const vram = vramShare(m.gpu);
  if (m.backlog >= OVER.backlog || (busy ?? 0) >= OVER.busy || (vram ?? 0) >= OVER.vram) return "over";
  if (m.backlog >= TIGHT.backlog || (busy ?? 0) >= TIGHT.busy || (vram ?? 0) >= TIGHT.vram) return "tight";
  if (busy === null && vram === null) return "unknown";
  return "ok";
}

/** max(busy share, VRAM share, backlog / 4), capped at 1; null when the level is unknown. */
export function loadFraction(m: LoadInput | null): number | null {
  if (!m || loadLevel(m) === "unknown") return null;
  return Math.min(1, Math.max(m.final_busy ?? 0, vramShare(m.gpu) ?? 0, m.backlog / BACKLOG_FULL));
}

/** The chart's readings (0–1, null = unknown), oldest first, at most `LOAD_HISTORY`. */
export function pushLoad(history: readonly (number | null)[], value: number | null): (number | null)[] {
  const next = [...history, value];
  return next.length > LOAD_HISTORY ? next.slice(next.length - LOAD_HISTORY) : next;
}

/**
 * SVG path `d` of the chart in a `width` × `height` box. The newest reading is
 * at the right edge and the slots are fixed (`LOAD_HISTORY`), so the line moves
 * left as time passes; an unknown reading breaks the line instead of drawing 0.
 */
export function loadPath(history: readonly (number | null)[], width: number, height: number): string {
  const step = width / (LOAD_HISTORY - 1);
  const offset = LOAD_HISTORY - history.length;
  const parts: string[] = [];
  let drawing = false;
  history.forEach((v, i) => {
    if (v === null) {
      drawing = false;
      return;
    }
    const x = round((offset + i) * step);
    const y = round(height - Math.min(1, Math.max(0, v)) * height);
    parts.push(`${drawing ? "L" : "M"}${x} ${y}`);
    drawing = true;
  });
  return parts.join(" ");
}

function round(n: number): number {
  return Math.round(n * 10) / 10;
}

const LEVEL_TEXT: Readonly<Record<LoadLevel, StringKey>> = {
  ok: "load.ok",
  tight: "load.tight",
  over: "load.over",
  unknown: "load.unknown",
};

/** Key of the plain-words line under the bar. */
export function loadTextKey(level: LoadLevel): StringKey {
  return LEVEL_TEXT[level];
}

/** MiB as GB: one decimal, without a trailing ".0" ("6.1", "11"). */
export function formatGb(mb: number): string {
  return String(Math.round((mb / 1024) * 10) / 10);
}

/** The real-time factor as "0.4"; two decimals below 0.1 so a fast GPU does not read "0.0". */
export function formatRtf(rtf: number): string {
  return rtf < 0.1 ? rtf.toFixed(2) : rtf.toFixed(1);
}

/** The small print: "顯示卡記憶體 6.1／11 GB・辨識速度 0.4 倍時間", with whichever part is known. */
export function loadDetail(m: Pick<MetricsEvent, "gpu" | "rtf"> | null): string | null {
  const gpu = m?.gpu ?? null;
  const rtf = m?.rtf ?? null;
  const vram =
    gpu && gpu.vram_total_mb > 0
      ? t("load.vram", { used: formatGb(gpu.vram_used_mb), total: formatGb(gpu.vram_total_mb) })
      : null;
  const speed = rtf === null ? null : t("load.speed", { times: formatRtf(rtf) });
  if (vram && speed) return t("load.detail", { vram, speed });
  return vram ?? speed;
}

export interface LoadState {
  readonly sessionId: number | null;
  /** The latest reading of the session; null before its first `metrics`. */
  readonly last: LoadReading | null;
  readonly history: readonly (number | null)[];
}

export function emptyLoad(sessionId: number | null = null): LoadState {
  return { sessionId, last: null, history: [] };
}

/** Adds one reading of the current session (a `serving` load has no session id). */
export function pushReading(state: LoadState, reading: LoadReading): LoadState {
  return { sessionId: state.sessionId, last: reading, history: pushLoad(state.history, loadFraction(reading)) };
}

/** Applies one `metrics` event; a reading of an earlier session is ignored. */
export function applyLoad(state: LoadState, ev: MetricsEvent): LoadState {
  if (ev.session_id !== null && state.sessionId !== null && ev.session_id < state.sessionId) return state;
  const base = ev.session_id !== null && ev.session_id !== state.sessionId ? emptyLoad(ev.session_id) : state;
  return { sessionId: base.sessionId, last: ev, history: pushLoad(base.history, loadFraction(ev)) };
}

export interface LoadView {
  level: LoadLevel;
  /** 0–100, rounded; null when the level is unknown. */
  percent: number | null;
  text: string;
  detail: string | null;
  /** SVG path of the chart (`loadPath`); "" before there is anything to draw. */
  path: string;
}

/** Chart box in SVG user units; CSS stretches it to the rail's width. */
export const CHART_WIDTH = 240;
export const CHART_HEIGHT = 40;

export function loadView(state: LoadState): LoadView {
  const level = loadLevel(state.last);
  const fraction = loadFraction(state.last);
  return {
    level,
    percent: fraction === null ? null : Math.round(fraction * 100),
    text: t(loadTextKey(level)),
    detail: loadDetail(state.last),
    path: loadPath(state.history, CHART_WIDTH, CHART_HEIGHT),
  };
}
