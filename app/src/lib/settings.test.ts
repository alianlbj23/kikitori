import { describe, expect, it } from "vitest";

import {
  DEFAULT_OVERLAY_STYLE,
  DEFAULT_SETTINGS,
  devicePatch,
  formFieldForEnginePath,
  formFromSettings,
  overlayStyleFromSubtitle,
  parseSettingsResult,
  recommendationPatch,
  settingsPatchFromForm,
  validateSettingsForm,
} from "./settings";

const validForm = () => formFromSettings(DEFAULT_SETTINGS);

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
    ["fast_model", "   "],
    ["final_model", "m".repeat(201)],
    ["partner_language", "ko"],
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
  it("builds a partial settings object with the overlay style under subtitle", () => {
    const patch = settingsPatchFromForm({
      ...validForm(),
      base_url: "  https://api.example.com/v1 ",
      background_opacity_percent: "30",
      position: "top",
    });
    expect(patch).toEqual({
      partner_language: "ja",
      translation: {
        base_url: "https://api.example.com/v1",
        fast_model: "qwen3.5:4b",
        final_model: "qwen3.5:4b",
        api_key_env: "KIKITORI_TRANSLATE_API_KEY",
      },
      subtitle: { font_size: 32, background_opacity: 0.3, position: "top", max_lines: 2 },
    });
  });

  it("remembers the device per mode", () => {
    expect(devicePatch("interpret", null)).toEqual({ mode: "interpret", interpret: { device_id: null } });
  });
});

describe("parseSettingsResult", () => {
  it("reads the engine result and falls back on bad fields with warnings", () => {
    const { settings, warnings } = parseSettingsResult({
      settings: {
        mode: "interpret",
        partner_language: "fr",
        interpret: { device_id: "mic-1" },
        asr: { fast_model: "tiny", final_model: "small", device: "cuda" },
        translation: { base_url: "http://h/v1", fast_model: "a", final_model: "b", api_key_env: "K" },
        subtitle: { font_size: 500, background_opacity: "x", position: "top" },
      },
      path: "C:/x/settings.json",
    });
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

  it("uses defaults for an empty subtitle object", () => {
    expect(overlayStyleFromSubtitle({})).toEqual(DEFAULT_OVERLAY_STYLE);
    expect(overlayStyleFromSubtitle(null)).toEqual(DEFAULT_OVERLAY_STYLE);
  });
});

describe("formFieldForEnginePath", () => {
  it("maps engine field paths to form fields", () => {
    expect(formFieldForEnginePath("translation.base_url: must be http or https")).toBe("base_url");
    expect(formFieldForEnginePath("subtitle.font_size: too big")).toBe("font_size");
    expect(formFieldForEnginePath("pipeline.context_lines: bad")).toBeNull();
  });
});

describe("recommendationPatch", () => {
  const rec = {
    fast_asr: { model: "base", device: "cpu", compute_type: "int8", cpu_threads: 4 },
    final_asr: { model: "small", device: "cpu", compute_type: "int8", cpu_threads: 0 },
    translation_fast: "qwen3.5:9b",
    translation_final: null,
    gpu_runtime_missing: true,
    reasons: [],
    warnings: [],
  };

  it("mirrors the engine's patch with compute types for v2 settings", () => {
    expect(recommendationPatch(rec, true)).toEqual({
      asr: {
        fast_model: "base",
        final_model: "small",
        device: "cpu",
        fast_compute_type: "int8",
        final_compute_type: "int8",
        fast_cpu_threads: 4,
        final_cpu_threads: null,
      },
      translation: { fast_model: "qwen3.5:9b" },
    });
  });

  it("omits compute types for v1 settings and uses auto for mixed devices", () => {
    const mixed = { ...rec, final_asr: { ...rec.final_asr, device: "cuda" }, translation_fast: null };
    expect(recommendationPatch(mixed, false)).toEqual({
      asr: { fast_model: "base", final_model: "small", device: "auto" },
    });
  });

  it("detects v2 settings by the presence of compute types", () => {
    const v2 = parseSettingsResult({ settings: { asr: { fast_compute_type: null } } });
    expect(v2.settings.asr_has_compute_types).toBe(true);
    expect(parseSettingsResult({ settings: { asr: {} } }).settings.asr_has_compute_types).toBe(false);
  });
});

