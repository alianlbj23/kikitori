import { afterEach, describe, expect, it } from "vitest";

import { setLocale } from "./i18n";
import {
  AUTO_LANGUAGE,
  filterLanguageOptions,
  fold,
  languageFormFromSettings,
  languageOptions,
  languagesSave,
  parseLanguageList,
  parseLanguagePreview,
  partnerWithoutConflict,
  previewMatches,
  previewNotes,
  previewRequest,
  previewRows,
  previewSummary,
  validateLanguageForm,
  type LanguageForm,
} from "./languages";
import { DEFAULT_SETTINGS } from "./settings";
import engine from "./testdata/engine-fake-nvidia.json";
import type { LanguagePreview } from "./types";
import { ValidationError } from "./validate";

afterEach(() => setLocale("zh-TW"));

const list = () => parseLanguageList(engine["languages.list"]);
const mediaPreview = () => parseLanguagePreview(engine["settings.preview_languages"]);
const interpretPreview = () => parseLanguagePreview(engine["settings.preview_languages interpret"]);

const media = (over: Partial<LanguageForm> = {}): LanguageForm => ({
  mode: "media",
  source: "ko",
  target: "es",
  partner: "ja",
  applyRecommended: true,
  ...over,
});

const interpret = (over: Partial<LanguageForm> = {}): LanguageForm => ({
  mode: "interpret",
  source: "ja",
  target: "zh-TW",
  partner: "en",
  applyRecommended: true,
  ...over,
});

/** A copy of the captured preview with some changes, for states the fake machine does not reach. */
function previewWith(over: Partial<LanguagePreview>): LanguagePreview {
  return { ...mediaPreview(), ...over };
}

describe("languages.list (real engine capture)", () => {
  it("reads the 20 languages in the engine's order", () => {
    const l = list();
    expect(l).toHaveLength(20);
    expect(l[0]).toEqual({
      code: "ja",
      english_name: "Japanese",
      native_name: "日本語",
      whisper_code: "ja",
      rtl: false,
      no_space: true,
    });
    expect(l.find((x) => x.code === "zh-CN")?.whisper_code).toBe("zh");
    expect(l.find((x) => x.code === "ar")?.rtl).toBe(true);
  });

  it("drops malformed entries and rejects an answer without languages", () => {
    const l = parseLanguageList({
      languages: [{ code: "ja", native_name: "日本語" }, { code: "<b>" }, "x", { code: "ja" }, { code: "ko", whisper_code: 5 }],
    });
    expect(l.map((x) => x.code)).toEqual(["ja", "ko"]);
    expect(l[1]!.whisper_code).toBe("ko");
    expect(l[1]!.english_name).toBe("ko");
    expect(() => parseLanguageList({ languages: [] })).toThrow(ValidationError);
    expect(() => parseLanguageList({})).toThrow(ValidationError);
  });
});

describe("menu options", () => {
  it("shows the native name with the interface-language name, once when they agree", () => {
    setLocale("en");
    const options = languageOptions(list());
    expect(options.find((o) => o.value === "ja")!.label).toBe("日本語 (Japanese)");
    expect(options.find((o) => o.value === "en")!.label).toBe("English");
    setLocale("zh-TW");
    expect(languageOptions(list()).find((o) => o.value === "zh-TW")!.label).toBe("繁體中文");
    expect(languageOptions(list()).find((o) => o.value === "ar")!.rtl).toBe(true);
  });

  it("starts the listening menu with automatic detection", () => {
    setLocale("en");
    const options = languageOptions(list(), true);
    expect(options[0]).toMatchObject({ value: AUTO_LANGUAGE, label: "Detect automatically" });
    expect(languageOptions(list())[0]!.value).toBe("ja");
  });

  it("filters by code, native, local or English name, ignoring case and accents, keeping the selection", () => {
    setLocale("en");
    const options = languageOptions(list(), true);
    const values = (q: string, selected = "ja") => filterLanguageOptions(options, q, selected).map((o) => o.value);
    expect(values("espanol")).toEqual(["ja", "es"]);
    expect(values("SPAN")).toEqual(["ja", "es"]);
    expect(values("한국")).toEqual(["ja", "ko"]);
    expect(values("zh-")).toEqual(["ja", "zh-TW", "zh-CN"]);
    expect(values("auto", "es")).toEqual([AUTO_LANGUAGE, "es"]);
    expect(values("   ")).toHaveLength(options.length);
    expect(values("no such language", "es")).toEqual(["es"]);
    expect(fold("Tiếng Việt")).toBe("tieng viet");
  });
});

describe("the language form", () => {
  it("starts from the saved settings in the current mode", () => {
    expect(languageFormFromSettings(DEFAULT_SETTINGS, "interpret")).toEqual({
      mode: "interpret",
      source: "ja",
      target: "zh-TW",
      partner: "ja",
      applyRecommended: true,
    });
  });

  it("accepts any listed combination, auto as the listening language, and same languages in media mode", () => {
    expect(validateLanguageForm(media(), list())).toEqual({});
    expect(validateLanguageForm(media({ source: AUTO_LANGUAGE }), list())).toEqual({});
    expect(validateLanguageForm(media({ source: "zh-CN", target: "zh-TW" }), list())).toEqual({});
    expect(validateLanguageForm(interpret(), list())).toEqual({});
  });

  it("flags unknown codes and interpret languages that cannot be told apart", () => {
    expect(Object.keys(validateLanguageForm(media({ source: "xx" }), list()))).toEqual(["source"]);
    expect(Object.keys(validateLanguageForm(media({ target: AUTO_LANGUAGE }), list()))).toEqual(["target"]);
    expect(Object.keys(validateLanguageForm(interpret({ partner: "zh-CN" }), list()))).toEqual(["partner"]);
    expect(Object.keys(validateLanguageForm(interpret({ partner: "zh-TW" }), list()))).toEqual(["partner"]);
    expect(Object.keys(validateLanguageForm(interpret({ target: "xx" }), list()))).toEqual(["target"]);
    // Interpret mode ignores the listening language.
    expect(validateLanguageForm(interpret({ source: "xx" }), list())).toEqual({});
  });

  it("asks the engine only about the fields of the current mode", () => {
    expect(previewRequest(media())).toEqual({ mode: "media", source_language: "ko", target_language: "es" });
    expect(previewRequest(interpret())).toEqual({ mode: "interpret", target_language: "zh-TW", partner_language: "en" });
  });

  it("matches a preview to the combination it was computed for", () => {
    expect(previewMatches(mediaPreview(), media())).toBe(true);
    expect(previewMatches(mediaPreview(), media({ target: "fr" }))).toBe(false);
    expect(previewMatches(mediaPreview(), interpret())).toBe(false);
    expect(previewMatches(interpretPreview(), interpret())).toBe(true);
    expect(previewMatches(null, media())).toBe(false);
  });

  it("moves a clashing interpret partner when the subtitle language changes", () => {
    expect(partnerWithoutConflict("ja", "ja", list())).toBe("en");
    expect(partnerWithoutConflict("zh-CN", "zh-TW", list())).toBe("ja");
    expect(partnerWithoutConflict("es", "ja", list())).toBe("ja");
  });
});

describe("saving languages", () => {
  it("adds the recommended models when ticked and the preview is for this combination", () => {
    const preview = mediaPreview();
    const save = languagesSave(media(), list(), preview);
    expect(save.withModels).toBe(true);
    expect(save.patch).toEqual({
      ...preview.patch,
      source_language: "ko",
      target_language: "es",
      partner_language: "ja",
    });
  });

  it("saves only the languages when unticked, or when the preview is stale or missing", () => {
    const only = { source_language: "ko", target_language: "es", partner_language: "ja" };
    expect(languagesSave(media({ applyRecommended: false }), list(), mediaPreview())).toEqual({ patch: only, withModels: false });
    expect(languagesSave(media(), list(), null)).toEqual({ patch: only, withModels: false });
    expect(languagesSave(media({ target: "fr" }), list(), mediaPreview()).withModels).toBe(false);
  });

  it("keeps interpret mode valid later: a media subtitle language moves a clashing partner", () => {
    expect(languagesSave(media({ target: "ja", partner: "ja", applyRecommended: false }), list(), null).patch).toEqual({
      source_language: "ko",
      target_language: "ja",
      partner_language: "en",
    });
    expect(languagesSave(interpret({ applyRecommended: false }), list(), null).patch).toEqual({
      target_language: "zh-TW",
      partner_language: "en",
    });
  });
});

describe("preview display", () => {
  it("lists the recommended models with their install state (real engine capture)", () => {
    setLocale("zh-TW");
    expect(previewRows(mediaPreview())).toEqual([
      { label: "即時字幕的辨識", model: "base", status: "已安裝", tone: "ok" },
      { label: "完整翻譯的辨識", model: "small", status: "已安裝", tone: "ok" },
      { label: "翻譯模型", model: "qwen3.5:4b", status: "已安裝", tone: "ok" },
    ]);
    expect(previewSummary(mediaPreview())).toBe("需要的模型都已安裝。");
  });

  it("says what the first start downloads", () => {
    setLocale("en");
    const p = mediaPreview();
    const missing = previewWith({
      asr: p.asr.map((m) => (m.model === "small" ? { ...m, installed: false, download_bytes: 484_000_000 } : m)),
      translation: [{ ...p.translation[0]!, model: "qwen3.5:9b", installed: false, download_bytes: 6_600_000_000 }],
      download_bytes: 7_084_000_000,
    });
    const rows = previewRows(missing);
    expect(rows[1]).toMatchObject({ model: "small", status: "Downloaded on first use, about 484 MB", tone: "download" });
    expect(rows[2]).toMatchObject({ model: "qwen3.5:9b", status: "Downloaded on first use, about 6.6 GB" });
    expect(previewSummary(missing)).toBe("The first start downloads about 7.1 GB.");
    expect(previewSummary(previewWith({ download_bytes: 1_000_000_000, download_size_known: false }))).toContain("unknown size");
  });

  it("explains a combination without a translation model", () => {
    setLocale("en");
    const p = mediaPreview();
    const status = (over: Partial<LanguagePreview>) => previewRows(previewWith(over))[2]!;
    expect(status({ translation_needed: false, translation: [] })).toMatchObject({ model: null, status: "No translation needed" });
    expect(
      status({
        translation_needed: false,
        translation: [],
        messages: [{ code: "translation_disabled", params: {}, message: "off" }],
      }).status,
    ).toContain("Show translation");
    expect(
      status({ recommendation: { ...p.recommendation, translation_status: "unsupported" }, translation: [] }),
    ).toMatchObject({ status: "No translation model supports this language combination", tone: "warn" });
    expect(status({ recommendation: { ...p.recommendation, translation_status: "remote" }, translation: [] }).tone).toBe("muted");
    expect(
      status({ translation: [{ ...p.translation[0]!, installed: null }] }).status,
    ).toContain("Cannot tell whether it is installed");
  });

  it("shows language-related engine notes, not hardware ones (real engine capture)", () => {
    const notes = previewNotes(interpretPreview());
    expect(notes).toEqual([
      { code: "asr_language_quality_low", params: { model: "small", language: "zh-TW" }, message: expect.any(String) },
    ]);
    expect(previewNotes(mediaPreview())).toEqual([]);
  });
});

describe("settings.preview_languages parsing", () => {
  it("reads the captured preview", () => {
    const p = mediaPreview();
    expect(p.languages).toEqual({ mode: "media", source_language: "ko", target_language: "es", partner_language: "ja" });
    expect(p.recommendation.translation_status).toBe("recommended");
    expect(p.asr.map((m) => m.model)).toEqual(["base", "small"]);
    expect(p.translation[0]).toMatchObject({ model: "qwen3.5:4b", roles: ["fast", "final"], size_approximate: true });
    expect(p.translation_needed).toBe(true);
    expect(p.download_bytes).toBe(0);
  });

  it("rejects a preview without a valid language combination", () => {
    const bad = { ...engine["settings.preview_languages"], languages: { mode: "media", target_language: "<b>" } };
    expect(() => parseLanguagePreview(bad)).toThrow(ValidationError);
  });
});
