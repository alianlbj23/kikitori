/**
 * Reading rhythm of the overlay: which lines are on screen right now.
 *
 * Role: pure, time-injected logic between the subtitle state (`subtitles.ts`,
 * every line the engine sent) and what the overlay draws
 * (docs/specs/competitive-features.md F5):
 *
 * - a line stays at least `minDisplayMs` before a newer one may push it out,
 *   so fast speech does not scroll a sentence away mid-read; a line's clock
 *   restarts when its complete translation arrives;
 * - the wait never builds up a backlog: when more lines are waiting than fit
 *   on screen, the newest screenful is shown at once;
 * - after `idleClearMs` without any change the lines fade out (0 = never);
 *   a faded line comes back only if its text changes (e.g. the translation
 *   arrives late), and new lines show as usual.
 *
 * In-place updates (a partial growing, partial → final) are shown immediately;
 * they replace nothing. The caller re-runs `stepRhythm` on every event and at
 * `wakeAt`.
 *
 * With `minDisplayFor` (v0.3 G7 "pace by length") a long line may wait longer
 * than `minDisplayMs`: `readingTimeMs` gives characters ÷ a reading speed per
 * script (Netflix's Japanese timed-text guide: 4 characters a second). The
 * no-backlog rule above still wins.
 */

import type { SubtitleLine } from "./subtitles";

export interface RhythmOptions {
  /** Lines on screen at once (1–3). */
  maxLines: number;
  minDisplayMs: number;
  /** Extra minimum for one line (G7); the larger of the two applies. */
  minDisplayFor?: (line: SubtitleLine) => number;
  /** 0 = never fade. */
  idleClearMs: number;
}

export interface RhythmState {
  /** Utterance ids on screen, oldest first. */
  readonly visible: readonly number[];
  /** When each visible line appeared or became final (ms, the caller's clock). */
  readonly stamps: ReadonlyMap<number, number>;
  /** Text signature of each visible line, to notice in-place changes. */
  readonly sigs: ReadonlyMap<number, string>;
  /** Newest utterance id ever put on screen: older unseen lines are skipped. */
  readonly shownUpTo: number;
  /** Lines hidden by the idle fade, with their text then. */
  readonly faded: ReadonlyMap<number, string>;
  /** Last time anything on screen changed. */
  readonly lastChangeAt: number;
}

export interface RhythmResult {
  state: RhythmState;
  /** What to draw, oldest first. */
  lines: SubtitleLine[];
  /** Run `stepRhythm` again at this time (a line may be replaced, or the screen fades). */
  wakeAt: number | null;
  /** The lines were just hidden by the idle fade (the overlay animates this). */
  fadedNow: boolean;
}

export function emptyRhythm(now = 0): RhythmState {
  return { visible: [], stamps: new Map(), sigs: new Map(), shownUpTo: 0, faded: new Map(), lastChangeAt: now };
}

function signature(line: SubtitleLine): string {
  return `${line.isFinal ? 1 : 0}|${line.original.length}|${line.original}|${line.translation}`;
}

function wasFinal(sig: string | undefined): boolean {
  return sig !== undefined && sig.startsWith("1|");
}

/** Advances the rhythm to `now` for the current subtitle lines (sorted by utterance id). */
export function stepRhythm(
  prev: RhythmState,
  lines: readonly SubtitleLine[],
  now: number,
  opts: RhythmOptions,
): RhythmResult {
  const maxLines = Math.max(1, Math.floor(opts.maxLines));
  const byId = new Map(lines.map((l) => [l.utteranceId, l]));
  const stamps = new Map(prev.stamps);
  const sigs = new Map(prev.sigs);
  const faded = new Map(prev.faded);
  let shownUpTo = prev.shownUpTo;

  // Lines the engine removed (non-speech) leave at once.
  let visible = prev.visible.filter((id) => byId.has(id));
  let changed = visible.length !== prev.visible.length;
  for (const id of prev.visible) {
    if (!byId.has(id)) {
      stamps.delete(id);
      sigs.delete(id);
    }
  }

  // In-place updates; becoming final restarts the line's reading time.
  for (const id of visible) {
    const line = byId.get(id) as SubtitleLine;
    const sig = signature(line);
    const old = sigs.get(id);
    if (old === sig) continue;
    changed = true;
    if (line.isFinal && !wasFinal(old)) stamps.set(id, now);
    sigs.set(id, sig);
  }

  // Faded lines whose text changed since come back; others stay hidden.
  const revived: number[] = [];
  for (const [id, sig] of faded) {
    const line = byId.get(id);
    if (!line) faded.delete(id);
    else if (signature(line) !== sig) {
      faded.delete(id);
      revived.push(id);
    }
  }

  const shown = new Set(visible);
  let pending = [...new Set([...revived, ...lines.filter((l) => l.utteranceId > shownUpTo).map((l) => l.utteranceId)])]
    .filter((id) => !shown.has(id))
    .sort((a, b) => a - b);
  // More waiting than fits on screen: waiting would only grow the lag.
  const force = pending.length > maxLines;
  if (force) {
    shownUpTo = Math.max(shownUpTo, ...pending);
    pending = pending.slice(-maxLines);
  }

  let wakeAt: number | null = null;
  for (const id of pending) {
    if (visible.length >= maxLines) {
      const oldest = visible[0];
      const oldestLine = byId.get(oldest);
      const wait = Math.max(opts.minDisplayMs, oldestLine && opts.minDisplayFor ? opts.minDisplayFor(oldestLine) : 0);
      const due = (stamps.get(oldest) ?? now) + wait;
      if (!force && now < due) {
        wakeAt = due;
        break;
      }
      visible = visible.slice(1);
      stamps.delete(oldest);
      sigs.delete(oldest);
    }
    visible = [...visible, id].sort((a, b) => a - b);
    stamps.set(id, now);
    sigs.set(id, signature(byId.get(id) as SubtitleLine));
    shownUpTo = Math.max(shownUpTo, id);
    changed = true;
  }

  const lastChangeAt = changed ? now : prev.lastChangeAt;
  let fadedNow = false;
  if (opts.idleClearMs > 0 && visible.length > 0) {
    const idleAt = lastChangeAt + opts.idleClearMs;
    if (now >= idleAt) {
      for (const id of visible) faded.set(id, sigs.get(id) ?? "");
      visible = [];
      stamps.clear();
      sigs.clear();
      fadedNow = true;
    } else {
      wakeAt = wakeAt === null ? idleAt : Math.min(wakeAt, idleAt);
    }
  }

  return {
    state: { visible, stamps, sigs, shownUpTo, faded, lastChangeAt },
    lines: visible.map((id) => byId.get(id) as SubtitleLine),
    wakeAt,
    fadedNow,
  };
}

/** Characters a second people read in CJK subtitles (Netflix Japanese timed-text guide). */
export const CJK_READING_CPS = 4;
/** Characters a second for other scripts (a common subtitling ceiling). */
export const OTHER_READING_CPS = 15;
const CJK_TARGETS = new Set(["ja", "zh-TW", "zh-CN", "ko"]);

/**
 * How long reading `line` takes (G7): the text people read — the translation,
 * or the original when translations are hidden or missing — at the reading
 * speed of its language.
 */
export function readingTimeMs(line: SubtitleLine, showTranslation: boolean): number {
  const useTranslation = showTranslation && line.translation.trim() !== "";
  const text = useTranslation ? line.translation : line.original;
  const language = useTranslation ? line.targetLanguage : line.sourceLanguage;
  const cjk = CJK_TARGETS.has(language) || ["zh", "ja", "ko"].includes(language);
  const chars = Array.from(text.replace(/\s+/gu, "")).length;
  return Math.round((chars / (cjk ? CJK_READING_CPS : OTHER_READING_CPS)) * 1000);
}

/** Subtitle lines the overlay keeps beyond the visible ones (waiting or faded). */
export function bufferLines(maxLines: number): number {
  return maxLines + 8;
}
