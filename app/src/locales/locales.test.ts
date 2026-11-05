import { describe, expect, it } from "vitest";

import indexHtml from "../../index.html?raw";
import overlayHtml from "../../overlay.html?raw";
import { DICTIONARIES, NATIVE_NAMES, UI_LOCALES } from "./index";
import type { PluralMessage } from "./types";
import { zhTW } from "./zh-TW";

const baseKeys = Object.keys(zhTW).sort();
const dictionaries = Object.entries(DICTIONARIES) as [string, Record<string, string | PluralMessage>][];

function placeholders(text: string): string[] {
  return [...text.matchAll(/\{([A-Za-z0-9_]+)\}/g)].map((m) => m[1]!).sort();
}

/** All texts of a message (one for a string, every form for a plural). */
function texts(v: string | PluralMessage): string[] {
  return typeof v === "string" ? [v] : Object.values(v).filter((x): x is string => typeof x === "string");
}

describe("locale registry", () => {
  it("lists the 13 interface locales, each with a native name", () => {
    expect(UI_LOCALES).toHaveLength(13);
    for (const l of UI_LOCALES) expect(NATIVE_NAMES[l].trim(), l).not.toBe("");
  });

  it("registers dictionaries only for listed locales, including the base and the English fallback", () => {
    for (const [l] of dictionaries) expect(UI_LOCALES as readonly string[], l).toContain(l);
    expect(DICTIONARIES["zh-TW"]).toBe(zhTW);
    expect(DICTIONARIES.en).toBeDefined();
  });
});

describe.each(dictionaries)("dictionary %s", (locale, messages) => {
  it("has exactly the base keys", () => {
    expect(Object.keys(messages).sort()).toEqual(baseKeys);
  });

  it("has no empty messages", () => {
    for (const [key, v] of Object.entries(messages)) {
      for (const text of texts(v)) expect(text.trim(), key).not.toBe("");
    }
  });

  it("uses the same placeholders as the base for every key and plural form", () => {
    for (const [key, v] of Object.entries(messages)) {
      const base = zhTW[key as keyof typeof zhTW];
      const expected = placeholders(texts(base)[0]!);
      for (const text of texts(v)) expect(placeholders(text), `${locale} ${key}: ${text}`).toEqual(expected);
    }
  });

  it("is plural exactly where the base is, with every category the locale needs", () => {
    const categories = new Intl.PluralRules(locale).resolvedOptions().pluralCategories;
    for (const [key, v] of Object.entries(messages)) {
      const base = zhTW[key as keyof typeof zhTW];
      expect(typeof v, key).toBe(typeof base);
      if (typeof v === "string") continue;
      for (const c of categories) expect(v[c], `${locale} ${key} needs "${c}"`).toBeTypeOf("string");
      const extra = Object.keys(v).filter((c) => !(categories as readonly string[]).includes(c));
      expect(extra, `${locale} ${key} has categories the locale never selects`).toEqual([]);
    }
  });
});

describe("static markup", () => {
  it.each([
    ["index.html", indexHtml],
    ["overlay.html", overlayHtml],
  ])("%s only uses keys that exist", (_name, html) => {
    const keys = [...html.matchAll(/data-i18n(?:-title|-placeholder|-aria-label)?="([^"]+)"/g)].map((m) => m[1]!);
    expect(keys.length).toBeGreaterThan(0);
    for (const key of keys) expect(baseKeys, key).toContain(key);
  });
});
