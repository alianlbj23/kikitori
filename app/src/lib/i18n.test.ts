import { afterEach, describe, expect, it, vi } from "vitest";

import { describeError } from "./api";
import { describeStatus, languageShort, regionNames, threadsLabel } from "./format";
import {
  UI_LOCALES,
  applyStatic,
  format,
  getLocale,
  htmlLangForLocale,
  languageName,
  localeForPreference,
  onLocaleChange,
  regionName,
  resolveLocale,
  setLocale,
  t,
  tDynamic,
  tp,
} from "./i18n";
import { validateSettingsForm } from "./settings";

afterEach(() => {
  setLocale("zh-TW");
  vi.restoreAllMocks();
});

describe("resolveLocale", () => {
  it.each<[string[], string]>([
    [["zh-TW"], "zh-TW"],
    [["zh-Hant"], "zh-TW"],
    [["zh-Hant-HK"], "zh-TW"],
    [["zh-HK"], "zh-TW"],
    [["zh-MO"], "zh-TW"],
    [["zh_TW"], "zh-TW"],
    [["zh"], "zh-CN"],
    [["zh-CN"], "zh-CN"],
    [["zh-Hans"], "zh-CN"],
    [["zh-SG"], "zh-CN"],
    [["pt"], "pt-BR"],
    [["pt-PT"], "pt-BR"],
    [["pt-BR"], "pt-BR"],
    [["en-US"], "en"],
    [["en-GB"], "en"],
    [["ja-JP"], "ja"],
    [["ko-KR"], "ko"],
    [["es-419"], "es"],
    [["fr-CA"], "fr"],
    [["de-AT"], "de"],
    [["ru"], "ru"],
    [["vi-VN"], "vi"],
    [["th-TH"], "th"],
    [["id-ID"], "id"],
  ])("%j → %s", (preferred, expected) => {
    expect(resolveLocale(preferred)).toBe(expected);
  });

  it("takes the first preferred language it supports, and falls back to English", () => {
    expect(resolveLocale(["sw-KE", "nl", "ja", "en"])).toBe("ja");
    expect(resolveLocale(["sw-KE", "nl"])).toBe("en");
    expect(resolveLocale([])).toBe("en");
    expect(resolveLocale(["", "x"])).toBe("en");
  });

  it("follows the system for the 'system' preference only", () => {
    expect(localeForPreference("system", ["ko-KR"])).toBe("ko");
    expect(localeForPreference("de", ["ko-KR"])).toBe("de");
  });
});

describe("t and tp", () => {
  it("fills {name} placeholders and leaves unknown ones visible", () => {
    expect(format("{a} and {b}", { a: 1 })).toBe("1 and {b}");
    setLocale("en");
    expect(t("device.defaultNamed", { name: "Speakers" })).toBe("Default: Speakers");
    setLocale("zh-TW");
    expect(t("device.defaultNamed", { name: "喇叭" })).toBe("預設：喇叭");
  });

  it("does not interpret placeholder-like text inside parameter values", () => {
    setLocale("en");
    expect(t("device.defaultNamed", { name: "{count}" })).toBe("Default: {count}");
  });

  it("picks plural forms with Intl.PluralRules", () => {
    setLocale("en");
    expect(tp("threads.count", 1)).toBe("1 thread");
    expect(tp("threads.count", 4)).toBe("4 threads");
    expect(threadsLabel(1)).toBe("1 thread");
    setLocale("zh-TW");
    expect(tp("threads.count", 1)).toBe("1 個執行緒");
  });

  it("falls back to English for a locale without a dictionary yet, with English plural rules", () => {
    setLocale("ru");
    expect(t("start.start")).toBe("Start");
    // Russian would pick "few" for 3; the English forms only have one/other.
    expect(tp("threads.count", 3)).toBe("3 threads");
  });

  it("looks up runtime keys only when they exist as plain strings", () => {
    setLocale("en");
    expect(tDynamic("start.stop")).toBe("Stop");
    expect(tDynamic("engine.not_a_code")).toBeNull();
    expect(tDynamic("threads.count")).toBeNull();
  });

  it("switches library wording with the locale", () => {
    setLocale("en");
    expect(describeStatus(null, "idle").label).toBe("Starting the engine");
    expect(describeError({ kind: "timeout", method: "hardware.detect", timeout_ms: 45_000 })).toBe(
      "The engine did not respond within 45 seconds. Try again later or restart the engine.",
    );
    expect(validateSettingsForm({
      base_url: "ftp://x",
      api_key_env: "OK",
      allow_restricted: false,
      font_size: "32",
      background_opacity_percent: "50",
      position: "bottom",
      max_lines: "2",
    }).base_url).toBe("The URL must start with http:// or https://.");
  });
});

describe("locale state", () => {
  it("notifies listeners on every setLocale and stops after unsubscribing", () => {
    const seen: string[] = [];
    const off = onLocaleChange((l) => seen.push(l));
    setLocale("en");
    setLocale("en");
    off();
    setLocale("ja");
    expect(seen).toEqual(["en", "en"]);
    expect(getLocale()).toBe("ja");
  });

  it("keeps notifying other listeners when one throws", () => {
    vi.spyOn(console, "error").mockImplementation(() => undefined);
    const seen: string[] = [];
    const offA = onLocaleChange(() => {
      throw new Error("boom");
    });
    const offB = onLocaleChange((l) => seen.push(l));
    setLocale("fr");
    offA();
    offB();
    expect(seen).toEqual(["fr"]);
    expect(console.error).toHaveBeenCalled();
  });

  it("maps Chinese locales to script-tagged html lang values", () => {
    expect(htmlLangForLocale("zh-TW")).toBe("zh-Hant-TW");
    expect(htmlLangForLocale("zh-CN")).toBe("zh-Hans-CN");
    expect(htmlLangForLocale("pt-BR")).toBe("pt-BR");
    expect(UI_LOCALES).toHaveLength(13);
  });
});

describe("names from Intl", () => {
  it("names languages in the interface language without a dictionary", () => {
    setLocale("en");
    expect(languageName("ja")).toBe("Japanese");
    expect(languageName("zh-TW")).toBe("Traditional Chinese");
    expect(languageName("zh-CN")).toBe("Simplified Chinese");
    setLocale("zh-TW");
    expect(languageName("zh-TW")).toBe("繁體中文");
    expect(languageName("not a code")).toBe("not a code");
  });

  it("names regions, treating UK as GB", () => {
    setLocale("en");
    expect(regionName("KR")).toBe("South Korea");
    expect(regionName("UK")).toBe(regionName("GB"));
    expect(regionName("xx")).toBe("xx");
    expect(regionNames(["EU", "KR"])).toBe("European Union, South Korea");
  });

  it("uses one-character tags in Chinese and Japanese interfaces, subtags elsewhere", () => {
    setLocale("zh-TW");
    expect(languageShort("ja")).toBe("日");
    expect(languageShort("zh-TW")).toBe("中");
    setLocale("ja");
    expect(languageShort("en")).toBe("英");
    setLocale("en");
    expect(languageShort("ja")).toBe("JA");
    expect(languageShort("<x>")).toBe("?");
  });
});

/** Just enough of the DOM for `applyStatic` (tests run in Node). */
class FakeElement {
  textContent = "";
  readonly attrs = new Map<string, string>();
  readonly dataset: Record<string, string> = {};

  constructor(attrs: Record<string, string>) {
    for (const [k, v] of Object.entries(attrs)) this.setAttribute(k, v);
  }

  getAttribute(name: string): string | null {
    return this.attrs.get(name) ?? null;
  }

  setAttribute(name: string, value: string): void {
    this.attrs.set(name, value);
    if (name.startsWith("data-")) {
      const key = name.slice(5).replace(/-([a-z])/g, (_, c: string) => c.toUpperCase());
      this.dataset[key] = value;
    }
  }
}

function fakeRoot(elements: FakeElement[]): ParentNode {
  return {
    querySelectorAll: (selector: string) => {
      const attr = /^\[([a-z0-9-]+)\]$/.exec(selector)?.[1];
      if (!attr) throw new Error(`unsupported selector ${selector}`);
      return elements.filter((e) => e.attrs.has(attr));
    },
  } as unknown as ParentNode;
}

describe("applyStatic", () => {
  it("fills text, attributes, plural text and language names", () => {
    const text = new FakeElement({ "data-i18n": "start.start" });
    const plural = new FakeElement({ "data-i18n": "settings.linesOption", "data-i18n-count": "2" });
    const title = new FakeElement({ "data-i18n-title": "asr.noSpeechHelp" });
    const placeholder = new FakeElement({ "data-i18n-placeholder": "common.auto" });
    const aria = new FakeElement({ "data-i18n-aria-label": "nav.label" });
    const lang = new FakeElement({ "data-i18n-language": "ja" });
    setLocale("en");
    applyStatic(fakeRoot([text, plural, title, placeholder, aria, lang]));
    expect(text.textContent).toBe("Start");
    expect(plural.textContent).toBe("2 sentences");
    expect(title.getAttribute("title")).toContain("cannot reliably tell");
    expect(placeholder.getAttribute("placeholder")).toBe("Auto");
    expect(aria.getAttribute("aria-label")).toBe("Pages");
    expect(lang.textContent).toBe("Japanese");
  });

  it("leaves elements with unknown keys untouched and logs them", () => {
    const error = vi.spyOn(console, "error").mockImplementation(() => undefined);
    const unknown = new FakeElement({ "data-i18n": "no.such.key" });
    unknown.textContent = "before";
    applyStatic(fakeRoot([unknown]));
    expect(unknown.textContent).toBe("before");
    expect(error).toHaveBeenCalledWith(expect.stringContaining("no.such.key"));
  });
});
