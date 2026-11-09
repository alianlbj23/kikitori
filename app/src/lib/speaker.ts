/**
 * Who-said-it labels for interpret mode, and the "could you say that again?"
 * phrase shown to the other person.
 *
 * Role: pure helpers for the overlay, the transcript and exports
 * (docs/specs/v0.3-features.md G3). In interpret mode every line comes from
 * either the user ("self", their own language) or the partner. A label is
 * shown when the speaker changes, as subtitling guidelines ask (Xbox
 * Accessibility Guideline 104: name the speaker on a change; colour must not be
 * the only cue). The repair phrase is fixed text in each of the 20 subtitle
 * languages, so pressing the shortcut needs no translation call.
 *
 * Media mode with speaker labels (docs/specs/speaker-labels.md) numbers the
 * voices instead ("Speaker 1", "Speaker 2"); the user may rename them for the
 * session, and the same rule (label on a change) applies to the names.
 */

import { SAY_AGAIN } from "../locales/phrases/say-again";
import type { Speaker } from "./types";

export { SAY_AGAIN };

/**
 * For each line, the speaker to label it with, or null when the previous line
 * had the same speaker (or the line has none, as in media mode).
 */
export function speakerLabels(lines: readonly { readonly speaker: Speaker | null }[]): (Speaker | null)[] {
  let previous: Speaker | null = null;
  return lines.map((line) => {
    const label = line.speaker !== null && line.speaker !== previous ? line.speaker : null;
    previous = line.speaker;
    return label;
  });
}

/** Number of chip colours for media-mode speaker labels (CSS `.voice.c0` … `.c5`). */
export const VOICE_COLORS = 6;

/**
 * The name shown for `voice` (from 1): the one the user gave it this session,
 * else `fallback(voice)` ("Speaker 2"). Names are trimmed; blank means default.
 */
export function voiceName(voice: number, names: readonly string[], fallback: (voice: number) => string): string {
  const given = (names[voice - 1] ?? "").trim();
  return given === "" ? fallback(voice) : given;
}

/**
 * Chip colour for a speaker name. Derived from the name, not the number, so
 * two labels the user renamed to the same name look like the same person —
 * renaming is how labels are merged (docs/specs/speaker-labels.md).
 */
export function voiceColor(name: string): number {
  let hash = 0;
  for (const ch of name) hash = (hash * 31 + (ch.codePointAt(0) ?? 0)) >>> 0;
  return hash % VOICE_COLORS;
}

/**
 * Media mode: for each line, the speaker name to show in front of it, or null
 * when the previous line had the same name (or the line has no label). Same
 * rule as `speakerLabels`, compared by name so merged labels read as one.
 */
export function voiceLabels(
  lines: readonly { readonly voice: number | null }[],
  names: readonly string[],
  fallback: (voice: number) => string,
): (string | null)[] {
  let previous: string | null = null;
  return lines.map((line) => {
    const name = line.voice === null ? null : voiceName(line.voice, names, fallback);
    const label = name !== null && name !== previous ? name : null;
    previous = name;
    return label;
  });
}

/** The repair phrase for `language`, or the English one for a code this build does not know. */
export function sayAgainPhrase(language: string): { text: string; lang: string } {
  const text = SAY_AGAIN[language];
  return text === undefined ? { text: SAY_AGAIN.en!, lang: "en" } : { text, lang: language };
}
