/**
 * The last few final lines, for the overlay's "recent subtitles" panel.
 *
 * Role: pure helper for the overlay (docs/specs/v0.3-features.md G6). The
 * overlay's own subtitle state keeps only a couple of lines beyond the screen
 * (`bufferLines`), so the panel keeps its own short list of finals: only
 * lines with text, in utterance order, the newest `max`.
 */

import type { SubtitleLine } from "./subtitles";

/** How many lines the "recent subtitles" shortcut shows. */
export const RECENT_LINES = 10;

/** `recent` with `line` added if it is a final with text (replacing the same utterance). */
export function pushRecent(recent: readonly SubtitleLine[], line: SubtitleLine, max = RECENT_LINES): SubtitleLine[] {
  if (!line.isFinal) return recent.slice();
  const rest = recent.filter((l) => l.utteranceId !== line.utteranceId);
  if (line.original.trim() === "" && line.translation.trim() === "") return rest; // "not speech": dropped
  rest.push(line);
  rest.sort((a, b) => a.utteranceId - b.utteranceId);
  return rest.slice(Math.max(0, rest.length - max));
}
