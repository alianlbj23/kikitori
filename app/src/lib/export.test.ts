import { describe, expect, it } from "vitest";

import {
  EXPORT_FORMATS,
  MIN_CUE_MS,
  bidiSafe,
  csvField,
  cuesFromLines,
  escapeVtt,
  exportTranscript,
  formatTimestamp,
  hasExportableLines,
  interpretNamer,
  oneLine,
  startsRtl,
  transcriptFileName,
} from "./export";
import type { SubtitleLine } from "./subtitles";

function line(id: number, original: string, translation: string, startS: number, endS: number, isFinal = true): SubtitleLine {
  return {
    utteranceId: id,
    original,
    translation,
    sourceLanguage: "ja",
    targetLanguage: "zh-TW",
    isFinal,
    startSample: Math.round(startS * 16_000),
    endSample: Math.round(endS * 16_000),
    stableChars: null,
    translationStableChars: null,
    speaker: null,
    voice: null,
    ruby: null,
  };
}

const LINES: SubtitleLine[] = [
  line(1, "こんにちは", "你好", 1.5, 3.25),
  line(2, "途中", "", 4, 5, false), // partial: never exported
  line(3, "ありがとう", "謝謝", 3661.001, 3662.5),
];

describe("timestamps", () => {
  it("formats hours, minutes, seconds and milliseconds", () => {
    expect(formatTimestamp(0, ",")).toBe("00:00:00,000");
    expect(formatTimestamp(1500, ",")).toBe("00:00:01,500");
    expect(formatTimestamp(3_661_001, ".")).toBe("01:01:01.001");
    expect(formatTimestamp(360_000_000, ".")).toBe("100:00:00.000");
    expect(formatTimestamp(-5, ",")).toBe("00:00:00,000");
  });

  it("derives cue times from 16 kHz samples and keeps end after start", () => {
    const cues = cuesFromLines(
      [line(1, "a", "A", 2, 2), line(2, "b", "B", 3, 2.5), line(3, "c", "C", 0.0001, 0.5)],
      "both",
    );
    expect(cues.map((c) => [c.startMs, c.endMs])).toEqual([
      [2000, 2000 + MIN_CUE_MS],
      [3000, 3000 + MIN_CUE_MS],
      [0, 500],
    ]);
    for (const c of cues) expect(c.endMs).toBeGreaterThan(c.startMs);
  });
});

describe("cues", () => {
  it("exports only final lines with text, per content choice", () => {
    expect(cuesFromLines(LINES, "both").map((c) => c.text)).toEqual([
      ["こんにちは", "你好"],
      ["ありがとう", "謝謝"],
    ]);
    expect(cuesFromLines(LINES, "original").map((c) => c.text)).toEqual([["こんにちは"], ["ありがとう"]]);
    expect(cuesFromLines(LINES, "translation").map((c) => c.text)).toEqual([["你好"], ["謝謝"]]);
  });

  it("uses the original when there is no translation and drops empty lines", () => {
    const lines = [line(1, "原文だけ", "", 0, 1), line(2, "  ", "", 1, 2), line(3, "", "  ", 2, 3)];
    expect(cuesFromLines(lines, "both").map((c) => c.text)).toEqual([["原文だけ"]]);
    expect(cuesFromLines(lines, "translation").map((c) => c.text)).toEqual([["原文だけ"]]);
    expect(hasExportableLines(lines)).toBe(true);
    expect(hasExportableLines([line(1, "a", "b", 0, 1, false)])).toBe(false);
  });

  it("collapses line breaks and control characters so a cue cannot be split", () => {
    expect(oneLine(" a\r\n\nb\tc\u0000d\u2028e ")).toBe("a b c d e");
  });
});

describe("SRT", () => {
  it("numbers cues and puts the translation under the original", () => {
    expect(exportTranscript(LINES, "srt", "both")).toBe(
      "1\r\n00:00:01,500 --> 00:00:03,250\r\nこんにちは\r\n你好\r\n" +
        "\r\n" +
        "2\r\n01:01:01,001 --> 01:01:02,500\r\nありがとう\r\n謝謝\r\n",
    );
  });

  it("keeps arrows in the text from looking like a timing line", () => {
    const srt = exportTranscript([line(1, "a --> b", "", 0, 1)], "srt", "original");
    expect(srt.split("\r\n").filter((l) => l.includes("-->"))).toHaveLength(1);
    expect(srt).toContain("a -> b");
  });

  it("is empty when there is nothing final", () => {
    expect(exportTranscript([line(1, "a", "", 0, 1, false)], "srt", "both")).toBe("");
  });
});

describe("WebVTT", () => {
  it("starts with the header and uses dots before milliseconds", () => {
    expect(exportTranscript(LINES.slice(0, 1), "vtt", "translation")).toBe(
      "WEBVTT\n\n1\n00:00:01.500 --> 00:00:03.250\n你好\n",
    );
    expect(exportTranscript([], "vtt", "both")).toBe("WEBVTT\n");
  });

  it("escapes markup characters, which also rules out a literal arrow", () => {
    expect(escapeVtt("a < b && c --> d")).toBe("a &lt; b &amp;&amp; c --&gt; d");
    const vtt = exportTranscript([line(1, "<b>x</b> -->", "", 0, 1)], "vtt", "original");
    expect(vtt).toContain("&lt;b&gt;x&lt;/b&gt; --&gt;");
    expect(vtt.split("\n").filter((l) => l.includes("-->"))).toHaveLength(1);
  });
});

describe("TXT", () => {
  it("prefixes each cue with its start time", () => {
    expect(exportTranscript(LINES, "txt", "both")).toBe(
      "[00:00:01] こんにちは\r\n你好\r\n\r\n[01:01:01] ありがとう\r\n謝謝\r\n",
    );
  });
});

describe("CSV", () => {
  it("has a BOM, a header, CRLF rows and the chosen columns", () => {
    const csv = exportTranscript(LINES, "csv", "both");
    expect(csv.startsWith("\uFEFFindex,start,end,source_language,target_language,original,translation\r\n")).toBe(true);
    expect(csv).toContain("\r\n1,00:00:01.500,00:00:03.250,ja,zh-TW,こんにちは,你好\r\n");
    expect(csv.endsWith("\r\n")).toBe(true);
    expect(exportTranscript(LINES, "csv", "original").split("\r\n")[0]).toBe(
      "\uFEFFindex,start,end,source_language,target_language,original",
    );
    expect(exportTranscript(LINES, "csv", "translation").split("\r\n")[0]).toBe(
      "\uFEFFindex,start,end,source_language,target_language,translation",
    );
  });

  it("quotes commas, quotes and line breaks", () => {
    expect(csvField("plain")).toBe("plain");
    expect(csvField("a, b")).toBe('"a, b"');
    expect(csvField('say "hi"')).toBe('"say ""hi"""');
    expect(csvField("a\nb")).toBe('"a\nb"');
  });

  it("defuses spreadsheet formulas but keeps dialogue dashes", () => {
    expect(csvField("=HYPERLINK(1)")).toBe("'=HYPERLINK(1)");
    expect(csvField("+1")).toBe("'+1");
    expect(csvField("@SUM")).toBe("'@SUM");
    expect(csvField("-2+3")).toBe("'-2+3");
    expect(csvField("- dialogue")).toBe("- dialogue");
    expect(csvField('=A1,"x"')).toBe('"\'=A1,""x"""');
  });
});

describe("right-to-left text", () => {
  it("detects the direction from the first letter", () => {
    expect(startsRtl("مرحبا بالعالم")).toBe(true);
    expect(startsRtl("«שלום»")).toBe(true);
    expect(startsRtl("123 مرحبا")).toBe(true);
    expect(startsRtl("Hello مرحبا")).toBe(false);
    expect(startsRtl("")).toBe(false);
  });

  it("wraps right-to-left lines in RLM marks in SRT, VTT and TXT but not CSV", () => {
    expect(bidiSafe("مرحبا!")).toBe("\u200Fمرحبا!\u200F");
    expect(bidiSafe("hello!")).toBe("hello!");
    const lines = [{ ...line(1, "こんにちは", "مرحبا!", 0, 1), targetLanguage: "ar" }];
    expect(exportTranscript(lines, "srt", "both")).toContain("\r\n\u200Fمرحبا!\u200F\r\n");
    expect(exportTranscript(lines, "vtt", "both")).toContain("\n\u200Fمرحبا!\u200F\n");
    expect(exportTranscript(lines, "txt", "both")).toContain("\u200Fمرحبا!\u200F");
    expect(exportTranscript(lines, "csv", "both")).not.toContain("\u200F");
  });
});

describe("file names", () => {
  it("are local date and time of the session start, mode and format", () => {
    const at = new Date(2026, 10, 6, 9, 5, 7);
    expect(transcriptFileName(at, "media", "srt")).toBe("2026-11-06_090507_media.srt");
    expect(transcriptFileName(at, "interpret", "csv")).toBe("2026-11-06_090507_interpret.csv");
  });

  it("match the rule Rust enforces (validate_transcript_name)", () => {
    const rust = /^[A-Za-z0-9][A-Za-z0-9_-]*\.(srt|vtt|txt|csv)$/;
    for (const format of EXPORT_FORMATS) {
      const name = transcriptFileName(new Date(), "media", format);
      expect(name).toMatch(rust);
      expect(name.length).toBeLessThanOrEqual(100);
    }
  });
});

describe("speaker labels in interpret exports (G3)", () => {
  const names = interpretNamer({ self: "你", partner: "對方" });
  const lines: SubtitleLine[] = [
    { ...line(1, "請問車站在哪裡", "Where is the station?", 0, 2), speaker: "self" },
    { ...line(2, "Over there.", "在那裡。", 3, 4), speaker: "partner" },
  ];

  it("prefixes the first text line with the speaker's name", () => {
    const cues = cuesFromLines(lines, "both", names);
    expect(cues[0]!.text).toEqual(["你: 請問車站在哪裡", "Where is the station?"]);
    expect(cues[1]!.text).toEqual(["對方: Over there.", "在那裡。"]);
    expect(cues.map((c) => c.who)).toEqual(["你", "對方"]);
    // The plain fields stay unprefixed.
    expect(cues[0]!.original).toBe("請問車站在哪裡");
    expect(exportTranscript(lines, "txt", "translation", names)).toContain("你: Where is the station?");
  });

  it("adds a speaker column to CSV instead of prefixing the text", () => {
    const csv = exportTranscript(lines, "csv", "both", names);
    const [header, first] = csv.replace("﻿", "").split("\r\n");
    expect(header).toBe("index,start,end,source_language,target_language,speaker,original,translation");
    expect(first).toContain(",你,請問車站在哪裡,");
  });

  it("labels nothing without names or in media mode", () => {
    expect(cuesFromLines(lines, "both")[0]!.text[0]).toBe("請問車站在哪裡");
    const media = lines.map((l) => ({ ...l, speaker: null }));
    expect(cuesFromLines(media, "both", names)[0]!.text[0]).toBe("請問車站在哪裡");
    expect(exportTranscript(media, "csv", "both", names)).not.toContain("speaker");
  });
});

describe("speaker labels in media exports (speaker-labels.md)", () => {
  const given = ["ぺこら"];
  const nameOf = (l: SubtitleLine) => (l.voice === null ? null : (given[l.voice - 1] ?? `說話者 ${l.voice}`));
  const lines: SubtitleLine[] = [
    { ...line(1, "こんぺこ", "大家好", 0, 2), voice: 1 },
    { ...line(2, "やっほー", "嗨", 3, 4), voice: 2 },
    { ...line(3, "えー", "欸", 5, 6), voice: null },
  ];

  it("prefixes named lines and leaves unlabelled ones as they are", () => {
    const cues = cuesFromLines(lines, "both", nameOf);
    expect(cues.map((c) => c.text[0])).toEqual(["ぺこら: こんぺこ", "說話者 2: やっほー", "えー"]);
    expect(cues.map((c) => c.who)).toEqual(["ぺこら", "說話者 2", null]);
  });

  it("puts the names in the CSV speaker column, blank when unlabelled", () => {
    const rows = exportTranscript(lines, "csv", "original", nameOf).replace("\uFEFF", "").split("\r\n");
    expect(rows[0]).toBe("index,start,end,source_language,target_language,speaker,original");
    expect(rows[1]).toContain(",ぺこら,こんぺこ");
    expect(rows[3]).toMatch(/,,えー$/);
  });

  it("keeps a renamed line on one line in the file", () => {
    const odd = (l: SubtitleLine) => (l.voice === 1 ? "A\nB" : null);
    expect(cuesFromLines(lines, "original", odd)[0]!.text[0]).toBe("A B: こんぺこ");
  });
});
