/**
 * Engine settings as the UI sees them: parsing, form validation, partial updates.
 *
 * Role: the engine owns `settings.json` (docs/specs/pipeline-engine.md "config",
 * schema 2) and validates authoritatively; this module reads the parts the UI
 * edits, checks form input before sending it (so mistakes are pointed at the
 * right field immediately), and builds the partial objects `settings.set` and
 * `models.validate` deep-merge. Two forms exist: the settings page (translation
 * service, licence region, subtitle appearance) and the model
 * combination on the models page (ASR and translation models, device,
 * precision, threads). How a hardware recommendation becomes settings is not
 * here on purpose: `settings.apply_recommendation` keeps that rule in the engine.
 * The language section has its own module (`languages.ts`), and so has the
 * glossary editor (`glossary.ts`, `translation.glossary`).
 * Overlay appearance, reading rhythm and the OBS text choice live in the
 * engine's opaque `subtitle` object, the UI's own state (dismissed
 * recommendation offers, interface language, transcript auto-save) in the
 * opaque `ui` object. Validation messages come from the i18n dictionary.
 */

import { DEFAULT_GLOSSARY, glossaryFromSettings } from "./glossary";
import { isUiLocale, t, type UiLocalePreference } from "./i18n";
import { isLanguageCode } from "./languages";

import type {
  AppliedRecommendation,
  AsrDevice,
  ComputeType,
  Mode,
  ObsContent,
  OverlayStyle,
  Settings,
  UiState,
} from "./types";
import {
  DEFAULT_IDLE_CLEAR_SECS,
  DEFAULT_MIN_DISPLAY_MS,
  FONT_SIZE_RANGE,
  IDLE_CLEAR_SECS_RANGE,
  MAX_LINES_RANGE,
  MIN_DISPLAY_MS_RANGE,
  ValidationError,
  parseRecommendation,
} from "./validate";

export const DEFAULT_OVERLAY_STYLE: OverlayStyle = {
  font_size: 32,
  background_opacity: 0.45,
  position: "bottom",
  max_lines: 2,
  min_display_ms: DEFAULT_MIN_DISPLAY_MS,
  idle_clear_secs: DEFAULT_IDLE_CLEAR_SECS,
};

export const OBS_CONTENTS: readonly ObsContent[] = ["off", "original", "translation", "both"];

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
  source_language: "ja",
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
    enabled: true,
    base_url: "http://localhost:11434/v1",
    fast_model: "qwen3.5:4b",
    final_model: "qwen3.5:4b",
    api_key_env: "KIKITORI_TRANSLATE_API_KEY",
    allow_restricted: false,
    glossary: DEFAULT_GLOSSARY,
  },
  subtitle: DEFAULT_OVERLAY_STYLE,
  obs_output: "off",
  ui: { dismissed_recommendations: [], locale: "system", auto_save_transcripts: false },
};

/** Recommendation fingerprints are short hex strings (engine `recommendation_fingerprint`). */
export const FINGERPRINT_PATTERN = /^[0-9a-f]{1,64}$/;
/** How many dismissed recommendation fingerprints `ui` keeps. */
export const MAX_DISMISSED = 10;

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

/**
 * A language code from the settings. Only the format is checked: the engine
 * owns the language list and has validated the value already.
 */
function pickLanguage(o: Obj, key: string, fallback: string, allowAuto: boolean, warn: Warn): string {
  const v = o[key];
  if (isLanguageCode(v, allowAuto)) return v;
  if (v !== undefined) warn(key);
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

/** The UI's state from the opaque `settings.ui`; malformed parts fall back to defaults. */
export function uiStateFromSettings(ui: unknown, warn: Warn): UiState {
  const o = asObj(ui);
  return {
    dismissed_recommendations: dismissedFromUi(o.dismissed_recommendations, warn),
    locale: localeFromUi(o.locale, warn),
    auto_save_transcripts: pickBool(o, "auto_save_transcripts", false, warn, "ui."),
  };
}

function dismissedFromUi(v: unknown, warn: Warn): string[] {
  if (v === undefined) return [];
  // Reading is more lenient than writing (MAX_DISMISSED): a list written by a later
  // version that keeps more entries must not be reported as malformed.
  if (Array.isArray(v) && v.length <= 100 && v.every((f) => typeof f === "string" && FINGERPRINT_PATTERN.test(f))) {
    return v as string[];
  }
  warn("ui.dismissed_recommendations");
  return [];
}

function localeFromUi(v: unknown, warn: Warn): UiLocalePreference {
  if (v === undefined || v === "system") return "system";
  if (isUiLocale(v)) return v;
  // 較新版本可能存了這一版還不認得的語系：視為「跟隨系統」，不當成壞欄位提示使用者。
  if (typeof v === "string" && v.length <= 35) return "system";
  warn("ui.locale");
  return "system";
}

/** Patch that stores the interface language; the engine deep-merges `ui`, so other keys stay. */
export function uiLocalePatch(locale: UiLocalePreference): Record<string, unknown> {
  return { ui: { locale } };
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
    min_display_ms: clampInt(o.min_display_ms, MIN_DISPLAY_MS_RANGE, DEFAULT_OVERLAY_STYLE.min_display_ms),
    idle_clear_secs: clampInt(o.idle_clear_secs, IDLE_CLEAR_SECS_RANGE, DEFAULT_OVERLAY_STYLE.idle_clear_secs),
  };
}

export interface SettingsResult {
  settings: Settings;
  /** Settings file location (shown on the settings page); null if not reported. */
  path: string | null;
  /** Fields that were malformed and replaced by defaults. */
  warnings: string[];
  /**
   * No settings file exists yet (first run). False when the engine does not
   * report it (older engine): then the UI never changes settings on its own.
   */
  isNew: boolean;
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
      source_language: pickLanguage(root, "source_language", d.source_language, true, warn),
      target_language: pickLanguage(root, "target_language", d.target_language, false, warn),
      partner_language: pickLanguage(root, "partner_language", d.partner_language, false, warn),
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
        enabled: pickBool(tr, "enabled", d.translation.enabled, warn, "translation."),
        base_url: pickStr(tr, "base_url", d.translation.base_url, warn, "translation."),
        fast_model: pickStr(tr, "fast_model", d.translation.fast_model, warn, "translation."),
        final_model: pickStr(tr, "final_model", d.translation.final_model, warn, "translation."),
        api_key_env: pickStr(tr, "api_key_env", d.translation.api_key_env, warn, "translation."),
        allow_restricted: pickBool(tr, "allow_restricted", d.translation.allow_restricted, warn, "translation."),
        glossary: glossaryFromSettings(tr.glossary, warn),
      },
      subtitle: overlayStyleFromSubtitle(root.subtitle),
      obs_output: pickEnum(asObj(root.subtitle), "obs_output", OBS_CONTENTS, d.obs_output, warn, "subtitle."),
      ui: uiStateFromSettings(root.ui, warn),
    },
    path,
    warnings,
    isNew: top.is_new === true,
  };
}

/**
 * `settings.apply_recommendation`: the saved settings plus what was written
 * (`applied`, shown to the user) and the recommendation it came from.
 */
export function parseAppliedRecommendation(result: unknown): AppliedRecommendation {
  const p = "settings.apply_recommendation";
  const o = asObj(result);
  // null: `only_if_new` was sent and settings had already been saved, so nothing was written.
  if (o.applied !== null && (typeof o.applied !== "object" || Array.isArray(o.applied))) {
    throw new ValidationError(`${p}.applied`, "expected an object or null");
  }
  const { settings, path, warnings, isNew } = parseSettingsResult(result);
  return {
    settings,
    path,
    warnings,
    isNew,
    applied: o.applied as Record<string, unknown> | null,
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
  font_size: string;
  background_opacity_percent: string;
  position: string;
  max_lines: string;
  /** Seconds, decimals allowed ("1.5"); stored as `subtitle.min_display_ms`. */
  min_display_secs: string;
  idle_clear_secs: string;
  obs_output: string;
  auto_save_transcripts: boolean;
}

export type FormErrors = Partial<Record<keyof SettingsForm, string>>;

const ENV_NAME = /^[A-Za-z_][A-Za-z0-9_]*$/;
const ENV_NAME_MAX = 100;
const MODEL_NAME_MAX = 200;

function parseIntField(raw: string): number | null {
  if (!/^\s*\d+\s*$/.test(raw)) return null;
  return Number.parseInt(raw, 10);
}

/** Non-negative seconds with an optional fraction ("1", "1.5", "0.25"). */
function parseSecondsField(raw: string): number | null {
  if (!/^\s*\d{1,6}(\.\d{1,3})?\s*$/.test(raw)) return null;
  return Number.parseFloat(raw);
}

/** Checks the settings page form; returns per-field messages (empty when valid). */
export function validateSettingsForm(form: SettingsForm): FormErrors {
  const errors: FormErrors = {};
  const url = form.base_url.trim();
  try {
    const parsed = new URL(url);
    if (parsed.protocol !== "http:" && parsed.protocol !== "https:") {
      errors.base_url = t("validation.urlScheme");
    }
  } catch {
    // URL() throws for anything unparsable; that is exactly the user error to report.
    errors.base_url = url === "" ? t("validation.urlEmpty") : t("validation.urlInvalid");
  }
  const env = form.api_key_env.trim();
  if (!ENV_NAME.test(env)) {
    errors.api_key_env = t("validation.envPattern");
  } else if (env.length > ENV_NAME_MAX) {
    errors.api_key_env = t("validation.envTooLong", { max: ENV_NAME_MAX });
  }
  const size = parseIntField(form.font_size);
  if (size === null || size < FONT_SIZE_RANGE[0] || size > FONT_SIZE_RANGE[1]) {
    errors.font_size = t("validation.fontSize", { min: FONT_SIZE_RANGE[0], max: FONT_SIZE_RANGE[1] });
  }
  const opacity = parseIntField(form.background_opacity_percent);
  if (opacity === null || opacity > 100) {
    errors.background_opacity_percent = t("validation.opacity");
  }
  if (form.position !== "top" && form.position !== "bottom") {
    errors.position = t("validation.position");
  }
  const lines = parseIntField(form.max_lines);
  if (lines === null || lines < MAX_LINES_RANGE[0] || lines > MAX_LINES_RANGE[1]) {
    errors.max_lines = t("validation.lines", { min: MAX_LINES_RANGE[0], max: MAX_LINES_RANGE[1] });
  }
  const minSecs = parseSecondsField(form.min_display_secs);
  const [msLo, msHi] = MIN_DISPLAY_MS_RANGE;
  if (minSecs === null || minSecs * 1000 < msLo || minSecs * 1000 > msHi) {
    errors.min_display_secs = t("validation.minDisplay", { min: msLo / 1000, max: msHi / 1000 });
  }
  const idle = parseIntField(form.idle_clear_secs);
  if (idle === null || idle < IDLE_CLEAR_SECS_RANGE[0] || idle > IDLE_CLEAR_SECS_RANGE[1]) {
    errors.idle_clear_secs = t("validation.idleClear", { min: IDLE_CLEAR_SECS_RANGE[0], max: IDLE_CLEAR_SECS_RANGE[1] });
  }
  if (!(OBS_CONTENTS as readonly string[]).includes(form.obs_output)) {
    errors.obs_output = t("validation.obsOutput");
  }
  return errors;
}

export function overlayStyleFromForm(form: SettingsForm): OverlayStyle {
  return {
    font_size: Number.parseInt(form.font_size, 10),
    background_opacity: Number.parseInt(form.background_opacity_percent, 10) / 100,
    position: form.position === "top" ? "top" : "bottom",
    max_lines: Number.parseInt(form.max_lines, 10),
    min_display_ms: Math.round(Number.parseFloat(form.min_display_secs) * 1000),
    idle_clear_secs: Number.parseInt(form.idle_clear_secs, 10),
  };
}

/** The form's OBS choice; call only after validation passed. */
export function obsOutputFromForm(form: SettingsForm): ObsContent {
  return (OBS_CONTENTS as readonly string[]).includes(form.obs_output) ? (form.obs_output as ObsContent) : "off";
}

/**
 * Partial settings for `settings.set`; call only after validation passed.
 * `obs_output` sits in `subtitle` beside the style (the engine deep-merges and
 * never reads either), but is not part of the style Rust validates.
 */
export function settingsPatchFromForm(form: SettingsForm): Record<string, unknown> {
  return {
    translation: {
      base_url: form.base_url.trim(),
      api_key_env: form.api_key_env.trim(),
      allow_restricted: form.allow_restricted,
    },
    subtitle: { ...overlayStyleFromForm(form), obs_output: obsOutputFromForm(form) },
    ui: { auto_save_transcripts: form.auto_save_transcripts },
  };
}

export function formFromSettings(s: Settings): SettingsForm {
  return {
    base_url: s.translation.base_url,
    api_key_env: s.translation.api_key_env,
    allow_restricted: s.translation.allow_restricted,
    font_size: String(s.subtitle.font_size),
    background_opacity_percent: String(Math.round(s.subtitle.background_opacity * 100)),
    position: s.subtitle.position,
    max_lines: String(s.subtitle.max_lines),
    min_display_secs: String(s.subtitle.min_display_ms / 1000),
    idle_clear_secs: String(s.subtitle.idle_clear_secs),
    obs_output: s.obs_output,
    auto_save_transcripts: s.ui.auto_save_transcripts,
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
    if (!ASR_MODEL_ID.test(form[key])) errors[key] = t("validation.asrModel");
  }
  if (!(ASR_DEVICES as readonly string[]).includes(form.device)) {
    errors.device = t("validation.device");
  }
  for (const key of ["translation_fast", "translation_final"] as const) {
    const v = form[key].trim();
    if (v === "") errors[key] = t("validation.translationEmpty");
    else if (v.length > MODEL_NAME_MAX) errors[key] = t("validation.translationTooLong", { max: MODEL_NAME_MAX });
  }
  for (const key of ["fast_compute_type", "final_compute_type"] as const) {
    const v = form[key];
    if (v !== "" && !(COMPUTE_TYPES as readonly string[]).includes(v)) errors[key] = t("validation.computeType");
  }
  for (const key of ["fast_cpu_threads", "final_cpu_threads"] as const) {
    const raw = form[key].trim();
    if (raw === "") continue;
    const n = parseIntField(raw);
    if (n === null || n < CPU_THREADS_RANGE[0] || n > CPU_THREADS_RANGE[1]) {
      errors[key] = t("validation.threads", { min: CPU_THREADS_RANGE[0], max: CPU_THREADS_RANGE[1] });
    }
  }
  const dirs = cudaDirs(form.cuda_library_dirs);
  if (dirs.length > MAX_CUDA_DIRS) {
    errors.cuda_library_dirs = t("validation.cudaDirsTooMany", { max: MAX_CUDA_DIRS });
  } else {
    const bad = dirs.find((d) => d.length > MAX_PATH_CHARS || !looksAbsolute(d));
    if (bad !== undefined) errors.cuda_library_dirs = t("validation.cudaDirNotAbsolute", { path: bad.slice(0, 80) });
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
    "subtitle.font_size": "font_size",
    "subtitle.background_opacity": "background_opacity_percent",
    "subtitle.position": "position",
    "subtitle.max_lines": "max_lines",
    "subtitle.min_display_ms": "min_display_secs",
    "subtitle.idle_clear_secs": "idle_clear_secs",
    "subtitle.obs_output": "obs_output",
    "ui.auto_save_transcripts": "auto_save_transcripts",
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
