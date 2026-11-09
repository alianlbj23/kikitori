import { describe, expect, it } from "vitest";

import {
  CORRECTIONS_MAX,
  correctionsFromSettings,
  correctionsToText,
  parseCorrectionsText,
} from "./corrections";

describe("parseCorrectionsText", () => {
  it("reads one rule per line and skips blank lines", () => {
    const parsed = parseCorrectionsText("ぺこら => ぺこーら\n\n  apex→Apex  \r\nホロ ライブ=>ホロライブ");
    expect(parsed.errors).toEqual([]);
    expect(parsed.entries).toEqual([
      { wrong: "ぺこら", right: "ぺこーら" },
      { wrong: "apex", right: "Apex" },
      { wrong: "ホロ ライブ", right: "ホロライブ" },
    ]);
  });

  it("points each problem at its line", () => {
    const text = ["no separator", "a => b => c", " => x", "same => same", "Apex => A", "APEX => B", `${"あ".repeat(101)} => x`].join(
      "\n",
    );
    const { entries, errors } = parseCorrectionsText(text);
    expect(entries).toEqual([{ wrong: "Apex", right: "A" }]);
    expect(errors.map((e) => e.line)).toEqual([1, 2, 3, 4, 6, 7]);
    // The duplicate names the first line with that spelling.
    expect(errors[4]!.message).toContain("5");
  });

  it("reports too many rules as a whole", () => {
    const text = Array.from({ length: CORRECTIONS_MAX + 1 }, (_, i) => `w${i} => r`).join("\n");
    const { errors } = parseCorrectionsText(text);
    expect(errors).toHaveLength(1);
    expect(errors[0]!.line).toBe(0);
  });

  it("round-trips through the text form", () => {
    const entries = [
      { wrong: "ぺこら", right: "ぺこーら" },
      { wrong: "apex", right: "Apex" },
    ];
    expect(parseCorrectionsText(correctionsToText(entries)).entries).toEqual(entries);
    expect(correctionsToText([])).toBe("");
  });
});

describe("correctionsFromSettings", () => {
  it("keeps well-formed rules and reports the rest", () => {
    const warned: string[] = [];
    const warn = (f: string) => warned.push(f);
    expect(correctionsFromSettings(undefined, warn)).toEqual([]);
    expect(correctionsFromSettings([{ wrong: "a", right: "b" }, { wrong: 1 }], warn)).toEqual([{ wrong: "a", right: "b" }]);
    expect(correctionsFromSettings("x", warn)).toEqual([]);
    expect(warned).toEqual(["translation.glossary.corrections", "translation.glossary.corrections"]);
  });
});
