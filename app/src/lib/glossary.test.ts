import { describe, expect, it } from "vitest";

import {
  DEFAULT_GLOSSARY,
  GLOSSARY_MAX_ENTRIES,
  glossaryErrorForEnginePath,
  glossaryFileName,
  glossaryFromSettings,
  glossaryPatch,
  glossaryToCsv,
  hasGlossaryErrors,
  matchKey,
  mergeGlossary,
  parseGlossaryCsv,
  validateGlossary,
} from "./glossary";
import { t } from "./i18n";
import type { GlossaryEntry } from "./types";

const LANGS = ["ja", "en", "zh-TW", "zh-CN", "ko"];

function entry(source: string, target: string, target_language: string | null = null): GlossaryEntry {
  return { source, target, target_language };
}

describe("glossaryFromSettings", () => {
  it("defaults quietly when an older engine has no glossary", () => {
    const warn: string[] = [];
    expect(glossaryFromSettings(undefined, (f) => warn.push(f))).toEqual(DEFAULT_GLOSSARY);
    expect(warn).toEqual([]);
  });

  it("reads the engine's shape", () => {
    const raw = {
      builtin: false,
      entries: [
        { source: "スパチャ", target: "超級留言", target_language: "zh-TW" },
        { source: "草", target: "lol", target_language: null },
      ],
      corrections: [{ wrong: "ぺこら", right: "ぺこーら" }],
    };
    expect(glossaryFromSettings(raw, () => undefined)).toEqual(raw);
  });

  it("keeps the well-formed entries and reports the rest", () => {
    const warn: string[] = [];
    const g = glossaryFromSettings(
      { builtin: "yes", entries: [{ source: "a", target: "b" }, { source: 1, target: "x" }, "junk"] },
      (f) => warn.push(f),
    );
    expect(g).toEqual({ builtin: true, entries: [entry("a", "b")], corrections: [] });
    expect(warn).toEqual(["translation.glossary.builtin", "translation.glossary.entries"]);
    const warn2: string[] = [];
    expect(glossaryFromSettings([1], (f) => warn2.push(f))).toEqual(DEFAULT_GLOSSARY);
    expect(warn2).toEqual(["translation.glossary"]);
  });
});

describe("matchKey", () => {
  it("normalizes width and case like the engine (NFKC + casefold + strip)", () => {
    expect(matchKey("ｽﾊﾟﾁｬ")).toBe("スパチャ");
    expect(matchKey("ＷＷＷ")).toBe("www");
    expect(matchKey("Straße")).toBe(matchKey("STRASSE"));
    expect(matchKey("ΟΔΟΣ")).toBe(matchKey("οδοσ"));
  });
});

describe("validateGlossary", () => {
  it("accepts a valid list", () => {
    const errors = validateGlossary([entry("スパチャ", "超級留言", "zh-TW"), entry("草", "笑")], LANGS);
    expect(hasGlossaryErrors(errors)).toBe(false);
  });

  it("checks each field like the engine", () => {
    const errors = validateGlossary(
      [
        entry("", "x"),
        entry(" padded", "x"),
        entry("a".repeat(101), "x"),
        entry("tab\there", "x"),
        entry("ok", "b".repeat(201)),
        entry("ok2", "line\nbreak"),
        entry("ok3", "x", "xx-unknown"),
        entry("zero​width", "x"),
        entry("ideo　space", "x"),
      ],
      LANGS,
    );
    expect(errors.rows.get(0)).toEqual({ source: t("glossary.error.empty") });
    expect(errors.rows.get(1)).toEqual({ source: t("glossary.error.spaces") });
    expect(errors.rows.get(2)).toEqual({ source: t("glossary.error.tooLong", { max: 100 }) });
    expect(errors.rows.get(3)).toEqual({ source: t("glossary.error.control") });
    expect(errors.rows.get(4)).toEqual({ target: t("glossary.error.tooLong", { max: 200 }) });
    expect(errors.rows.get(5)).toEqual({ target: t("glossary.error.control") });
    expect(errors.rows.get(6)).toEqual({ target_language: t("glossary.error.language") });
    expect(errors.rows.get(7)).toEqual({ source: t("glossary.error.control") });
    expect(errors.rows.get(8)).toEqual({ source: t("glossary.error.control") });
  });

  it("counts length in code points, not UTF-16 units", () => {
    // 100 characters outside the BMP are 200 UTF-16 units but within the limit.
    expect(validateGlossary([entry("𠮷".repeat(100), "x")], LANGS).rows.size).toBe(0);
    expect(validateGlossary([entry("𠮷".repeat(101), "x")], LANGS).rows.size).toBe(1);
  });

  it("flags duplicates on the later row, per subtitle language", () => {
    const errors = validateGlossary(
      [entry("スパチャ", "A", "zh-TW"), entry("ｽﾊﾟﾁｬ", "B", "zh-TW"), entry("スパチャ", "C"), entry("スパチャ", "D", "en")],
      LANGS,
    );
    expect([...errors.rows.keys()]).toEqual([1]);
    expect(errors.rows.get(1)?.source).toBe(t("glossary.error.duplicate", { row: 1 }));
  });

  it("checks only the code format while the language list is unknown", () => {
    expect(validateGlossary([entry("a", "b", "fr")], null).rows.size).toBe(0);
    expect(validateGlossary([entry("a", "b", "<b>")], null).rows.size).toBe(1);
  });

  it("limits the number of entries", () => {
    const many = Array.from({ length: GLOSSARY_MAX_ENTRIES + 1 }, (_, i) => entry(`t${i}`, "x"));
    const errors = validateGlossary(many, LANGS);
    expect(errors.list).toBe(t("glossary.error.tooMany", { max: GLOSSARY_MAX_ENTRIES, count: GLOSSARY_MAX_ENTRIES + 1 }));
    expect(validateGlossary(many.slice(1), LANGS).list).toBeNull();
  });
});

describe("glossaryPatch", () => {
  it("sends the whole list (settings.set replaces lists)", () => {
    const corrections = [{ wrong: "apex", right: "Apex" }];
    expect(glossaryPatch({ builtin: false, entries: [entry("a", "b", "en")], corrections })).toEqual({
      translation: {
        glossary: { builtin: false, entries: [{ source: "a", target: "b", target_language: "en" }], corrections },
      },
    });
  });
});

describe("glossaryErrorForEnginePath", () => {
  it("maps entry paths to the row and field", () => {
    expect(glossaryErrorForEnginePath("translation.glossary.entries[3].source: duplicate of an earlier entry")).toEqual({
      kind: "entry",
      index: 3,
      field: "source",
    });
    expect(glossaryErrorForEnginePath("translation.glossary.entries[0].target_language: unknown language")).toEqual({
      kind: "entry",
      index: 0,
      field: "target_language",
    });
    expect(glossaryErrorForEnginePath("translation.glossary.entries[12]: expected an object, got str")).toEqual({
      kind: "entry",
      index: 12,
      field: null,
    });
    expect(glossaryErrorForEnginePath("translation.glossary.entries[2].note: unknown field")).toEqual({
      kind: "entry",
      index: 2,
      field: null,
    });
  });

  it("maps list, switch and unrelated paths", () => {
    expect(glossaryErrorForEnginePath("translation.glossary.entries: expected a list of at most 500 entries")).toEqual({ kind: "list" });
    expect(glossaryErrorForEnginePath("translation.glossary: unknown field x")).toEqual({ kind: "list" });
    expect(glossaryErrorForEnginePath("translation.glossary.builtin: expected a boolean")).toEqual({ kind: "builtin" });
    expect(glossaryErrorForEnginePath("translation.base_url: bad")).toBeNull();
  });
});

describe("glossary CSV", () => {
  const entries = [
    entry("スパチャ", "超級留言", "zh-TW"),
    entry("草", "lol, really", "en"),
    entry('say "hi"', "multi\nline?", null),
    entry("=SUM(A1)", "-1", null),
    entry("- dash", "+plus", null),
  ];

  it("writes UTF-8 with BOM, a header, CRLF rows and guarded formulas", () => {
    const csv = glossaryToCsv(entries);
    expect(csv.startsWith("﻿source,target,target_language\r\n")).toBe(true);
    expect(csv).toContain('草,"lol, really",en\r\n');
    expect(csv).toContain('"say ""hi""","multi\nline?",\r\n');
    expect(csv).toContain("'=SUM(A1),'-1,\r\n");
    expect(csv.endsWith("\r\n")).toBe(true);
  });

  it("reads back exactly what it wrote", () => {
    const parsed = parseGlossaryCsv(glossaryToCsv(entries));
    expect(parsed.badLines).toEqual([]);
    expect(parsed.entries).toEqual(entries);
  });

  it("reads files without BOM or header, with LF or CR endings", () => {
    expect(parseGlossaryCsv("a,b\nc,d,ja\n").entries).toEqual([entry("a", "b"), entry("c", "d", "ja")]);
    expect(parseGlossaryCsv("a,b\rc,d").entries).toEqual([entry("a", "b"), entry("c", "d")]);
  });

  it("finds columns by header name in any order", () => {
    const csv = "Target_Language,Target,Source,Note\nen,lol,草,x\n,超級留言,スパチャ,\n";
    expect(parseGlossaryCsv(csv).entries).toEqual([entry("草", "lol", "en"), entry("スパチャ", "超級留言")]);
    expect(parseGlossaryCsv("source,target\na,b\n").entries).toEqual([entry("a", "b")]);
  });

  it("accepts semicolon and tab separated files (spreadsheets in many locales)", () => {
    expect(parseGlossaryCsv("source;target;target_language\na;b,c;en\n").entries).toEqual([entry("a", "b,c", "en")]);
    expect(parseGlossaryCsv("a\tb\tja\n").entries).toEqual([entry("a", "b", "ja")]);
  });

  it("treats empty, * and all as every language, and trims padding", () => {
    const csv = "a,b,*\nc,d,ALL\n  e , f ,\n";
    expect(parseGlossaryCsv(csv).entries).toEqual([entry("a", "b"), entry("c", "d"), entry("e", "f")]);
    expect(parseGlossaryCsv('"x" , "y"\n').entries).toEqual([entry("x", "y")]);
    // Text after a closing quote is kept with the spaces before it (hand-edited files).
    expect(parseGlossaryCsv('"hello" world,x\n').entries).toEqual([entry("hello world", "x")]);
  });

  it("skips blank lines and reports rows without a target by line number", () => {
    const csv = "source,target\n\na,b\nonly-source\n,\n\"multi\nline\",x\nlast,\n";
    const parsed = parseGlossaryCsv(csv);
    expect(parsed.entries).toEqual([entry("a", "b"), entry("multi\nline", "x")]);
    expect(parsed.badLines).toEqual([4, 8]);
  });

  it("keeps a quote inside an unquoted field and an apostrophe that guards nothing", () => {
    expect(parseGlossaryCsv('it"s,\'quoted\n').entries).toEqual([entry('it"s', "'quoted")]);
  });
});

describe("mergeGlossary", () => {
  it("appends new rows, skips exact repeats and keeps conflicts for the user", () => {
    const existing = [entry("スパチャ", "超級留言", "zh-TW")];
    const merged = mergeGlossary(existing, [
      entry("ｽﾊﾟﾁｬ", "超級留言", "zh-TW"),
      entry("スパチャ", "斗內", "zh-TW"),
      entry("草", "笑"),
      entry("草", "笑"),
    ]);
    expect(merged.entries).toEqual([...existing, entry("スパチャ", "斗內", "zh-TW"), entry("草", "笑")]);
    expect(merged.added).toBe(2);
    expect(merged.skipped).toBe(2);
    // The conflict is then reported by validation.
    expect(validateGlossary(merged.entries, LANGS).rows.get(1)?.source).toBe(t("glossary.error.duplicate", { row: 1 }));
  });
});

describe("glossaryFileName", () => {
  it("follows the transcript file name rule (ASCII, .csv)", () => {
    expect(glossaryFileName(new Date(2026, 10, 6, 9, 5, 7))).toBe("glossary_2026-11-06_090507.csv");
    expect(glossaryFileName(new Date())).toMatch(/^[A-Za-z0-9][A-Za-z0-9_-]*\.csv$/);
  });
});
