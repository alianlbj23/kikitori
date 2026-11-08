/**
 * Subtitle state: how a stream of `subtitle` events becomes the lines on screen.
 *
 * Role: pure, framework-free logic shared by the overlay (a few lines) and the
 * main window's transcript (hundreds of lines). Rendering code only ever reads
 * the state produced here, so the ordering and replacement rules live in one
 * tested place:
 *
 * - a partial (`is_final=false`) is replaced in place by later partials and by
 *   the final for the same `utterance_id`;
 * - a final is never downgraded by a late partial;
 * - a final with empty original and translation clears that utterance (the
 *   engine sends this when the full pass decided it was not speech);
 * - lines are ordered by `utterance_id`; only the newest `maxLines` are kept;
 * - events from an older session are dropped, a newer session starts afresh.
 */

import { rubyParts, type RubyParts, type RubySpan } from "./ruby";
import { splitStable, type TextParts } from "./stable";
import type { SubtitleEvent } from "./types";

export interface SubtitleLine {
  readonly utteranceId: number;
  readonly original: string;
  readonly translation: string;
  readonly sourceLanguage: string;
  readonly targetLanguage: string;
  readonly isFinal: boolean;
  readonly startSample: number;
  readonly endSample: number;
  /** Partials: code points of `original` that have settled (null = none known). */
  readonly stableChars: number | null;
  /** Furigana over the original (docs/specs/furigana.md); null = none. */
  readonly ruby: readonly RubySpan[] | null;
}

export interface SubtitleState {
  readonly sessionId: number | null;
  readonly lines: readonly SubtitleLine[];
  /** Highest utterance id finalised in this session (survives trimming). */
  readonly lastFinalId: number;
}

export function emptySubtitles(sessionId: number | null = null): SubtitleState {
  return { sessionId, lines: [], lastFinalId: 0 };
}

function toLine(ev: SubtitleEvent): SubtitleLine {
  return {
    utteranceId: ev.utterance_id,
    original: ev.original,
    translation: ev.translation,
    sourceLanguage: ev.source_language,
    targetLanguage: ev.target_language,
    isFinal: ev.is_final,
    startSample: ev.start_sample,
    endSample: ev.end_sample,
    stableChars: ev.stable_chars,
    ruby: ev.ruby,
  };
}

function isBlank(ev: SubtitleEvent): boolean {
  return ev.original.trim() === "" && ev.translation.trim() === "";
}

/** Applies one event; returns the same object when nothing changed. */
export function applySubtitle(
  state: SubtitleState,
  ev: SubtitleEvent,
  maxLines: number,
): SubtitleState {
  if (!Number.isInteger(maxLines) || maxLines < 1) {
    throw new RangeError(`maxLines must be a positive integer, got ${maxLines}`);
  }
  let current = state;
  if (ev.session_id !== null && current.sessionId !== ev.session_id) {
    if (current.sessionId !== null && ev.session_id < current.sessionId) {
      return state; // late event from a session that already ended
    }
    current = emptySubtitles(ev.session_id);
  }

  const index = current.lines.findIndex((l) => l.utteranceId === ev.utterance_id);
  const existing = index >= 0 ? current.lines[index] : undefined;

  if (!ev.is_final) {
    // The engine never sends a partial after the final for the same utterance;
    // if one arrives anyway (reordering, trimmed final) it must not resurrect it.
    if (existing?.isFinal || ev.utterance_id <= current.lastFinalId) return state;
    if (isBlank(ev)) return state;
  }

  const lastFinalId = ev.is_final
    ? Math.max(current.lastFinalId, ev.utterance_id)
    : current.lastFinalId;

  if (ev.is_final && isBlank(ev)) {
    if (!existing) {
      return current === state && lastFinalId === state.lastFinalId
        ? state
        : { ...current, lastFinalId };
    }
    return {
      ...current,
      lastFinalId,
      lines: current.lines.filter((_, i) => i !== index),
    };
  }

  const line = toLine(ev);
  let lines: SubtitleLine[];
  if (existing) {
    lines = current.lines.slice();
    lines[index] = line;
  } else {
    lines = current.lines.slice();
    // Usually the newest utterance, so search from the end.
    let at = lines.length;
    while (at > 0 && lines[at - 1].utteranceId > line.utteranceId) at--;
    lines.splice(at, 0, line);
  }
  if (lines.length > maxLines) lines = lines.slice(lines.length - maxLines);
  return { sessionId: current.sessionId, lines, lastFinalId };
}

/** What one line shows, given the "顯示翻譯" switch. */
export interface LineDisplay {
  original: string;
  /** `original` split into its settled prefix and the tail that may change (F2). */
  originalParts: TextParts;
  /**
   * The same split with furigana (`<ruby>` segments); null when the line has
   * none, and the renderers then use `originalParts` exactly as before.
   */
  originalRuby: RubyParts | null;
  /** Empty when translations are hidden; the translation line is then left out. */
  translation: string;
  /**
   * Translations are off: the original takes the translation's primary style
   * (size, colour, weight) instead of sitting small above an empty line.
   */
  originalOnly: boolean;
}

/**
 * The text of `line` with translations shown or hidden. Lines translated before
 * the switch was turned off keep their translation in the state, so turning it
 * back on shows them again; the engine sends an empty translation while off.
 */
export function lineDisplay(line: SubtitleLine, showTranslation: boolean): LineDisplay {
  const originalParts = splitStable(line.original, line.stableChars, line.isFinal);
  const originalRuby = rubyParts(line.original, line.ruby, line.stableChars, line.isFinal);
  return showTranslation
    ? { original: line.original, originalParts, originalRuby, translation: line.translation, originalOnly: false }
    : { original: line.original, originalParts, originalRuby, translation: "", originalOnly: true };
}

/** Trims to a new limit (e.g. after the user changes "lines on screen"). */
export function trimSubtitles(state: SubtitleState, maxLines: number): SubtitleState {
  if (state.lines.length <= maxLines) return state;
  return { ...state, lines: state.lines.slice(state.lines.length - maxLines) };
}
