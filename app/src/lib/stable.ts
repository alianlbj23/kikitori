/**
 * Stable prefix of a live (partial) subtitle line.
 *
 * Role: pure helper between the subtitle state (`subtitles.ts`) and the two
 * renderers (main window transcript, overlay). The engine marks how much of a
 * partial's original text has settled (`stable_chars`, LocalAgreement-2 in
 * competitive-features.md F2); the renderers show that prefix at normal
 * emphasis and fade only the tail that may still change.
 *
 * `stable_chars` counts code points (Python `len`), while JavaScript strings
 * index UTF-16 units, so the cut is computed here and never by slicing with
 * the raw number. The cut also never falls inside a grapheme cluster (a base
 * letter and its combining marks, an emoji sequence): such a cluster would be
 * split across two spans and drawn broken, so it counts as unstable until the
 * whole cluster is covered. Logical order is kept, so right-to-left text (the
 * renderers use `dir="auto"` on the parent) needs nothing special.
 */

export interface TextParts {
  /** Settled prefix, shown at normal emphasis. */
  readonly stable: string;
  /** Tail that may still change, shown faded. */
  readonly unstable: string;
}

let segmenter: Intl.Segmenter | null | undefined;

/** `Intl.Segmenter` for grapheme clusters, or null where the runtime lacks it. */
function graphemeSegmenter(): Intl.Segmenter | null {
  if (segmenter === undefined) {
    // Older WebKitGTK builds lack Intl.Segmenter; code points are the fallback.
    segmenter = typeof Intl.Segmenter === "function" ? new Intl.Segmenter(undefined, { granularity: "grapheme" }) : null;
  }
  return segmenter;
}

/** Text units that must stay together, in order (graphemes, or code points as a fallback). */
function clusters(text: string): Iterable<string> {
  const seg = graphemeSegmenter();
  if (!seg) return Array.from(text);
  return (function* () {
    for (const s of seg.segment(text)) yield s.segment;
  })();
}

/**
 * Splits a line's original text into its stable prefix and unstable tail.
 *
 * - finals are stable as a whole;
 * - a partial without `stableChars` (older engine, or nothing settled yet) is
 *   unstable as a whole — the way partials were always shown;
 * - a count beyond the text means all of it; negative or fractional counts
 *   are treated as "nothing settled" / rounded down.
 */
export function splitStable(text: string, stableChars: number | null, isFinal: boolean): TextParts {
  if (isFinal) return { stable: text, unstable: "" };
  if (stableChars === null || !Number.isFinite(stableChars) || stableChars <= 0) {
    return { stable: "", unstable: text };
  }
  const limit = Math.floor(stableChars);
  let codePoints = 0;
  let cut = 0;
  for (const cluster of clusters(text)) {
    const size = Array.from(cluster).length;
    if (codePoints + size > limit) break;
    codePoints += size;
    cut += cluster.length;
  }
  return { stable: text.slice(0, cut), unstable: text.slice(cut) };
}
