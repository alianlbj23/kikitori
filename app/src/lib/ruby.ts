/**
 * Furigana (ruby) for Japanese originals.
 *
 * Role: pure helper between the subtitle state (`subtitles.ts`) and the two
 * renderers (main window transcript, overlay), next to `stable.ts`. The engine
 * sends `ruby: [[start, end, reading], ...]` on Japanese subtitles when furigana
 * is on (docs/specs/furigana.md): offsets count code points of `original`, like
 * `stable_chars`, and readings are hiragana over the kanji only.
 *
 * `rubyParts` turns that into segments for the settled prefix and the tail that
 * may still change (F2): an annotated word belongs to the settled part only
 * when it ends inside it, so a word is never drawn half faded. `segmentNodes`
 * builds `<ruby>base<rt>reading</rt></ruby>` with DOM calls (never innerHTML):
 * both base and reading come from the engine.
 */

import { h } from "./dom";
import { splitStable } from "./stable";

/** `[start, end, reading]`: `original` from code point `start` to `end` reads `reading`. */
export type RubySpan = readonly [start: number, end: number, reading: string];

/** A run of the original: plain text (`reading` null) or one annotated word. */
export interface RubySegment {
  readonly text: string;
  readonly reading: string | null;
}

export interface RubyParts {
  /** Settled prefix, shown at normal emphasis. */
  readonly stable: readonly RubySegment[];
  /** Tail that may still change, shown faded. */
  readonly unstable: readonly RubySegment[];
}

/** Longest reading accepted for one word (same bound as the Rust validator). */
export const MAX_RUBY_READING_CHARS = 64;
/** Hiragana (U+3041–U+3096) and the long vowel mark (U+30FC), checked by code point so no kana sits in a literal. */
function isHiraganaReading(reading: string): boolean {
  if (!reading) return false;
  for (const ch of reading) {
    const cp = ch.codePointAt(0)!;
    if (!((cp >= 0x3041 && cp <= 0x3096) || cp === 0x30fc)) return false;
  }
  return true;
}

/**
 * Whether `spans` is a valid annotation of a text of `textCodePoints` code
 * points: sorted, non-overlapping, inside the text, short hiragana readings.
 */
export function validRuby(spans: readonly RubySpan[], textCodePoints: number): boolean {
  let previousEnd = 0;
  for (const [start, end, reading] of spans) {
    if (!Number.isSafeInteger(start) || !Number.isSafeInteger(end)) return false;
    if (start < previousEnd || start >= end || end > textCodePoints) return false;
    if (Array.from(reading).length > MAX_RUBY_READING_CHARS || !isHiraganaReading(reading)) return false;
    previousEnd = end;
  }
  return true;
}

/** UTF-16 index of every code point boundary: `offsets[i]` is where code point `i` starts. */
function codePointOffsets(text: string): number[] {
  const offsets = [0];
  let at = 0;
  for (const ch of text) {
    at += ch.length;
    offsets.push(at);
  }
  return offsets;
}

function pushText(into: RubySegment[], text: string): void {
  if (!text) return;
  const last = into[into.length - 1];
  if (last && last.reading === null) into[into.length - 1] = { text: last.text + text, reading: null };
  else into.push({ text, reading: null });
}

/**
 * Splits `text` into annotated words and plain runs, divided into the settled
 * prefix and the unstable tail exactly as `splitStable` divides plain text,
 * except that a word crossing the cut moves to the tail as a whole.
 *
 * Returns null when there is nothing to annotate or the spans are invalid
 * (the caller then shows plain text, as without furigana).
 */
export function rubyParts(
  text: string,
  ruby: readonly RubySpan[] | null,
  stableChars: number | null,
  isFinal: boolean,
): RubyParts | null {
  if (!ruby || ruby.length === 0) return null;
  const offsets = codePointOffsets(text);
  if (!validRuby(ruby, offsets.length - 1)) return null;
  // UTF-16 index of the stable/unstable cut (never inside a grapheme cluster).
  const cut = splitStable(text, stableChars, isFinal).stable.length;
  const stable: RubySegment[] = [];
  const unstable: RubySegment[] = [];
  let pos = 0;
  const plain = (from: number, to: number) => {
    if (from >= to) return;
    const mid = Math.min(Math.max(cut, from), to);
    pushText(stable, text.slice(from, mid));
    pushText(unstable, text.slice(mid, to));
  };
  for (const [start, end, reading] of ruby) {
    const from = offsets[start]!;
    const to = offsets[end]!;
    // Once a word has gone to the tail, everything after it does too.
    if (unstable.length > 0) pushText(unstable, text.slice(pos, from));
    else plain(pos, from);
    (to <= cut && unstable.length === 0 ? stable : unstable).push({ text: text.slice(from, to), reading });
    pos = to;
  }
  if (unstable.length > 0) pushText(unstable, text.slice(pos));
  else plain(pos, text.length);
  return { stable, unstable };
}

/** A string that changes whenever the rendered segments would (cheap change detection). */
export function rubyKey(parts: RubyParts): string {
  return JSON.stringify([parts.stable, parts.unstable]);
}

/** Text nodes and `<ruby>` elements for `segments`, built without parsing markup. */
export function segmentNodes(segments: readonly RubySegment[]): Node[] {
  return segments.map((s) =>
    s.reading === null ? document.createTextNode(s.text) : h("ruby", {}, s.text, h("rt", {}, s.reading)),
  );
}

/**
 * Furigana on a word's first appearance only (v0.3 G7; Netflix's Japanese
 * guide marks readings once). `owners` maps a word and its reading to the
 * utterance that first showed it and is updated here: that utterance keeps the
 * reading on every redraw (partials and its final), later ones show the word
 * plain. The caller keeps one map per session.
 */
export function firstReadingsOnly(parts: RubyParts, utteranceId: number, owners: Map<string, number>): RubyParts {
  const strip = (segments: readonly RubySegment[]): RubySegment[] =>
    segments.map((s) => {
      if (s.reading === null) return s;
      const key = `${s.text}\u0000${s.reading}`;
      const owner = owners.get(key);
      if (owner === undefined) {
        owners.set(key, utteranceId);
        return s;
      }
      return owner === utteranceId ? s : { text: s.text, reading: null };
    });
  return { stable: strip(parts.stable), unstable: strip(parts.unstable) };
}
