/**
 * Calm live captions: which characters just appeared, and a line that never
 * shrinks while it is still being heard.
 *
 * Role: pure helpers for the overlay renderer (docs/specs/v0.3-features.md
 * G2). Google's live-caption study (CHI EA 2023, N=123) found that
 * stabilised text with new words fading in reads better than plain
 * re-rendering, and that updates which re-wrap lines already on screen are
 * the distracting part. So the overlay fades in only the text added since the
 * last frame of the same line, and keeps a live line at least as tall as it
 * has been, so a tail that gets shorter does not make the block jump.
 */

import type { TextParts } from "./stable";

/** A part of a line split into text already on screen and text that just appeared. */
export interface FreshText {
  readonly kept: string;
  readonly fresh: string;
}

/** `parts` (stable prefix + tail) with the newly appeared text of each marked. */
export interface FreshParts {
  readonly stable: FreshText;
  readonly unstable: FreshText;
}

/** Length (UTF-16 units) of the common prefix, never cutting a surrogate pair. */
function commonPrefix(a: string, b: string): number {
  const limit = Math.min(a.length, b.length);
  let i = 0;
  while (i < limit && a.charCodeAt(i) === b.charCodeAt(i)) i++;
  // Back off from the middle of a surrogate pair (𠮷, emoji): it would be split across spans.
  if (i > 0 && i < b.length) {
    const code = b.charCodeAt(i - 1);
    if (code >= 0xd800 && code <= 0xdbff) i--;
  }
  return i;
}

function split(text: string, keptUpTo: number): FreshText {
  const cut = Math.max(0, Math.min(text.length, keptUpTo));
  return { kept: text.slice(0, cut), fresh: text.slice(cut) };
}

/**
 * Marks what is new in `parts` compared with `previous` (the same line's whole
 * text the last time it was drawn). Everything after the common prefix is
 * fresh; with no previous frame nothing is (a line's first appearance is not
 * animated character by character).
 */
export function freshParts(parts: TextParts, previous: string | null): FreshParts {
  const text = parts.stable + parts.unstable;
  const kept = previous === null ? text.length : commonPrefix(previous, text);
  return {
    stable: split(parts.stable, kept),
    unstable: split(parts.unstable, kept - parts.stable.length),
  };
}

/**
 * Remembers the tallest height each live line reached. A partial is drawn at
 * least that tall; a final (or an utterance no longer on screen) lets go, so
 * the settled text takes its natural height.
 */
export class HeightLatch {
  private readonly heights = new Map<number, number>();

  /** Minimum height (px) to give a line before measuring it; null = none. */
  minHeight(utteranceId: number, isFinal: boolean): number | null {
    if (isFinal) {
      this.heights.delete(utteranceId);
      return null;
    }
    return this.heights.get(utteranceId) ?? null;
  }

  /** Records a partial line's drawn height (ignored for finals and bad values). */
  record(utteranceId: number, isFinal: boolean, height: number): void {
    if (isFinal || !Number.isFinite(height) || height <= 0) return;
    this.heights.set(utteranceId, Math.max(this.heights.get(utteranceId) ?? 0, height));
  }

  /** Forgets lines that are no longer on screen (keeps the map small). */
  keepOnly(utteranceIds: Iterable<number>): void {
    const keep = new Set(utteranceIds);
    for (const id of this.heights.keys()) if (!keep.has(id)) this.heights.delete(id);
  }

  /** Forgets everything (new session, cleared screen). */
  clear(): void {
    this.heights.clear();
  }
}
