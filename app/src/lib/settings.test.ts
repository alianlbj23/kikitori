import { describe, expect, it } from "vitest";

import engine from "./testdata/engine-fake-nvidia.json";
import {
  DEFAULT_OVERLAY_STYLE,
  DEFAULT_SETTINGS,
  combinationFieldForEnginePath,
  combinationFormFromSettings,
  combinationPatch,
  devicePatch,
  formFieldForEnginePath,
  formFromSettings,
  overlayStyleFromSubtitle,
  parseAppliedRecommendation,
  parseSettingsResult,
  settingsPatchFromForm,
  uiLocalePatch,
  validateCombinationForm,
  validateSettingsForm,
} from "./settings";
import { ValidationError } from "./validate";

const validForm = () => formFromSettings(DEFAULT_SETTINGS);
const validCombo = () => combinationFormFromSettings(DEFAULT_SETTINGS);

describe("validateSettingsForm", () => {
  it("accepts the defaults", () => {
    expect(validateSettingsForm(validForm())).toEqual({});
  });

  it.each([
    ["base_url", "ftp://x"],
    ["base_url", "not a url"],
    ["base_url", ""],
    ["api_key_env", "1ABC"],
    ["api_key_env", "MY-KEY"],
    ["api_key_env", "sk-actual-secret value"],
    ["font_size", "13"],
    ["font_size", "abc"],
    ["background_opacity_percent", "101"],
    ["background_opacity_percent", "-1"],
    ["position", "left"],
    ["max_lines", "4"],
  ] as const)("flags %s = %j", (field, value) => {
    const errors = validateSettingsForm({ ...validForm(), [field]: value });
    expect(errors[field]).toBeTruthy();
  });
});

describe("settings patch", () => {
  it("builds a partial settings object with the licence region and overlay style (languages have their own section)", () => {
    const patch = settingsPatchFromForm({
      ...validForm(),
      base_url: "  https://api.example.com/v1 ",
      allow_restricted: true,
      background_opacity_percent: "30",
      position: "top",
    });
    expect(patch).toEqual({
      translation: {
        base_url: "https://api.example.com/v1",
        api_key_env: "KIKITORI_TRANSLATE_API_KEY",
        allow_restricted: true,
      },
      subtitle: { font_size: 32, background_opacity: 0.3, position: "top", max_lines: 2 },
    });
  });

  it("never sends translation models from the settings page", () => {
    const patch = settingsPatchFromForm(validForm()) as { translation: Record<string, unknown> };
    expect(patch.translation).not.toHaveProperty("fast_model");
    expect(patch.translation).not.toHaveProperty("final_model");
  });

  it("defaults allow_restricted to false", () => {
    expect(DEFAULT_SETTINGS.translation.allow_restricted).toBe(false);
    expect(validForm().allow_restricted).toBe(false);
  });

  it("remembers the device per mode", () => {
    expect(devicePatch("interpret", null)).toEqual({ mode: "interpret", interpret: { device_id: null } });
  });
});

describe("parseSettingsResult", () => {
  it("reads the engine result and falls back on bad fields with warnings", () => {
    const { settings, warnings, path } = parseSettingsResult({
      settings: {
        mode: "interpret",
        partner_language: "<b>",
        interpret: { device_id: "mic-1" },
        asr: { fast_model: "tiny", final_model: "small", device: "cuda" },
        translation: { base_url: "http://h/v1", fast_model: "a", final_model: "b", api_key_env: "K" },
        subtitle: { font_size: 500, background_opacity: "x", position: "top" },
      },
      path: "C:/x/settings.json",
    });
    expect(path).toBe("C:/x/settings.json");
    expect(settings.mode).toBe("interpret");
    expect(settings.partner_language).toBe("ja");
    expect(warnings).toContain("partner_language");
    expect(settings.interpret_device_id).toBe("mic-1");
    expect(settings.subtitle).toEqual({
      font_size: 96,
      background_opacity: DEFAULT_OVERLAY_STYLE.background_opacity,
      position: "top",
      max_lines: DEFAULT_OVERLAY_STYLE.max_lines,
    });
  });

  it("reads schema 2 fields", () => {
    const { settings, warnings } = parseSettingsResult(engine["settings.apply_recommendation"]);
    expect(warnings).toEqual([]);
    expect(settings.asr).toEqual({
      fast_model: "base",
      final_model: "small",
      device: "cpu",
      fast_compute_type: "int8",
      final_compute_type: "int8",
      fast_cpu_threads: 4,
      final_cpu_threads: 4,
      cuda_library_dirs: [],
    });
    expect(settings.translation.allow_restricted).toBe(false);
  });

  it("warns about malformed schema 2 fields instead of failing", () => {
    const { settings, warnings } = parseSettingsResult({
      settings: {
        asr: { fast_compute_type: "int4", fast_cpu_threads: 0, final_cpu_threads: 1.5, cuda_library_dirs: "C:/x" },
        translation: { allow_restricted: "yes" },
      },
    });
    expect(settings.asr.fast_compute_type).toBeNull();
    expect(settings.asr.fast_cpu_threads).toBeNull();
    expect(settings.asr.cuda_library_dirs).toEqual([]);
    expect(settings.translation.allow_restricted).toBe(false);
    expect(warnings).toEqual(
      expect.arrayContaining([
        "asr.fast_compute_type",
        "asr.fast_cpu_threads",
        "asr.final_cpu_threads",
        "asr.cuda_library_dirs",
        "translation.allow_restricted",
      ]),
    );
  });

  it("reports whether the settings file is new (first run)", () => {
    expect(parseSettingsResult(engine["settings.get"]).isNew).toBe(true);
    expect(parseSettingsResult(engine["settings.apply_recommendation"]).isNew).toBe(false);
    // An older engine does not send is_new: never treat that as a first run.
    expect(parseSettingsResult({ settings: {} }).isNew).toBe(false);
    expect(parseSettingsResult({ settings: {}, is_new: "yes" }).isNew).toBe(false);
  });

  it("reads dismissed recommendations from the opaque ui object", () => {
    expect(parseSettingsResult(engine["settings.get"]).settings.ui).toEqual({
      dismissed_recommendations: [],
      locale: "system",
    });
    const ok = parseSettingsResult({ settings: { ui: { dismissed_recommendations: ["0123456789abcdef"], other: 1 } } });
    expect(ok.settings.ui.dismissed_recommendations).toEqual(["0123456789abcdef"]);
    expect(ok.warnings).toEqual([]);
    const bad = parseSettingsResult({ settings: { ui: { dismissed_recommendations: ["x", 3] } } });
    expect(bad.settings.ui.dismissed_recommendations).toEqual([]);
    expect(bad.warnings).toContain("ui.dismissed_recommendations");
  });

  it("reads the interface language from ui.locale, keeping unknown future locales quiet", () => {
    const read = (locale: unknown) => parseSettingsResult({ settings: { ui: { locale } } });
    expect(read(undefined).settings.ui.locale).toBe("system");
    expect(read("system").settings.ui.locale).toBe("system");
    expect(read("pt-BR").settings.ui.locale).toBe("pt-BR");
    expect(read("en").warnings).toEqual([]);
    // A locale added by a later version: follow the system, without a "bad field" notice.
    const future = read("sw");
    expect(future.settings.ui.locale).toBe("system");
    expect(future.warnings).toEqual([]);
    const bad = read(42);
    expect(bad.settings.ui.locale).toBe("system");
    expect(bad.warnings).toContain("ui.locale");
  });

  it("stores the interface language under ui without touching other ui keys", () => {
    // The engine deep-merges settings.set, so sending only ui.locale keeps dismissed_recommendations.
    expect(uiLocalePatch("ja")).toEqual({ ui: { locale: "ja" } });
    expect(uiLocalePatch("system")).toEqual({ ui: { locale: "system" } });
  });

  it("uses defaults for an empty subtitle object", () => {
    expect(overlayStyleFromSubtitle({})).toEqual(DEFAULT_OVERLAY_STYLE);
    expect(overlayStyleFromSubtitle(null)).toEqual(DEFAULT_OVERLAY_STYLE);
  });
});

describe("parseAppliedRecommendation", () => {
  it("reads what the engine wrote, the new settings and the recommendation", () => {
    const r = parseAppliedRecommendation(engine["settings.apply_recommendation"]);
    expect(r.path).toBe("C:/fake/settings.json");
    expect(r.settings.asr.device).toBe("cpu");
    expect(r.applied).toMatchObject({ asr: { device: "cpu", fast_cpu_threads: 4 } });
    expect(r.recommendation.tier).toBe("cpu");
  });

  it("accepts applied = null (only_if_new and settings were already saved)", () => {
    const r = parseAppliedRecommendation({ ...engine["settings.apply_recommendation"], applied: null });
    expect(r.applied).toBeNull();
  });

  it("rejects a result without applied or recommendation", () => {
    const { applied: _a, ...noApplied } = engine["settings.apply_recommendation"];
    expect(() => parseAppliedRecommendation(noApplied)).toThrow(ValidationError);
    const { recommendation: _r, ...noRec } = engine["settings.apply_recommendation"];
    expect(() => parseAppliedRecommendation(noRec)).toThrow(ValidationError);
  });
});

describe("validateCombinationForm", () => {
  it("accepts the defaults (automatic precision and threads)", () => {
    expect(validateCombinationForm(validCombo())).toEqual({});
  });

  it.each([
    ["fast_model", ""],
    ["final_model", "../x"],
    ["device", "tpu"],
    ["translation_fast", "  "],
    ["translation_final", "m".repeat(201)],
    ["fast_compute_type", "int4"],
    ["fast_cpu_threads", "0"],
    ["final_cpu_threads", "257"],
    ["final_cpu_threads", "2.5"],
    ["cuda_library_dirs", "relative\\bin"],
    ["cuda_library_dirs", Array.from({ length: 17 }, (_, i) => `C:\\cuda${i}`).join("\n")],
  ] as const)("flags %s = %j", (field, value) => {
    const errors = validateCombinationForm({ ...validCombo(), [field]: value });
    expect(errors[field]).toBeTruthy();
  });

  it("accepts Windows, UNC and POSIX absolute library folders", () => {
    const dirs = "C:\\CUDA\\bin\n\n  \\\\server\\cuda  \n/usr/local/cuda/lib64";
    expect(validateCombinationForm({ ...validCombo(), cuda_library_dirs: dirs })).toEqual({});
  });
});

describe("combinationPatch", () => {
  it("sends every field, with automatic values as explicit null", () => {
    expect(combinationPatch(validCombo())).toEqual({
      asr: {
        fast_model: "base",
        final_model: "small",
        device: "auto",
        fast_compute_type: null,
        final_compute_type: null,
        fast_cpu_threads: null,
        final_cpu_threads: null,
        cuda_library_dirs: [],
      },
      translation: { fast_model: "qwen3.5:4b", final_model: "qwen3.5:4b" },
    });
  });

  it("converts advanced inputs", () => {
    const patch = combinationPatch({
      ...validCombo(),
      device: "cuda",
      final_compute_type: "int8_float16",
      fast_cpu_threads: " 6 ",
      cuda_library_dirs: "C:\\a\r\n\r\nD:\\b ",
      translation_final: " qwen3.5:9b ",
    }) as { asr: Record<string, unknown>; translation: Record<string, unknown> };
    expect(patch.asr).toMatchObject({
      device: "cuda",
      fast_compute_type: null,
      final_compute_type: "int8_float16",
      fast_cpu_threads: 6,
      cuda_library_dirs: ["C:\\a", "D:\\b"],
    });
    expect(patch.translation.final_model).toBe("qwen3.5:9b");
  });

  it("round-trips through the form", () => {
    const settings = parseSettingsResult(engine["settings.apply_recommendation"]).settings;
    const patch = combinationPatch(combinationFormFromSettings(settings)) as { asr: Record<string, unknown> };
    expect(patch.asr).toEqual({ ...settings.asr });
  });
});

describe("engine field paths", () => {
  it("maps settings page paths", () => {
    expect(formFieldForEnginePath("translation.base_url: must be http or https")).toBe("base_url");
    expect(formFieldForEnginePath("translation.allow_restricted: expected a boolean")).toBe("allow_restricted");
    expect(formFieldForEnginePath("subtitle.font_size: too big")).toBe("font_size");
    expect(formFieldForEnginePath("pipeline.context_lines: bad")).toBeNull();
  });

  it("maps combination paths, including list items", () => {
    expect(combinationFieldForEnginePath("asr.fast_cpu_threads: expected an integer in [1, 256], got 0")).toBe(
      "fast_cpu_threads",
    );
    expect(combinationFieldForEnginePath("asr.cuda_library_dirs[2]: expected an absolute path")).toBe(
      "cuda_library_dirs",
    );
    expect(combinationFieldForEnginePath("translation.final_model: empty")).toBe("translation_final");
    expect(combinationFieldForEnginePath("translation.base_url: bad")).toBeNull();
  });
});
