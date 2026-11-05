/**
 * Runtime validation of everything the webview receives from Rust.
 *
 * Role: Rust already validates engine output, but the webview must not trust an
 * `unknown` payload blindly either (defence in depth, and the command results
 * such as `models.list` are forwarded by Rust without a schema). Each parser
 * either returns a fully typed value or throws `ValidationError` with the path
 * of the offending field.
 */

import type {
  AboutInfo,
  AsrChoice,
  AsrModel,
  BridgeStatus,
  CombinationCheck,
  CommandError,
  DeviceInfo,
  DownloadKind,
  DownloadProgressEvent,
  EngineInfo,
  GpuInfo,
  GpuRuntime,
  HardwareInfo,
  HardwareReport,
  LogEvent,
  ModelRole,
  ModelsList,
  ObsStatus,
  OutputInfo,
  OverlaySnapshot,
  OverlayStyle,
  ShortcutInfo,
  Recommendation,
  RecommendationPreview,
  StatusEvent,
  SubtitleEvent,
  TranslationModel,
  TranslationServerInfo,
  TranslationStatus,
  UpdateDownloadEvent,
  UpdateInfo,
} from "./types";
import { parseEngineMessages } from "./messages";

export class ValidationError extends Error {
  constructor(path: string, problem: string) {
    super(`${path}: ${problem}`);
    this.name = "ValidationError";
  }
}

type Obj = Record<string, unknown>;

function obj(v: unknown, path: string): Obj {
  if (typeof v !== "object" || v === null || Array.isArray(v)) {
    throw new ValidationError(path, "expected an object");
  }
  return v as Obj;
}

function str(o: Obj, key: string, path: string, maxLen = 10_000): string {
  const v = o[key];
  if (typeof v !== "string") throw new ValidationError(`${path}.${key}`, "expected a string");
  if (v.length > maxLen) throw new ValidationError(`${path}.${key}`, `longer than ${maxLen}`);
  return v;
}

function optStr(o: Obj, key: string, path: string): string | null {
  const v = o[key];
  if (v === undefined || v === null) return null;
  return str(o, key, path);
}

function uint(o: Obj, key: string, path: string): number {
  const v = o[key];
  if (typeof v !== "number" || !Number.isSafeInteger(v) || v < 0) {
    throw new ValidationError(`${path}.${key}`, "expected a non-negative integer");
  }
  return v;
}

function optUint(o: Obj, key: string, path: string): number | null {
  const v = o[key];
  if (v === undefined || v === null) return null;
  return uint(o, key, path);
}

function optBool(o: Obj, key: string, path: string): boolean | null {
  const v = o[key];
  if (v === undefined || v === null) return null;
  return bool(o, key, path);
}

/** Finite non-negative number (sizes in GB and the like); null when absent. */
function optNum(o: Obj, key: string, path: string): number | null {
  const v = o[key];
  if (v === undefined || v === null) return null;
  if (typeof v !== "number" || !Number.isFinite(v) || v < 0) {
    throw new ValidationError(`${path}.${key}`, "expected a non-negative number");
  }
  return v;
}

function bool(o: Obj, key: string, path: string): boolean {
  const v = o[key];
  if (typeof v !== "boolean") throw new ValidationError(`${path}.${key}`, "expected a boolean");
  return v;
}

function oneOf<T extends string>(o: Obj, key: string, path: string, allowed: readonly T[]): T {
  const v = o[key];
  if (typeof v !== "string" || !(allowed as readonly string[]).includes(v)) {
    throw new ValidationError(`${path}.${key}`, `expected one of ${allowed.join(", ")}`);
  }
  return v as T;
}

function arr(o: Obj, key: string, path: string): unknown[] {
  const v = o[key];
  if (!Array.isArray(v)) throw new ValidationError(`${path}.${key}`, "expected an array");
  return v;
}

const LANGUAGE_TAG = /^[A-Za-z0-9-]{0,16}$/;

function lang(o: Obj, key: string, path: string): string {
  const v = str(o, key, path, 16);
  if (!LANGUAGE_TAG.test(v)) throw new ValidationError(`${path}.${key}`, "not a language tag");
  return v;
}

export function parseSubtitle(v: unknown): SubtitleEvent {
  const p = "subtitle";
  const o = obj(v, p);
  const ev: SubtitleEvent = {
    utterance_id: uint(o, "utterance_id", p),
    original: str(o, "original", p),
    translation: str(o, "translation", p),
    source_language: lang(o, "source_language", p),
    target_language: lang(o, "target_language", p),
    is_final: bool(o, "is_final", p),
    start_sample: uint(o, "start_sample", p),
    end_sample: uint(o, "end_sample", p),
    session_id: optUint(o, "session_id", p),
  };
  if (ev.end_sample < ev.start_sample) {
    throw new ValidationError(`${p}.end_sample`, "before start_sample");
  }
  return ev;
}

/** Translation model names as the engine accepts them (`models.pull_translation`). */
export const TRANSLATION_MODEL_NAME = /^(?!.*\.\.)[A-Za-z0-9][A-Za-z0-9._:/-]{0,199}$/;

export function parseStatus(v: unknown): StatusEvent {
  const p = "status";
  const o = obj(v, p);
  const code = optStr(o, "code", p);
  const data = typeof o.data === "object" && o.data !== null && !Array.isArray(o.data) ? (o.data as Obj) : {};
  // Only names the engine could pull are kept: they are sent back to it as-is.
  const missing =
    code === "translation_model_missing"
      ? strList(data.models, 10, 200).filter((m) => TRANSLATION_MODEL_NAME.test(m))
      : [];
  return {
    state: oneOf(o, "state", p, ["idle", "loading", "running", "error"] as const),
    detail: optStr(o, "detail", p),
    session_id: optUint(o, "session_id", p),
    code,
    missing_translation_models: [...new Set(missing)],
  };
}

const DOWNLOAD_KINDS: readonly DownloadKind[] = ["asr", "gpu_runtime", "translation"];

export function parseDownloadProgress(v: unknown): DownloadProgressEvent {
  const p = "download_progress";
  const o = obj(v, p);
  return {
    model: str(o, "model", p, 200),
    // Unknown kinds (a newer engine) are treated like none: matched by name only.
    kind: (DOWNLOAD_KINDS as readonly unknown[]).includes(o.kind) ? (o.kind as DownloadKind) : null,
    downloaded_bytes: uint(o, "downloaded_bytes", p),
    total_bytes: uint(o, "total_bytes", p),
    state:
      o.state === undefined
        ? "downloading"
        : oneOf(o, "state", p, ["downloading", "done", "error"] as const),
    error: optStr(o, "error", p),
  };
}

export function parseLog(v: unknown): LogEvent {
  const p = "log";
  const o = obj(v, p);
  return {
    level: oneOf(o, "level", p, ["debug", "info", "warning", "error"] as const),
    message: str(o, "message", p),
  };
}

export function parseEngineInfo(v: unknown): EngineInfo {
  const p = "engine.info";
  const o = obj(v, p);
  return {
    version: str(o, "version", p, 100),
    protocol_version: uint(o, "protocol_version", p),
    platform: str(o, "platform", p, 100),
    fake: o.fake === true,
  };
}

export function parseBridgeStatus(v: unknown): BridgeStatus {
  const p = "bridge";
  const o = obj(v, p);
  const source = o.source;
  return {
    phase: oneOf(o, "phase", p, ["starting", "ready", "restarting", "stopped", "failed"] as const),
    detail: optStr(o, "detail", p),
    restarts: uint(o, "restarts", p),
    command: optStr(o, "command", p),
    source:
      source === null || source === undefined
        ? null
        : oneOf(o, "source", p, ["env", "dev_uv", "sidecar"] as const),
    info: o.info === null || o.info === undefined ? null : parseEngineInfo(o.info),
  };
}

export const FONT_SIZE_RANGE = [14, 96] as const;
export const MAX_LINES_RANGE = [1, 3] as const;
/** Same bounds as Rust `MIN_DISPLAY_MS_RANGE` / `IDLE_CLEAR_SECS_RANGE`. */
export const MIN_DISPLAY_MS_RANGE = [0, 10_000] as const;
export const IDLE_CLEAR_SECS_RANGE = [0, 600] as const;
export const DEFAULT_MIN_DISPLAY_MS = 1500;
export const DEFAULT_IDLE_CLEAR_SECS = 8;

/** An optional integer in `range`; `fallback` when absent (older Rust builds). */
function rangedOr(o: Obj, key: string, path: string, range: readonly [number, number], fallback: number): number {
  if (o[key] === undefined) return fallback;
  const v = uint(o, key, path);
  if (v < range[0] || v > range[1]) throw new ValidationError(`${path}.${key}`, "out of range");
  return v;
}

export function parseOverlayStyle(v: unknown): OverlayStyle {
  const p = "style";
  const o = obj(v, p);
  const fontSize = uint(o, "font_size", p);
  const maxLines = uint(o, "max_lines", p);
  const opacity = o.background_opacity;
  if (fontSize < FONT_SIZE_RANGE[0] || fontSize > FONT_SIZE_RANGE[1]) {
    throw new ValidationError(`${p}.font_size`, "out of range");
  }
  if (maxLines < MAX_LINES_RANGE[0] || maxLines > MAX_LINES_RANGE[1]) {
    throw new ValidationError(`${p}.max_lines`, "out of range");
  }
  if (typeof opacity !== "number" || !Number.isFinite(opacity) || opacity < 0 || opacity > 1) {
    throw new ValidationError(`${p}.background_opacity`, "expected a number in [0, 1]");
  }
  return {
    font_size: fontSize,
    background_opacity: opacity,
    position: oneOf(o, "position", p, ["top", "bottom"] as const),
    max_lines: maxLines,
    min_display_ms: rangedOr(o, "min_display_ms", p, MIN_DISPLAY_MS_RANGE, DEFAULT_MIN_DISPLAY_MS),
    idle_clear_secs: rangedOr(o, "idle_clear_secs", p, IDLE_CLEAR_SECS_RANGE, DEFAULT_IDLE_CLEAR_SECS),
  };
}

const SHORTCUT_ACTIONS = ["click_through", "toggle_translation", "clear_subtitles"] as const;

/** `OverlaySnapshot.shortcuts`; unknown actions (a newer Rust build) are dropped. */
function parseShortcuts(v: unknown, path: string): ShortcutInfo[] {
  if (v === undefined) return [];
  if (!Array.isArray(v)) throw new ValidationError(path, "expected an array");
  const out: ShortcutInfo[] = [];
  v.forEach((item, i) => {
    const ip = `${path}[${i}]`;
    const o = obj(item, ip);
    if (!(SHORTCUT_ACTIONS as readonly unknown[]).includes(o.action)) return;
    out.push({
      action: oneOf(o, "action", ip, SHORTCUT_ACTIONS),
      keys: str(o, "keys", ip, 100),
      registered: bool(o, "registered", ip),
    });
  });
  return out;
}

/** Payload of `shortcut://action` (the main window handles these). */
export function parseShortcutAction(v: unknown): ShortcutInfo["action"] {
  if (typeof v !== "string" || !(SHORTCUT_ACTIONS as readonly string[]).includes(v)) {
    throw new ValidationError("shortcut", `expected one of ${SHORTCUT_ACTIONS.join(", ")}`);
  }
  return v as ShortcutInfo["action"];
}

export function parseObsStatus(v: unknown): ObsStatus {
  const p = "obs_set_output";
  const o = obj(v, p);
  return {
    content: oneOf(o, "content", p, ["off", "original", "translation", "both"] as const),
    path: optStr(o, "path", p),
    error: optStr(o, "error", p),
  };
}

export function parseOutputInfo(v: unknown): OutputInfo {
  const p = "output_info";
  const o = obj(v, p);
  return { transcripts_dir: optStr(o, "transcripts_dir", p), obs_file: optStr(o, "obs_file", p) };
}

/** `transcript_save`: the full path of the saved file. */
export function parseSavedPath(v: unknown): string {
  if (typeof v !== "string" || v.length === 0 || v.length > 4096) {
    throw new ValidationError("transcript_save", "expected a path");
  }
  return v;
}

export function parseOverlaySnapshot(v: unknown): OverlaySnapshot {
  const p = "overlay";
  const o = obj(v, p);
  return {
    style: parseOverlayStyle(o.style),
    click_through: bool(o, "click_through", p),
    visible: bool(o, "visible", p),
    shortcut: str(o, "shortcut", p, 100),
    shortcut_registered: bool(o, "shortcut_registered", p),
    // Older Rust builds have no locale; a malformed one is ignored (the overlay follows the OS).
    locale: typeof o.locale === "string" && /^[A-Za-z]{2,3}(-[A-Za-z0-9]{2,8})?$/.test(o.locale) ? o.locale : null,
    // Older Rust builds have no flag: translations were always shown then.
    show_translation: o.show_translation !== false,
    shortcuts: parseShortcuts(o.shortcuts, `${p}.shortcuts`),
  };
}

export function parseDevices(v: unknown): DeviceInfo[] {
  const p = "devices.list";
  // The architecture spec says "a DeviceInfo list"; pipeline-engine.md wraps it
  // as {"devices": [...]}. Accept both.
  const list = Array.isArray(v) ? v : arr(obj(v, p), "devices", p);
  return list.map((item, i) => {
    const ip = `${p}[${i}]`;
    const o = obj(item, ip);
    return {
      id: str(o, "id", ip, 512),
      name: str(o, "name", ip, 512),
      kind: oneOf(o, "kind", ip, ["loopback", "microphone"] as const),
      is_default: bool(o, "is_default", ip),
    };
  });
}

/** List of strings; non-string items and overflow are dropped (display-only lists). */
function strList(v: unknown, maxItems = 50, maxLen = 2000): string[] {
  if (!Array.isArray(v)) return [];
  return v.filter((x): x is string => typeof x === "string" && x.length <= maxLen).slice(0, maxItems);
}

function roles(v: unknown): ModelRole[] {
  return strList(v, 4).filter((r): r is ModelRole => r === "fast" || r === "final");
}

function optObj(o: Obj, key: string, path: string): Obj {
  const v = o[key];
  return v === undefined || v === null ? {} : obj(v, `${path}.${key}`);
}

function parseAsrChoice(v: unknown, path: string): AsrChoice {
  const o = obj(v, path);
  return {
    model: str(o, "model", path, 100),
    device: str(o, "device", path, 20),
    compute_type: optStr(o, "compute_type", path),
    cpu_threads: optUint(o, "cpu_threads", path) ?? 0,
  };
}

export function parseRecommendation(v: unknown, p = "recommendation"): Recommendation {
  const o = obj(v, p);
  return {
    tier: str(o, "tier", p, 100),
    fast_asr: parseAsrChoice(o.fast_asr, `${p}.fast_asr`),
    final_asr: parseAsrChoice(o.final_asr, `${p}.final_asr`),
    translation_fast: optStr(o, "translation_fast", p),
    translation_final: optStr(o, "translation_final", p),
    translation_budget_gb: optNum(o, "translation_budget_gb", p),
    gpu_runtime_missing: bool(o, "gpu_runtime_missing", p),
    reasons: parseEngineMessages(o.reasons),
    warnings: parseEngineMessages(o.warnings),
    translation_status: (TRANSLATION_STATUSES as readonly unknown[]).includes(o.translation_status)
      ? (o.translation_status as TranslationStatus)
      : null,
  };
}

const TRANSLATION_STATUSES: readonly TranslationStatus[] = ["recommended", "remote", "not_needed", "unsupported"];

/**
 * `settings.preview_recommendation`. The fingerprint ends up in the settings
 * file (`ui.dismissed_recommendations`), so only short hex strings are accepted.
 */
export function parseRecommendationPreview(v: unknown): RecommendationPreview {
  const p = "settings.preview_recommendation";
  const o = obj(v, p);
  const fingerprint = str(o, "fingerprint", p, 64);
  if (!/^[0-9a-f]+$/.test(fingerprint)) throw new ValidationError(`${p}.fingerprint`, "expected a hex string");
  return {
    patch: obj(o.patch, `${p}.patch`),
    recommendation: parseRecommendation(o.recommendation, `${p}.recommendation`),
    differs: bool(o, "differs", p),
    fingerprint,
  };
}

function parseGpu(v: unknown, path: string): GpuInfo {
  const o = obj(v, path);
  return {
    index: uint(o, "index", path),
    name: str(o, "name", path, 200),
    memory_total_mb: uint(o, "memory_total_mb", path),
    memory_used_mb: optUint(o, "memory_used_mb", path),
    driver_version: optStr(o, "driver_version", path),
    compute_capability: optStr(o, "compute_capability", path),
  };
}

function parseTranslationServer(v: unknown, path: string): TranslationServerInfo | null {
  if (v === null || v === undefined) return null;
  const o = obj(v, path);
  const models = o.models;
  return {
    base_url: str(o, "base_url", path, 2000),
    is_local: optBool(o, "is_local", path) ?? false,
    reachable: optBool(o, "reachable", path) ?? false,
    models: Array.isArray(models) ? strList(models, 500, 200) : null,
    error: optStr(o, "error", path),
  };
}

function parseHardwareInfo(v: unknown, hp: string): HardwareInfo {
  const h = obj(v, hp);
  const cpu = optObj(h, "cpu", hp);
  const mem = optObj(h, "memory", hp);
  const cublas = optObj(h, "cublas", hp);
  return {
    os: str(h, "os", hp, 100),
    os_version: optStr(h, "os_version", hp) ?? "",
    machine: optStr(h, "machine", hp) ?? "",
    cpu: {
      model: optStr(cpu, "model", `${hp}.cpu`),
      physical_cores: optUint(cpu, "physical_cores", `${hp}.cpu`),
      logical_cores: optUint(cpu, "logical_cores", `${hp}.cpu`),
      architecture: optStr(cpu, "architecture", `${hp}.cpu`),
    },
    cpu_count: optUint(h, "cpu_count", hp),
    memory: {
      // ram_bytes is the first-version name of the same number.
      total_bytes: optUint(mem, "total_bytes", `${hp}.memory`) ?? optUint(h, "ram_bytes", hp),
      available_bytes: optUint(mem, "available_bytes", `${hp}.memory`),
    },
    gpus: arr(h, "gpus", hp).map((g, i) => parseGpu(g, `${hp}.gpus[${i}]`)),
    apple_silicon: bool(h, "apple_silicon", hp),
    cublas: {
      supported_platform: optBool(cublas, "supported_platform", `${hp}.cublas`) ?? false,
      available: optBool(cublas, "available", `${hp}.cublas`) ?? false,
      library_dirs: strList(cublas.library_dirs, 16),
      source: optStr(cublas, "source", `${hp}.cublas`),
    },
    translation_server: parseTranslationServer(h.translation_server, `${hp}.translation_server`),
    errors: strList(h.errors),
  };
}

/** `hardware.detect`: `{hardware, recommendation}`. */
export function parseHardware(v: unknown): HardwareReport {
  const p = "hardware.detect";
  const o = obj(v, p);
  return {
    hardware: parseHardwareInfo(o.hardware, `${p}.hardware`),
    recommendation: parseRecommendation(o.recommendation, `${p}.recommendation`),
  };
}

function parseAsrModel(v: unknown, path: string): AsrModel {
  const o = obj(v, path);
  return {
    id: str(o, "id", path, 100),
    size_bytes: uint(o, "size_bytes", path),
    downloaded: bool(o, "downloaded", path),
    license: str(o, "license", path, 200),
    speed_tier: str(o, "speed_tier", path, 50),
    no_speech_reliable: optBool(o, "no_speech_reliable", path),
    cpu_ram_mb: optUint(o, "cpu_ram_mb", path),
    vram_mb: optUint(o, "vram_mb", path),
    vram_int8_mb: optUint(o, "vram_int8_mb", path),
    recommended_for: roles(o.recommended_for),
  };
}

function parseGpuRuntime(v: unknown, p: string): GpuRuntime {
  const o = obj(v, p);
  return {
    id: str(o, "id", p, 100),
    supported: bool(o, "supported", p),
    installed: bool(o, "installed", p),
    package: optStr(o, "package", p),
    version: optStr(o, "version", p),
    path: optStr(o, "path", p),
    download_bytes: optUint(o, "download_bytes", p),
    installed_bytes: optUint(o, "installed_bytes", p),
    license: optStr(o, "license", p),
    license_url: optStr(o, "license_url", p),
    cublas_available: optBool(o, "cublas_available", p) ?? false,
    cublas_source: optStr(o, "cublas_source", p),
  };
}

function parseTranslationModel(v: unknown, path: string): TranslationModel {
  const o = obj(v, path);
  const name = str(o, "name", path, 200);
  return {
    name,
    label: optStr(o, "label", path) ?? name,
    installed: optBool(o, "installed", path),
    known: optBool(o, "known", path) ?? false,
    recommended_for: roles(o.recommended_for),
    license: optStr(o, "license", path),
    license_url: optStr(o, "license_url", path),
    commercial_use: optBool(o, "commercial_use", path),
    excluded_regions: strList(o.excluded_regions, 20, 20),
    approx_vram_gb: optNum(o, "approx_vram_gb", path),
  };
}

/**
 * `models.list` (hardware.md `models_overview()`). The first-version fields the
 * engine still sends (`translation.base_url/installed/error/presets`) are
 * ignored: `translation.server` and `translation.models` carry the same data.
 */
export function parseModels(v: unknown): ModelsList {
  const p = "models.list";
  const o = obj(v, p);
  const tp = `${p}.translation`;
  const t = obj(o.translation, tp);
  const sp = `${tp}.server`;
  const server = obj(t.server, sp);
  return {
    asr: arr(o, "asr", p).map((m, i) => parseAsrModel(m, `${p}.asr[${i}]`)),
    gpu_runtime: parseGpuRuntime(o.gpu_runtime, `${p}.gpu_runtime`),
    translation: {
      server: {
        base_url: str(server, "base_url", sp, 2000),
        is_local: optBool(server, "is_local", sp) ?? false,
        reachable: bool(server, "reachable", sp),
        error: optStr(server, "error", sp),
      },
      models: arr(t, "models", tp).map((m, i) => parseTranslationModel(m, `${tp}.models[${i}]`)),
    },
  };
}

/** `models.validate`: `{ok, errors, warnings}`. */
export function parseCombinationCheck(v: unknown): CombinationCheck {
  const p = "models.validate";
  const o = obj(v, p);
  const errors = parseEngineMessages(o.errors);
  // The engine sets ok=false exactly when there are errors; if the two ever
  // disagree, trust the errors so saving is never offered next to one.
  return { ok: bool(o, "ok", p) && errors.length === 0, errors, warnings: parseEngineMessages(o.warnings) };
}

export function parseAbout(v: unknown): AboutInfo {
  const p = "app_about";
  const o = obj(v, p);
  return {
    license: str(o, "license", p, 100),
    app_licenses: optStr(o, "app_licenses", p),
    engine_licenses: optStr(o, "engine_licenses", p),
  };
}

/** `update_check` result; `null` means no newer version. */
export function parseUpdateInfo(v: unknown): UpdateInfo | null {
  if (v === null) return null;
  const p = "update_check";
  const o = obj(v, p);
  return {
    version: str(o, "version", p, 100),
    currentVersion: str(o, "currentVersion", p, 100),
    notes: optStr(o, "notes", p),
  };
}

/** One `update_install` progress message. */
export function parseUpdateDownloadEvent(v: unknown): UpdateDownloadEvent {
  const p = "update_install";
  const o = obj(v, p);
  switch (o.event) {
    case "Started":
      return { event: "Started", contentLength: optUint(obj(o.data, `${p}.data`), "contentLength", `${p}.data`) };
    case "Progress":
      return { event: "Progress", chunkLength: uint(obj(o.data, `${p}.data`), "chunkLength", `${p}.data`) };
    case "Finished":
      return { event: "Finished" };
    default:
      throw new ValidationError(`${p}.event`, "unknown download event");
  }
}

/** Recognises the serialized Rust `RequestError`; anything else is not one. */
export function asCommandError(e: unknown): CommandError | null {
  if (typeof e !== "object" || e === null) return null;
  const o = e as Obj;
  switch (o.kind) {
    case "engine":
      return typeof o.code === "string" && typeof o.message === "string"
        ? { kind: "engine", code: o.code, message: o.message }
        : null;
    case "timeout":
      return typeof o.method === "string" && typeof o.timeout_ms === "number"
        ? { kind: "timeout", method: o.method, timeout_ms: o.timeout_ms }
        : null;
    case "disconnected":
      return typeof o.detail === "string" ? { kind: "disconnected", detail: o.detail } : null;
    case "invalid_input":
      return typeof o.message === "string" ? { kind: "invalid_input", message: o.message } : null;
    default:
      return null;
  }
}
