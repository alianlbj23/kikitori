import { describe, expect, it } from "vitest";

import { pushRecent } from "./recent";
import type { SubtitleLine } from "./subtitles";

function line(id: number, text: string, isFinal = true): SubtitleLine {
  return {
    utteranceId: id,
    original: text,
    translation: text ? `譯:${text}` : "",
    sourceLanguage: "ja",
    targetLanguage: "zh-TW",
    isFinal,
    startSample: id * 16000,
    endSample: id * 16000 + 8000,
    stableChars: null,
    translationStableChars: null,
    speaker: null,
    voice: null,
    ruby: null,
  };
}

const ids = (lines: readonly SubtitleLine[]) => lines.map((l) => l.utteranceId);

describe("pushRecent", () => {
  it("keeps finals with text, in order, the newest max", () => {
    let recent: SubtitleLine[] = [];
    for (let id = 1; id <= 5; id++) recent = pushRecent(recent, line(id, `t${id}`), 3);
    expect(ids(recent)).toEqual([3, 4, 5]);
  });

  it("ignores partials and replaces a repeated utterance", () => {
    let recent = pushRecent([], line(1, "a"));
    recent = pushRecent(recent, line(2, "b", false));
    expect(ids(recent)).toEqual([1]);
    recent = pushRecent(recent, line(1, "a2"));
    expect(recent.map((l) => l.original)).toEqual(["a2"]);
  });

  it("drops a final that turned out not to be speech, and sorts late finals", () => {
    let recent = pushRecent([], line(2, "b"));
    recent = pushRecent(recent, line(1, "a"));
    expect(ids(recent)).toEqual([1, 2]);
    recent = pushRecent(recent, line(2, ""));
    expect(ids(recent)).toEqual([1]);
  });
});
