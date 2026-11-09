import { describe, expect, it } from "vitest";

import { SAY_AGAIN, VOICE_COLORS, sayAgainPhrase, speakerLabels, voiceColor, voiceLabels, voiceName } from "./speaker";
import type { Speaker } from "./types";

describe("speakerLabels", () => {
  it("labels a line only when the speaker changes", () => {
    const order: Speaker[] = ["self", "self", "partner", "partner", "self"];
    const lines = order.map((speaker) => ({ speaker }));
    expect(speakerLabels(lines)).toEqual(["self", null, "partner", null, "self"]);
  });

  it("labels nothing in media mode and restarts after an unlabelled line", () => {
    expect(speakerLabels([{ speaker: null }, { speaker: null }])).toEqual([null, null]);
    expect(speakerLabels([{ speaker: "self" }, { speaker: null }, { speaker: "self" }])).toEqual([
      "self",
      null,
      "self",
    ]);
    expect(speakerLabels([])).toEqual([]);
  });
});

describe("sayAgainPhrase", () => {
  it("has a phrase for every subtitle language", () => {
    const codes = ["ja", "en", "zh-TW", "zh-CN", "ko", "es", "fr", "de", "pt", "ru"];
    const more = ["it", "ar", "hi", "id", "vi", "th", "tr", "pl", "nl", "uk"];
    expect(Object.keys(SAY_AGAIN).sort()).toEqual([...codes, ...more].sort());
    for (const text of Object.values(SAY_AGAIN)) {
      expect(text.trim().length).toBeGreaterThan(0);
      expect(Array.from(text).length).toBeLessThanOrEqual(200); // Rust overlay_notice limit
    }
  });

  it("returns the phrase with its language, and English for an unknown code", () => {
    expect(sayAgainPhrase("ja")).toEqual({ text: SAY_AGAIN.ja, lang: "ja" });
    expect(sayAgainPhrase("zh-TW").lang).toBe("zh-TW");
    expect(sayAgainPhrase("xx")).toEqual({ text: SAY_AGAIN.en, lang: "en" });
  });
});

describe("voice labels (speaker-labels.md)", () => {
  const fallback = (n: number) => `Speaker ${n}`;

  it("names a voice by its given name, or the default when blank", () => {
    expect(voiceName(1, ["Pekora", "  "], fallback)).toBe("Pekora");
    expect(voiceName(2, ["Pekora", "  "], fallback)).toBe("Speaker 2");
    expect(voiceName(3, [], fallback)).toBe("Speaker 3");
    expect(voiceName(1, ["  Miko  "], fallback)).toBe("Miko");
  });

  it("labels a line when the speaker name changes", () => {
    const lines = [1, 1, 2, null, 2, 1].map((voice) => ({ voice }));
    expect(voiceLabels(lines, [], fallback)).toEqual(["Speaker 1", null, "Speaker 2", null, "Speaker 2", "Speaker 1"]);
    expect(voiceLabels([], [], fallback)).toEqual([]);
  });

  it("treats two labels renamed to the same name as one person", () => {
    const lines = [1, 3, 2].map((voice) => ({ voice }));
    expect(voiceLabels(lines, ["Pekora", "Miko", "Pekora"], fallback)).toEqual(["Pekora", null, "Miko"]);
    expect(voiceColor("Pekora")).toBe(voiceColor("Pekora"));
  });

  it("picks a chip colour in range", () => {
    for (const name of ["Speaker 1", "Speaker 2", "ぺこら", ""]) {
      const c = voiceColor(name);
      expect(Number.isInteger(c) && c >= 0 && c < VOICE_COLORS).toBe(true);
    }
    expect(new Set(["Speaker 1", "Speaker 2", "Speaker 3"].map(voiceColor)).size).toBeGreaterThan(1);
  });
});
