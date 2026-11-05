/**
 * Every engine message code has a dictionary entry with exactly its parameters.
 *
 * The code list is `src/kikitori/hardware/messages.py` `CATALOG`, copied to
 * `lib/testdata/engine-message-codes.json` by `app/dev/extract_message_codes.py`
 * (rerun it when the engine adds a code; this test then names the missing keys).
 */

import { describe, expect, it } from "vitest";

import catalog from "../lib/testdata/engine-message-codes.json";
import { DICTIONARIES } from "./index";
import type { PluralMessage } from "./types";

interface CodeSpec {
  params: string[];
  types: Record<string, "str" | "int" | "float">;
  english: string;
}

const codes = Object.entries(catalog.codes as Record<string, CodeSpec>);
const dictionaries = Object.entries(DICTIONARIES) as [string, Record<string, string | PluralMessage>][];

function placeholders(text: string): string[] {
  return [...new Set([...text.matchAll(/\{([A-Za-z0-9_]+)\}/g)].map((m) => m[1]!))].sort();
}

describe("engine message code list", () => {
  it("was extracted from the engine catalog", () => {
    expect(codes.length).toBeGreaterThan(50);
    for (const [code, spec] of codes) {
      expect(code).toMatch(/^[a-z0-9_]+$/);
      // The English template uses exactly the declared params (format specs aside).
      const inTemplate = [...spec.english.matchAll(/\{([A-Za-z0-9_]+)(?::[^}]*)?\}/g)].map((m) => m[1]!);
      expect(new Set(inTemplate), code).toEqual(new Set(spec.params));
    }
  });
});

describe.each(dictionaries)("dictionary %s", (_locale, messages) => {
  it.each(codes)("translates engine.%s with the same parameters", (code, spec) => {
    const text = messages[`engine.${code}`];
    expect(typeof text, `engine.${code} is missing`).toBe("string");
    expect(placeholders(text as string)).toEqual([...spec.params].sort());
  });
});
