/**
 * Engine settings as the UI sees them: parsing, form validation, partial updates.
 *
 * Role: the engine owns `settings.json` (docs/specs/pipeline-engine.md "config",
 * schema 2) and validates authoritatively; this module reads the parts the UI
 * edits, checks form input before sending it (so mistakes are pointed at the
 * right field immediately), and builds the partial objects `settings.set` and
 * `models.validate` deep-merge. Two forms exist: the settings page (translation
 * service, licence region, language, subtitle appearance) and the model
 * combination on the models page (ASR and translation models, device,
 * precision, threads). How a hardware recommendation becomes settings is not
 * here on purpose: `settings.apply_recommendation` keeps that rule in the engine.
 * Overlay appearance lives in the engine's opaque `subtitle` object.
 */

import type { AppliedRecommendation, AsrDevice, ComputeType, Mode, OverlayStyle, PartnerLanguage, Settings } from "./types";
import { FONT_SIZE_RANGE, MAX_LINES_RANGE, ValidationError, parseRecommendation } from "./validate";

export const DEFAULT_OVERLAY_STYLE: OverlayStyle = {
  font_size: 32,
  background_opacity: 0.45,
  position: "bottom",
  max_lines: 2,
};

/** CTranslate2 precisions the engine accepts (config `COMPUTE_TYPES`). */
export const COMPUTE_TYPES: readonly ComputeType[] = [
  "int8",
  "int8_float32",
  "int8_float16",
  "int8_bfloat16",
  "int16",
  "float16",
  "bfloat16",
  "float32",
];
export const ASR_DEVICES: readonly AsrDevice[] = ["auto", "cpu", "cuda"];
export const CPU_THREADS_RANGE = [1, 256] as const;
export const MAX_CUDA_DIRS = 16;
const MAX_PATH_CHARS = 1024;
const ASR_MODEL_ID = /^[A-Za-z0-9._-]{1,100}$/;

export const DEFAULT_SETTINGS: Settings = {
  mode: "media",
  target_language: "zh-TW",
  partner_language: "ja",
  media_device_id: null,
  interpret_device_id: null,
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
  translation: {
    base_url: "http://localhost:11434/v1",
    fast_model: "qwen3.5:4b",
    final_model: "qwen3.5:4b",
    api_key_env: "KIKITORI_TRANSLATE_API_KEY",
    allow_restricted: false,
  },
  subtitle: DEFAULT_OVERLAY_STYLE,
};

type Obj = Record<string, unknown>;
type Warn = (field: string) => void;

function asObj(v: unknown): Obj {
  return typeof v === "object" && v !== null && !Array.isArray(v) ? (v as Obj) : {};
}

function pickStr(o: Obj, key: string, fallback: string, warn: Warn, path: string) {
  const v = o[key];
  if (typeof v === "string" && v.length <= 2000) return v;
  if (v !== undefined) warn(`${path}${key}`);
  return fallback;
}

function pickNullableStr(o: Obj, key: string, warn: Warn, path: string) {
  const v = o[key];
  if (v === null || v === undefined) return null;
  if (typeof v === "string" && v.length > 0 && v.length <= 512) return v;
  warn(`${path}${key}`);
  return null;
}

function pickEnum<T extends string>(o: Obj, key: string, allowed: readonly T[], fallback: T, warn: Warn, path: string): T {
  const v = o[key];
  if (typeof v === "string" && (allowed as readonly string[]).includes(v)) return v as T;
  if (v !== undefined) warn(`${path}${key}`);
  return fallback;
}

function pickNullableEnum<T extends string>(o: Obj, key: string, allowed: readonly T[], warn: Warn, path: string): T | null {
  const v = o[key];
  if (v === null || v === undefined) return null;
  return pickEnum<T | "">(o, key, allowed, "", warn, path) || null;
}

function pickThreads(o: Obj, key: string, warn: Warn, path: string): number | null {
  const v = o[key];
  if (v === null || v === undefined) return null;
  if (typeof v === "number" && Number.isSafeInteger(v) && v >= CPU_THREADS_RANGE[0] && v <= CPU_THREADS_RANGE[1]) {
    return v;
  }
  warn(`${path}${key}`);
  return null;
}

function pickBool(o: Obj, key: string, fallback: boolean, warn: Warn, path: string): boolean {
  const v = o[key];
  if (typeof v === "boolean") return v;
  if (v !== undefined) warn(`${path}${key}`);
  return fallback;
}

function pickPaths(o: Obj, key: string, warn: Warn, path: string): string[] {
  const v = o[key];
  if (v === undefined) return [];
  if (
    Array.isArray(v) &&
    v.length <= MAX_CUDA_DIRS &&
    v.every((p) => typeof p === "string" && p.length > 0 && p.length <= MAX_PATH_CHARS)
  ) {
    return v as string[];
  }
  warn(`${path}${key}`);
  return [];
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

export interface SettingsResult {
  settings: Settings;
  /** Settings file location (shown on the settings page); null if not reported. */
  path: string | null;
  /** Fields that were malformed and replaced by defaults. */
  warnings: string[];
}

/**
 * Reads the `settings.get` / `settings.set` / `settings.apply_recommendation`
 * result. Missing or malformed fields fall back to defaults and are reported
 * through `warnings` instead of failing, so one odd value does not lock the user
 * out of the settings page.
 */
export function parseSettingsResult(result: unknown): SettingsResult {
  const warnings: string[] = [];
  const warn: Warn = (field) => warnings.push(field);
  const top = asObj(result);
  const root = asObj(top.settings);
  const media = asObj(root.media);
  const interpret = asObj(root.interpret);
  const asr = asObj(root.asr);
  const tr = asObj(root.translation);
  const d = DEFAULT_SETTINGS;
  const path = typeof top.path === "string" && top.path.length <= 4096 ? top.path : null;
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
        device: pickEnum(asr, "device", ASR_DEVICES, d.asr.device, warn, "asr."),
        fast_compute_type: pickNullableEnum(asr, "fast_compute_type", COMPUTE_TYPES, warn, "asr."),
        final_compute_type: pickNullableEnum(asr, "final_compute_type", COMPUTE_TYPES, warn, "asr."),
        fast_cpu_threads: pickThreads(asr, "fast_cpu_threads", warn, "asr."),
        final_cpu_threads: pickThreads(asr, "final_cpu_threads", warn, "asr."),
        cuda_library_dirs: pickPaths(asr, "cuda_library_dirs", warn, "asr."),
      },
      translation: {
        base_url: pickStr(tr, "base_url", d.translation.base_url, warn, "translation."),
        fast_model: pickStr(tr, "fast_model", d.translation.fast_model, warn, "translation."),
        final_model: pickStr(tr, "final_model", d.translation.final_model, warn, "translation."),
        api_key_env: pickStr(tr, "api_key_env", d.translation.api_key_env, warn, "translation."),
        allow_restricted: pickBool(tr, "allow_restricted", d.translation.allow_restricted, warn, "translation."),
      },
      subtitle: overlayStyleFromSubtitle(root.subtitle),
    },
    path,
    warnings,
  };
}

/**
 * `settings.apply_recommendation`: the saved settings plus what was written
 * (`applied`, shown to the user) and the recommendation it came from.
 */
export function parseAppliedRecommendation(result: unknown): AppliedRecommendation {
  const p = "settings.apply_recommendation";
  const o = asObj(result);
  if (typeof o.applied !== "object" || o.applied === null || Array.isArray(o.applied)) {
    throw new ValidationError(`${p}.applied`, "expected an object");
  }
  const { settings, path, warnings } = parseSettingsResult(result);
  return {
    settings,
    path,
    warnings,
    applied: o.applied as Record<string, unknown>,
    recommendation: parseRecommendation(o.recommendation, `${p}.recommendation`),
  };
}

// ---------------------------------------------------------------- settings page

/** Raw values of the settings page form (strings straight from inputs). */
export interface SettingsForm {
  base_url: string;
  api_key_env: string;
  /** Checkbox: "I am not in the EU, UK or South Korea and accept the licences". */
  allow_restricted: boolean;
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

/** Checks the settings page form; returns per-field messages (empty when valid). */
export function validateSettingsForm(form: SettingsForm): FormErrors {
  const errors: FormErrors = {};
  const url = form.base_url.trim();
  try {
    const parsed = new URL(url);
    if (parsed.protocol !== "http:" && parsed.protocol !== "https:") {
      errors.base_url = "網址要以 http:// 或 https:// 開頭。";
    }
  } catch {
    // URL() throws for anything unparsable; that is exactly the user error to report.
    errors.base_url = url === "" ? "請填寫翻譯服務的網址。" : "這不是有效的網址。";
  }
  const env = form.api_key_env.trim();
  if (!ENV_NAME.test(env)) {
    errors.api_key_env = "只能用英文字母、數字和底線，且不能以數字開頭。";
  } else if (env.length > 100) {
    errors.api_key_env = "名稱太長（最多 100 個字元）。";
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
      api_key_env: form.api_key_env.trim(),
      allow_restricted: form.allow_restricted,
    },
    subtitle: overlayStyleFromForm(form),
  };
}

export function formFromSettings(s: Settings): SettingsForm {
  return {
    base_url: s.translation.base_url,
    api_key_env: s.translation.api_key_env,
    allow_restricted: s.translation.allow_restricted,
    partner_language: s.partner_language,
    font_size: String(s.subtitle.font_size),
    background_opacity_percent: String(Math.round(s.subtitle.background_opacity * 100)),
    position: s.subtitle.position,
    max_lines: String(s.subtitle.max_lines),
  };
}

// ---------------------------------------------------------------- model combination

/**
 * Raw values of the model combination form (models page). Empty strings mean
 * "automatic" for precision and threads; library directories are one per line.
 */
export interface CombinationForm {
  fast_model: string;
  final_model: string;
  device: string;
  translation_fast: string;
  translation_final: string;
  fast_compute_type: string;
  final_compute_type: string;
  fast_cpu_threads: string;
  final_cpu_threads: string;
  cuda_library_dirs: string;
}

export type CombinationErrors = Partial<Record<keyof CombinationForm, string>>;

export function combinationFormFromSettings(s: Settings): CombinationForm {
  return {
    fast_model: s.asr.fast_model,
    final_model: s.asr.final_model,
    device: s.asr.device,
    translation_fast: s.translation.fast_model,
    translation_final: s.translation.final_model,
    fast_compute_type: s.asr.fast_compute_type ?? "",
    final_compute_type: s.asr.final_compute_type ?? "",
    fast_cpu_threads: s.asr.fast_cpu_threads === null ? "" : String(s.asr.fast_cpu_threads),
    final_cpu_threads: s.asr.final_cpu_threads === null ? "" : String(s.asr.final_cpu_threads),
    cuda_library_dirs: s.asr.cuda_library_dirs.join("\n"),
  };
}

function cudaDirs(raw: string): string[] {
  return raw
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter((line) => line !== "");
}

/** Windows drive or UNC path, or a POSIX path; the engine checks for its own OS. */
function looksAbsolute(path: string): boolean {
  return /^[A-Za-z]:[\\/]/.test(path) || /^\\\\[^\\]/.test(path) || path.startsWith("/");
}

/**
 * Local checks that do not need the engine (format and ranges), run before
 * `models.validate`, which checks the combination against the hardware.
 */
export function validateCombinationForm(form: CombinationForm): CombinationErrors {
  const errors: CombinationErrors = {};
  for (const key of ["fast_model", "final_model"] as const) {
    if (!ASR_MODEL_ID.test(form[key])) errors[key] = "請選擇語音辨識模型。";
  }
  if (!(ASR_DEVICES as readonly string[]).includes(form.device)) {
    errors.device = "請選擇運算裝置。";
  }
  for (const key of ["translation_fast", "translation_final"] as const) {
    const v = form[key].trim();
    if (v === "") errors[key] = "請填寫翻譯模型的名稱。";
    else if (v.length > MODEL_NAME_MAX) errors[key] = `模型名稱最多 ${MODEL_NAME_MAX} 個字元。`;
  }
  for (const key of ["fast_compute_type", "final_compute_type"] as const) {
    const v = form[key];
    if (v !== "" && !(COMPUTE_TYPES as readonly string[]).includes(v)) errors[key] = "請選擇精度。";
  }
  for (const key of ["fast_cpu_threads", "final_cpu_threads"] as const) {
    const raw = form[key].trim();
    if (raw === "") continue;
    const n = parseIntField(raw);
    if (n === null || n < CPU_THREADS_RANGE[0] || n > CPU_THREADS_RANGE[1]) {
      errors[key] = `執行緒數要在 ${CPU_THREADS_RANGE[0]} 到 ${CPU_THREADS_RANGE[1]} 之間，留空代表自動。`;
    }
  }
  const dirs = cudaDirs(form.cuda_library_dirs);
  if (dirs.length > MAX_CUDA_DIRS) {
    errors.cuda_library_dirs = `最多 ${MAX_CUDA_DIRS} 個資料夾。`;
  } else {
    const bad = dirs.find((d) => d.length > MAX_PATH_CHARS || !looksAbsolute(d));
    if (bad !== undefined) errors.cuda_library_dirs = `「${bad.slice(0, 80)}」不是完整路徑（例如 C:\\CUDA\\bin）。`;
  }
  return errors;
}

/**
 * Partial settings for `models.validate {settings}` and `settings.set`; call
 * only after `validateCombinationForm` passed. Every combination field is sent,
 * so "automatic" is an explicit null rather than "keep the old value".
 */
export function combinationPatch(form: CombinationForm): Record<string, unknown> {
  const threads = (raw: string): number | null => (raw.trim() === "" ? null : Number.parseInt(raw, 10));
  const precision = (raw: string): string | null => (raw === "" ? null : raw);
  return {
    asr: {
      fast_model: form.fast_model,
      final_model: form.final_model,
      device: form.device,
      fast_compute_type: precision(form.fast_compute_type),
      final_compute_type: precision(form.final_compute_type),
      fast_cpu_threads: threads(form.fast_cpu_threads),
      final_cpu_threads: threads(form.final_cpu_threads),
      cuda_library_dirs: cudaDirs(form.cuda_library_dirs),
    },
    translation: {
      fast_model: form.translation_fast.trim(),
      final_model: form.translation_final.trim(),
    },
  };
}

// ---------------------------------------------------------------- misc

/** Patch that remembers the mode and its device (`null` = system default). */
export function devicePatch(mode: Mode, deviceId: string | null): Record<string, unknown> {
  return { mode, [mode]: { device_id: deviceId } };
}

function enginePath(message: string): string {
  return message.split(":", 1)[0]?.trim() ?? "";
}

/**
 * The engine reports settings errors as `"<field.path>: <problem>"`; map the
 * path back to a settings page field so the message appears next to the input.
 */
export function formFieldForEnginePath(message: string): keyof SettingsForm | null {
  const map: Record<string, keyof SettingsForm> = {
    "translation.base_url": "base_url",
    "translation.api_key_env": "api_key_env",
    "translation.allow_restricted": "allow_restricted",
    partner_language: "partner_language",
    "subtitle.font_size": "font_size",
    "subtitle.background_opacity": "background_opacity_percent",
    "subtitle.position": "position",
    "subtitle.max_lines": "max_lines",
  };
  return map[enginePath(message)] ?? null;
}

/** Same mapping for the model combination form. */
export function combinationFieldForEnginePath(message: string): keyof CombinationForm | null {
  // List items are reported as e.g. "asr.cuda_library_dirs[3]".
  const path = enginePath(message).replace(/\[\d+\]$/, "");
  const map: Record<string, keyof CombinationForm> = {
    "asr.fast_model": "fast_model",
    "asr.final_model": "final_model",
    "asr.device": "device",
    "asr.fast_compute_type": "fast_compute_type",
    "asr.final_compute_type": "final_compute_type",
    "asr.fast_cpu_threads": "fast_cpu_threads",
    "asr.final_cpu_threads": "final_cpu_threads",
    "asr.cuda_library_dirs": "cuda_library_dirs",
    "translation.fast_model": "translation_fast",
    "translation.final_model": "translation_final",
  };
  return map[path] ?? null;
}
