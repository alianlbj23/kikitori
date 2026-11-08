import { describe, expect, it } from "vitest";

import { applySubtitle, emptySubtitles, lineDisplay, trimSubtitles, type SubtitleState } from "./subtitles";
import type { SubtitleEvent } from "./types";

function ev(id: number, text: string, isFinal: boolean, session: number | null = 1): SubtitleEvent {
  return {
    utterance_id: id,
    original: text,
    translation: `譯:${text}`,
    source_language: "ja",
    target_language: "zh-TW",
    is_final: isFinal,
    start_sample: id * 16000,
    end_sample: id * 16000 + 8000,
    session_id: session,
    stable_chars: null,
    ruby: null,
    latency_ms: null,
  };
}

function run(events: SubtitleEvent[], maxLines = 10, start: SubtitleState = emptySubtitles()) {
  return events.reduce((s, e) => applySubtitle(s, e, maxLines), start);
}

const texts = (s: SubtitleState) => s.lines.map((l) => `${l.utteranceId}:${l.original}:${l.isFinal ? "F" : "P"}`);

describe("applySubtitle", () => {
  it("replaces partials in place and then with the final", () => {
    const s = run([ev(1, "こん", false), ev(1, "こんにち", false), ev(1, "こんにちは", true)]);
    expect(texts(s)).toEqual(["1:こんにちは:F"]);
  });

  it("keeps the position of an utterance when its final arrives after a newer partial", () => {
    const s = run([ev(1, "a", false), ev(2, "b", false), ev(1, "A", true)]);
    expect(texts(s)).toEqual(["1:A:F", "2:b:P"]);
  });

  it("orders by utterance id even when events arrive out of order", () => {
    const s = run([ev(3, "c", true), ev(1, "a", true), ev(2, "b", true)]);
    expect(texts(s)).toEqual(["1:a:F", "2:b:F", "3:c:F"]);
  });

  it("never downgrades a final with a late partial", () => {
    const s = run([ev(1, "final", true), ev(1, "late", false)]);
    expect(texts(s)).toEqual(["1:final:F"]);
  });

  it("does not resurrect a trimmed utterance with a late partial", () => {
    const s = run([ev(1, "a", true), ev(2, "b", true), ev(3, "c", true), ev(1, "zombie", false)], 2);
    expect(texts(s)).toEqual(["2:b:F", "3:c:F"]);
  });

  it("keeps only the newest maxLines lines", () => {
    const s = run([1, 2, 3, 4].map((i) => ev(i, `t${i}`, true)), 2);
    expect(texts(s)).toEqual(["3:t3:F", "4:t4:F"]);
  });

  it("removes an utterance when its final is empty (filtered as non-speech)", () => {
    const blank = { ...ev(2, "", true), translation: "" };
    const s = run([ev(1, "a", true), ev(2, "うー", false), blank]);
    expect(texts(s)).toEqual(["1:a:F"]);
    // A later partial for the cleared utterance stays gone.
    expect(texts(applySubtitle(s, ev(2, "again", false), 10))).toEqual(["1:a:F"]);
  });

  it("ignores empty partials and empty finals for unknown utterances", () => {
    const start = run([ev(1, "a", true)]);
    const emptyPartial = { ...ev(2, " ", false), translation: "" };
    expect(applySubtitle(start, emptyPartial, 10)).toBe(start);
    const emptyFinal = { ...ev(3, "", true), translation: "" };
    expect(texts(applySubtitle(start, emptyFinal, 10))).toEqual(["1:a:F"]);
  });

  it("drops events from an older session and resets on a newer one", () => {
    const s1 = run([ev(1, "old", true, 1)]);
    const s2 = applySubtitle(s1, ev(1, "new", false, 2), 10);
    expect(s2.sessionId).toBe(2);
    expect(texts(s2)).toEqual(["1:new:P"]);
    expect(applySubtitle(s2, ev(5, "stale", true, 1), 10)).toBe(s2);
  });

  it("accepts events without session id into the current session", () => {
    const s = run([ev(1, "a", true, 4), ev(2, "b", true, null)]);
    expect(s.sessionId).toBe(4);
    expect(texts(s)).toEqual(["1:a:F", "2:b:F"]);
  });

  it("does not mutate the previous state", () => {
    const s1 = run([ev(1, "a", false)]);
    const before = JSON.stringify(s1);
    applySubtitle(s1, ev(1, "b", true), 10);
    expect(JSON.stringify(s1)).toBe(before);
  });

  it("rejects an invalid maxLines", () => {
    expect(() => applySubtitle(emptySubtitles(), ev(1, "a", true), 0)).toThrow(RangeError);
  });
});

describe("trimSubtitles", () => {
  it("trims to the newest lines and leaves short lists untouched", () => {
    const s = run([1, 2, 3].map((i) => ev(i, `t${i}`, true)));
    expect(texts(trimSubtitles(s, 1))).toEqual(["3:t3:F"]);
    expect(trimSubtitles(s, 5)).toBe(s);
  });
});

describe("stable prefix of live lines (F2)", () => {
  it("follows the newest partial and is dropped by the final", () => {
    const p1 = { ...ev(1, "今日は寒い", false), stable_chars: 2 };
    const p2 = { ...ev(1, "今日は寒いね", false), stable_chars: 4 };
    let s = run([p1]);
    expect(lineDisplay(s.lines[0]!, true).originalParts).toEqual({ stable: "今日", unstable: "は寒い" });
    s = applySubtitle(s, p2, 10);
    expect(s.lines[0]!.stableChars).toBe(4);
    expect(lineDisplay(s.lines[0]!, false).originalParts).toEqual({ stable: "今日は寒", unstable: "いね" });
    s = applySubtitle(s, ev(1, "今日は寒いね。", true), 10);
    expect(s.lines[0]!.stableChars).toBeNull();
    expect(lineDisplay(s.lines[0]!, true).originalParts).toEqual({ stable: "今日は寒いね。", unstable: "" });
  });

  it("shows a partial without stable_chars entirely as unstable", () => {
    const s = run([ev(1, "abc", false)]);
    expect(lineDisplay(s.lines[0]!, true).originalParts).toEqual({ stable: "", unstable: "abc" });
  });
});

describe("furigana on live and final lines", () => {
  it("keeps the newest ruby with the stable prefix, and none without it", () => {
    const ruby = [
      [0, 2, "きょう"],
      [3, 4, "さむ"],
    ] as const;
    let s = run([{ ...ev(1, "今日は寒い", false), stable_chars: 3, ruby }]);
    expect(s.lines[0]!.ruby).toEqual(ruby);
    expect(lineDisplay(s.lines[0]!, true).originalRuby).toEqual({
      stable: [
        { text: "今日", reading: "きょう" },
        { text: "は", reading: null },
      ],
      unstable: [
        { text: "寒", reading: "さむ" },
        { text: "い", reading: null },
      ],
    });
    // The plain split is still there for the renderers' plain path.
    expect(lineDisplay(s.lines[0]!, true).originalParts).toEqual({ stable: "今日は", unstable: "寒い" });
    // Furigana turned off mid-sentence: the final has no ruby and is drawn as plain text.
    s = applySubtitle(s, ev(1, "今日は寒いね。", true), 10);
    expect(s.lines[0]!.ruby).toBeNull();
    expect(lineDisplay(s.lines[0]!, true).originalRuby).toBeNull();
  });
});
