import { afterEach, describe, expect, it } from "vitest";

import { setLocale } from "./i18n";
import { formatEngineMessage, localizeParam, parseEngineMessage, parseEngineMessages, type Translate } from "./messages";

afterEach(() => setLocale("zh-TW"));

/** A stand-in dictionary with one engine code, so the mechanism is tested apart from the wording. */
const fakeTranslate: Translate = (key, params) =>
  key === "engine.gpu_runtime_missing" ? `GPU ${String(params.name)} needs the runtime` : null;

describe("formatEngineMessage", () => {
  it("shows plain strings from older engines as-is", () => {
    expect(formatEngineMessage("顯示卡缺少 cuBLAS", fakeTranslate)).toBe("顯示卡缺少 cuBLAS");
  });

  it("translates a known code with its params", () => {
    expect(
      formatEngineMessage(
        { code: "gpu_runtime_missing", params: { name: "RTX 5070" }, message: "fallback" },
        fakeTranslate,
      ),
    ).toBe("GPU RTX 5070 needs the runtime");
  });

  it("falls back to the engine's English message for an unknown code", () => {
    expect(formatEngineMessage({ code: "brand_new", params: {}, message: "Something new." }, fakeTranslate)).toBe(
      "Something new.",
    );
  });

  it("uses the real dictionary by default and falls back to message for unknown codes", () => {
    setLocale("en");
    expect(formatEngineMessage({ code: "gpu_runtime_missing", params: {}, message: "English text" })).toContain(
      "GPU runtime",
    );
    expect(formatEngineMessage({ code: "made_up_code", params: {}, message: "English text" })).toBe("English text");
  });
});

describe("parseEngineMessage", () => {
  it("accepts strings and coded objects, keeping only scalar params", () => {
    expect(parseEngineMessage("plain")).toBe("plain");
    expect(
      parseEngineMessage({ code: "low_ram", params: { gb: 8, name: "x", nested: { a: 1 }, bad: Number.NaN }, message: "Low RAM" }),
    ).toEqual({ code: "low_ram", params: { gb: 8, name: "x" }, message: "Low RAM" });
    expect(parseEngineMessage({ code: "no_params", message: "ok" })).toEqual({ code: "no_params", params: {}, message: "ok" });
  });

  it("rejects malformed messages", () => {
    expect(parseEngineMessage(3)).toBeNull();
    expect(parseEngineMessage(null)).toBeNull();
    expect(parseEngineMessage({ code: "x" })).toBeNull();
    expect(parseEngineMessage({ code: "Bad Code!", message: "m" })).toBeNull();
    expect(parseEngineMessage({ message: "m" })).toBeNull();
    expect(parseEngineMessage("x".repeat(2001))).toBeNull();
  });

  it("drops bad list items instead of failing the whole payload", () => {
    expect(parseEngineMessages(["a", 1, { code: "c", message: "m" }, { nope: true }])).toEqual([
      "a",
      { code: "c", params: {}, message: "m" },
    ]);
    expect(parseEngineMessages("not a list")).toEqual([]);
  });
});

describe("engine message parameters", () => {
  it("names languages in the interface language and treats auto as automatic detection", () => {
    setLocale("en");
    expect(localizeParam("language", "ja")).toBe("Japanese");
    expect(localizeParam("target_language", "zh-TW")).toBe("Traditional Chinese");
    expect(localizeParam("source_language", "auto")).toBe("Detect automatically");
    setLocale("zh-TW");
    expect(localizeParam("source_language", "ko")).toBe("韓文");
  });

  it("labels ASR paths and translation fields, names regions, and formats GB with one decimal", () => {
    setLocale("en");
    expect(localizeParam("path", "fast_asr")).toBe("Live subtitle recognition");
    expect(localizeParam("path", "final_asr")).toBe("Final subtitle recognition");
    expect(localizeParam("path", "something_new")).toBe("something_new");
    expect(localizeParam("field", "translation_final")).toBe("The final translation model");
    expect(localizeParam("regions", "EU,UK, KR")).toBe("European Union, United Kingdom, South Korea");
    expect(localizeParam("ram_gb", 7)).toBe("7.0");
    expect(localizeParam("vram_mb", 8188)).toBe(8188);
    expect(localizeParam("model", "qwen3.5:9b")).toBe("qwen3.5:9b");
    expect(localizeParam("compute_type", "float16")).toBe("float16");
    expect(localizeParam("compute_type", "float16 / int8_float16")).toBe("live float16, final int8_float16");
  });

  it("fills the real dictionary with localized parameters", () => {
    setLocale("zh-TW");
    expect(
      formatEngineMessage({
        code: "asr_language_quality_low",
        params: { model: "small", language: "zh-TW" },
        message: "small recognizes zh-TW poorly",
      }),
    ).toBe("small 辨識繁體中文的品質較差（公開測試的錯誤率超過 20%）");
    setLocale("en");
    expect(
      formatEngineMessage({
        code: "asr_model_not_downloaded",
        params: { path: "fast_asr", model: "medium" },
        message: "fast_asr: ...",
      }),
    ).toBe("Live subtitle recognition: speech recognition model medium is not downloaded yet; it is downloaded before starting");
    expect(
      formatEngineMessage({ code: "translation_budget_vram", params: { budget_gb: 7 }, message: "About 7.0 GB" }),
    ).toContain("About 7.0 GB");
  });
});
