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
  AsrChoice,
  AsrModel,
  BridgeStatus,
  CommandError,
  DeviceInfo,
  DownloadProgressEvent,
  EngineInfo,
  GpuRuntime,
  HardwareReport,
  LogEvent,
  ModelsList,
  OverlaySnapshot,
  OverlayStyle,
  Recommendation,
  StatusEvent,
  SubtitleEvent,
  TranslationPreset,
} from "./types";

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

export function parseStatus(v: unknown): StatusEvent {
  const p = "status";
  const o = obj(v, p);
  return {
    state: oneOf(o, "state", p, ["idle", "loading", "running", "error"] as const),
    detail: optStr(o, "detail", p),
    session_id: optUint(o, "session_id", p),
    code: optStr(o, "code", p),
  };
}

export function parseDownloadProgress(v: unknown): DownloadProgressEvent {
  const p = "download_progress";
  const o = obj(v, p);
  return {
    model: str(o, "model", p, 200),
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
  };
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

function strList(v: unknown, maxItems = 50): string[] {
  if (!Array.isArray(v)) return [];
  return v.filter((x): x is string => typeof x === "string" && x.length <= 2000).slice(0, maxItems);
}

function parseAsrChoice(v: unknown, path: string): AsrChoice | null {
  if (v === null || v === undefined) return null;
  const o = obj(v, path);
  return {
    model: str(o, "model", path, 100),
    device: str(o, "device", path, 20),
    compute_type: optStr(o, "compute_type", path),
    cpu_threads: optUint(o, "cpu_threads", path),
  };
}

export function parseRecommendation(v: unknown): Recommendation | null {
  const p = "hardware.detect.recommendation";
  if (v === null || v === undefined) return null;
  const o = obj(v, p);
  return {
    fast_asr: parseAsrChoice(o.fast_asr, `${p}.fast_asr`),
    final_asr: parseAsrChoice(o.final_asr, `${p}.final_asr`),
    translation_fast: optStr(o, "translation_fast", p),
    translation_final: optStr(o, "translation_final", p),
    gpu_runtime_missing: o.gpu_runtime_missing === true,
    reasons: strList(o.reasons),
    warnings: strList(o.warnings),
  };
}

export function parseHardware(v: unknown): HardwareReport {
  const p = "hardware.detect";
  const o = obj(v, p);
  const h = obj(o.hardware, `${p}.hardware`);
  const hp = `${p}.hardware`;
  const gpus = arr(h, "gpus", hp).map((g, i) => {
    const gp = `${hp}.gpus[${i}]`;
    const go = obj(g, gp);
    // hardware.md reports memory_total_mb; the first engine version vram_bytes.
    const mb = optUint(go, "memory_total_mb", gp);
    return {
      name: str(go, "name", gp, 200),
      vram_bytes: optUint(go, "vram_bytes", gp) ?? (mb === null ? null : mb * 1024 * 1024),
    };
  });
  return {
    hardware: {
      os: str(h, "os", hp, 100),
      os_version: optStr(h, "os_version", hp) ?? "",
      machine: optStr(h, "machine", hp) ?? "",
      cpu_count: optUint(h, "cpu_count", hp),
      ram_bytes: optUint(h, "ram_bytes", hp),
      gpus,
      apple_silicon: h.apple_silicon === true,
    },
    recommendation: parseRecommendation(o.recommendation),
  };
}

function parseAsrModel(v: unknown, path: string): AsrModel {
  const o = obj(v, path);
  return {
    id: str(o, "id", path, 100),
    size_bytes: uint(o, "size_bytes", path),
    license: str(o, "license", path, 200),
    speed_tier: str(o, "speed_tier", path, 50),
    downloaded: bool(o, "downloaded", path),
    recommended_for: strList(o.recommended_for, 4),
  };
}

function parseGpuRuntime(v: unknown): GpuRuntime | null {
  const p = "models.list.gpu_runtime";
  if (v === null || v === undefined) return null;
  const o = obj(v, p);
  return {
    id: str(o, "id", p, 100),
    supported: bool(o, "supported", p),
    installed: bool(o, "installed", p),
    download_bytes: optUint(o, "download_bytes", p),
    license: optStr(o, "license", p),
    license_url: optStr(o, "license_url", p),
  };
}

/** hardware.md `translation.models[]` entry, mapped onto the preset shape. */
function parseTranslationModel(v: unknown, path: string): TranslationPreset {
  const o = obj(v, path);
  const installed = o.installed;
  const name = str(o, "name", path, 200);
  return {
    model: name,
    label: optStr(o, "label", path) ?? name,
    license: optStr(o, "license", path) ?? "未知",
    license_url: optStr(o, "license_url", path),
    commercial_use: o.commercial_use === true,
    excluded_regions: strList(o.excluded_regions, 20),
    installed: typeof installed === "boolean" ? installed : null,
  };
}

function parsePreset(v: unknown, path: string): TranslationPreset {
  const o = obj(v, path);
  const installed = o.installed;
  if (installed !== null && installed !== undefined && typeof installed !== "boolean") {
    throw new ValidationError(`${path}.installed`, "expected boolean or null");
  }
  return {
    model: str(o, "model", path, 200),
    label: str(o, "label", path, 200),
    license: str(o, "license", path, 200),
    license_url: optStr(o, "license_url", path),
    commercial_use: bool(o, "commercial_use", path),
    excluded_regions: arr(o, "excluded_regions", path).filter(
      (r): r is string => typeof r === "string",
    ),
    installed: typeof installed === "boolean" ? installed : null,
  };
}

/**
 * `models.list`. Accepts the first engine shape (`translation.{base_url,
 * installed, error, presets}`) and the hardware.md shape (`translation.{server,
 * models}` plus `gpu_runtime`).
 */
export function parseModels(v: unknown): ModelsList {
  const p = "models.list";
  const o = obj(v, p);
  const tp = `${p}.translation`;
  const t = obj(o.translation, tp);
  const hasServer = t.server !== undefined;
  const sp = hasServer ? `${tp}.server` : tp;
  const server = hasServer ? obj(t.server, sp) : t;

  const presets: TranslationPreset[] = Array.isArray(t.models)
    ? t.models.map((m, i) => parseTranslationModel(m, `${tp}.models[${i}]`))
    : arr(t, "presets", tp).map((m, i) => parsePreset(m, `${tp}.presets[${i}]`));

  let installed: string[] | null = null;
  const installedRaw = t.installed;
  if (Array.isArray(installedRaw)) {
    installed = installedRaw.filter((n): n is string => typeof n === "string");
  } else if (installedRaw !== null && installedRaw !== undefined) {
    throw new ValidationError(`${tp}.installed`, "expected an array or null");
  } else if (Array.isArray(t.models) && server.reachable !== false) {
    installed = presets.filter((m) => m.installed === true).map((m) => m.model);
  }

  return {
    asr: arr(o, "asr", p).map((m, i) => parseAsrModel(m, `${p}.asr[${i}]`)),
    gpu_runtime: parseGpuRuntime(o.gpu_runtime),
    translation: {
      base_url: str(server, "base_url", sp, 2000),
      installed,
      error: optStr(server, "error", sp),
      presets,
    },
  };
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
