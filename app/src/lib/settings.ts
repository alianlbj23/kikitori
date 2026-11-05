/**
 * Engine settings as the UI sees them: parsing, form validation, partial updates.
 *
 * Role: the engine owns `settings.json` (docs/specs/pipeline-engine.md "config")
 * and validates authoritatively; this module reads the parts the UI edits, checks
 * form input before sending it (so mistakes are pointed at the right field
 * immediately), and builds the partial object `settings.set` deep-merges.
 * Overlay appearance lives in the engine's opaque `subtitle` object.
 */

import type { Mode, OverlayStyle, PartnerLanguage, Recommendation, Settings } from "./types";
import { FONT_SIZE_RANGE, MAX_LINES_RANGE } from "./validate";

export const DEFAULT_OVERLAY_STYLE: OverlayStyle = {
  font_size: 32,
  background_opacity: 0.45,
  position: "bottom",
  max_lines: 2,
};

export const DEFAULT_SETTINGS: Settings = {
  mode: "media",
  target_language: "zh-TW",
  partner_language: "ja",
  media_device_id: null,
  interpret_device_id: null,
  asr: { fast_model: "base", final_model: "large-v3-turbo", device: "auto" },
  asr_has_compute_types: false,
  translation: {
    base_url: "http://localhost:11434/v1",
    fast_model: "qwen3.5:4b",
    final_model: "qwen3.5:4b",
    api_key_env: "KIKITORI_TRANSLATE_API_KEY",
  },
  subtitle: DEFAULT_OVERLAY_STYLE,
};

type Obj = Record<string, unknown>;

function asObj(v: unknown): Obj {
  return typeof v === "object" && v !== null && !Array.isArray(v) ? (v as Obj) : {};
}

function pickStr(o: Obj, key: string, fallback: string, warn: (m: string) => void, path: string) {
  const v = o[key];
  if (typeof v === "string" && v.length <= 2000) return v;
  if (v !== undefined) warn(`${path}${key}`);
  return fallback;
}

function pickNullableStr(o: Obj, key: string, warn: (m: string) => void, path: string) {
  const v = o[key];
  if (v === null || v === undefined) return null;
  if (typeof v === "string" && v.length > 0 && v.length <= 512) return v;
  warn(`${path}${key}`);
  return null;
}

function pickEnum<T extends string>(
  o: Obj,
  key: string,
  allowed: readonly T[],
  fallback: T,
  warn: (m: string) => void,
  path: string,
): T {
  const v = o[key];
  if (typeof v === "string" && (allowed as readonly string[]).includes(v)) return v as T;
  if (v !== undefined) warn(`${path}${key}`);
  return fallback;
}

function clampInt(v: unknown, [lo, hi]: readonly [number, number], fallback: number): number {
  if (typeof v !== "number" || !Number.isFinite(v)) return fallback;
  return Math.min(hi, Math.max(lo, Math.round(v)));
}

/** Overlay style from `settings.subtitle`, falling back field by field. */
export function overlayStyleFromSubtitle(subtitle: unknown): OverlayStyle {
  const o = asObj(subtitle);
  const opacity = o.background_opacity;
  return {
    font_size: clampInt(o.font_size, FONT_SIZE_RANGE, DEFAULT_OVERLAY_STYLE.font_size),
    background_opacity:
      typeof opacity === "number" && Number.isFinite(opacity)
        ? Math.min(1, Math.max(0, opacity))
        : DEFAULT_OVERLAY_STYLE.background_opacity,
    position: o.position === "top" || o.position === "bottom" ? o.position : "bottom",
    max_lines: clampInt(o.max_lines, MAX_LINES_RANGE, DEFAULT_OVERLAY_STYLE.max_lines),
  };
}

/**
 * Reads the `settings.get` / `settings.set` result. Missing or malformed fields
 * fall back to defaults and are reported through `warnings` instead of failing,
 * so one odd value does not lock the user out of the settings page.
 */
export function parseSettingsResult(result: unknown): { settings: Settings; warnings: string[] } {
  const warnings: string[] = [];
  const warn = (field: string) => warnings.push(field);
  const root = asObj(asObj(result).settings);
  const media = asObj(root.media);
  const interpret = asObj(root.interpret);
  const asr = asObj(root.asr);
  const tr = asObj(root.translation);
  const d = DEFAULT_SETTINGS;
  return {
    settings: {
      mode: pickEnum(root, "mode", ["media", "interpret"] as const, d.mode, warn, ""),
      target_language: pickStr(root, "target_language", d.target_language, warn, ""),
      partner_language: pickEnum(root, "partner_language", ["ja", "en"] as const, d.partner_language, warn, ""),
      media_device_id: pickNullableStr(media, "device_id", warn, "media."),
      interpret_device_id: pickNullableStr(interpret, "device_id", warn, "interpret."),
      asr: {
        fast_model: pickStr(asr, "fast_model", d.asr.fast_model, warn, "asr."),
        final_model: pickStr(asr, "final_model", d.asr.final_model, warn, "asr."),
        device: pickEnum(asr, "device", ["auto", "cpu", "cuda"] as const, d.asr.device, warn, "asr."),
      },
      asr_has_compute_types: "fast_compute_type" in asr,
      translation: {
        base_url: pickStr(tr, "base_url", d.translation.base_url, warn, "translation."),
        fast_model: pickStr(tr, "fast_model", d.translation.fast_model, warn, "translation."),
        final_model: pickStr(tr, "final_model", d.translation.final_model, warn, "translation."),
        api_key_env: pickStr(tr, "api_key_env", d.translation.api_key_env, warn, "translation."),
      },
      subtitle: overlayStyleFromSubtitle(root.subtitle),
    },
    warnings,
  };
}

/** Raw values of the settings form (strings straight from inputs). */
export interface SettingsForm {
  base_url: string;
  api_key_env: string;
  fast_model: string;
  final_model: string;
  partner_language: string;
  font_size: string;
  background_opacity_percent: string;
  position: string;
  max_lines: string;
}

export type FormErrors = Partial<Record<keyof SettingsForm, string>>;

const ENV_NAME = /^[A-Za-z_][A-Za-z0-9_]*$/;
const MODEL_NAME_MAX = 200;

function parseIntField(raw: string): number | null {
  if (!/^\s*\d+\s*$/.test(raw)) return null;
  return Number.parseInt(raw, 10);
}

/** Checks the form; returns per-field messages (empty object when valid). */
export function validateSettingsForm(form: SettingsForm): FormErrors {
  const errors: FormErrors = {};
  const url = form.base_url.trim();
  try {
    const parsed = new URL(url);
    if (parsed.protocol !== "http:" && parsed.protocol !== "https:") {
      errors.base_url = "網址要以 http:// 或 https:// 開頭。";
    }
  } catch {
    errors.base_url = url === "" ? "請填寫翻譯服務的網址。" : "這不是有效的網址。";
  }
  const env = form.api_key_env.trim();
  if (!ENV_NAME.test(env)) {
    errors.api_key_env = "只能用英文字母、數字和底線，且不能以數字開頭。";
  } else if (env.length > 100) {
    errors.api_key_env = "名稱太長（最多 100 個字元）。";
  }
  for (const key of ["fast_model", "final_model"] as const) {
    const v = form[key].trim();
    if (v === "") errors[key] = "請填寫模型名稱。";
    else if (v.length > MODEL_NAME_MAX) errors[key] = `模型名稱最多 ${MODEL_NAME_MAX} 個字元。`;
  }
  if (form.partner_language !== "ja" && form.partner_language !== "en") {
    errors.partner_language = "請選擇日語或英語。";
  }
  const size = parseIntField(form.font_size);
  if (size === null || size < FONT_SIZE_RANGE[0] || size > FONT_SIZE_RANGE[1]) {
    errors.font_size = `字級要在 ${FONT_SIZE_RANGE[0]} 到 ${FONT_SIZE_RANGE[1]} 之間。`;
  }
  const opacity = parseIntField(form.background_opacity_percent);
  if (opacity === null || opacity > 100) {
    errors.background_opacity_percent = "背景不透明度要在 0% 到 100% 之間。";
  }
  if (form.position !== "top" && form.position !== "bottom") {
    errors.position = "請選擇上方或下方。";
  }
  const lines = parseIntField(form.max_lines);
  if (lines === null || lines < MAX_LINES_RANGE[0] || lines > MAX_LINES_RANGE[1]) {
    errors.max_lines = `句數要在 ${MAX_LINES_RANGE[0]} 到 ${MAX_LINES_RANGE[1]} 之間。`;
  }
  return errors;
}

export function overlayStyleFromForm(form: SettingsForm): OverlayStyle {
  return {
    font_size: Number.parseInt(form.font_size, 10),
    background_opacity: Number.parseInt(form.background_opacity_percent, 10) / 100,
    position: form.position === "top" ? "top" : "bottom",
    max_lines: Number.parseInt(form.max_lines, 10),
  };
}

/** Partial settings for `settings.set`; call only after validation passed. */
export function settingsPatchFromForm(form: SettingsForm): Record<string, unknown> {
  return {
    partner_language: form.partner_language as PartnerLanguage,
    translation: {
      base_url: form.base_url.trim(),
      fast_model: form.fast_model.trim(),
      final_model: form.final_model.trim(),
      api_key_env: form.api_key_env.trim(),
    },
    subtitle: overlayStyleFromForm(form),
  };
}

export function formFromSettings(s: Settings): SettingsForm {
  return {
    base_url: s.translation.base_url,
    api_key_env: s.translation.api_key_env,
    fast_model: s.translation.fast_model,
    final_model: s.translation.final_model,
    partner_language: s.partner_language,
    font_size: String(s.subtitle.font_size),
    background_opacity_percent: String(Math.round(s.subtitle.background_opacity * 100)),
    position: s.subtitle.position,
    max_lines: String(s.subtitle.max_lines),
  };
}

/**
 * `settings.set` patch applying a hardware recommendation; mirrors the engine's
 * `recommendation_patch` (hardware.md): one device when both paths agree,
 * automatic threads for 0, translation kept when not recommended. Compute types
 * and threads are only sent to engines whose settings have those fields.
 */
export function recommendationPatch(rec: Recommendation, withComputeTypes: boolean): Record<string, unknown> {
  const patch: Record<string, unknown> = {};
  const fast = rec.fast_asr;
  const final = rec.final_asr;
  if (fast && final) {
    const device = fast.device === final.device ? fast.device : "auto";
    const asr: Record<string, unknown> = {
      fast_model: fast.model,
      final_model: final.model,
      device: ["auto", "cpu", "cuda"].includes(device) ? device : "auto",
    };
    if (withComputeTypes) {
      asr.fast_compute_type = fast.compute_type;
      asr.final_compute_type = final.compute_type;
      asr.fast_cpu_threads = fast.cpu_threads || null;
      asr.final_cpu_threads = final.cpu_threads || null;
    }
    patch.asr = asr;
  }
  const translation: Record<string, string> = {};
  if (rec.translation_fast) translation.fast_model = rec.translation_fast;
  if (rec.translation_final) translation.final_model = rec.translation_final;
  if (Object.keys(translation).length > 0) patch.translation = translation;
  return patch;
}

/** Patch that remembers the mode and its device (`null` = system default). */
export function devicePatch(mode: Mode, deviceId: string | null): Record<string, unknown> {
  return { mode, [mode]: { device_id: deviceId } };
}

/**
 * The engine reports settings errors as `"<field.path>: <problem>"`; map the
 * path back to a form field so the message appears next to the right input.
 */
export function formFieldForEnginePath(message: string): keyof SettingsForm | null {
  const path = message.split(":", 1)[0]?.trim() ?? "";
  const map: Record<string, keyof SettingsForm> = {
    "translation.base_url": "base_url",
    "translation.api_key_env": "api_key_env",
    "translation.fast_model": "fast_model",
    "translation.final_model": "final_model",
    partner_language: "partner_language",
    "subtitle.font_size": "font_size",
    "subtitle.background_opacity": "background_opacity_percent",
    "subtitle.position": "position",
    "subtitle.max_lines": "max_lines",
  };
  return map[path] ?? null;
}
