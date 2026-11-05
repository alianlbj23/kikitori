/**
 * Shared types for data crossing the Rust ↔ webview boundary.
 *
 * Role: the TypeScript mirror of the engine protocol (docs/specs/app-architecture.md,
 * docs/specs/pipeline-engine.md) and of the bridge/overlay payloads defined in
 * app/src-tauri/src. Values of these types only exist after passing the
 * validators in `validate.ts`.
 */

export type Mode = "media" | "interpret";
export type PartnerLanguage = "ja" | "en";

export interface SubtitleEvent {
  utterance_id: number;
  original: string;
  translation: string;
  source_language: string;
  target_language: string;
  is_final: boolean;
  start_sample: number;
  end_sample: number;
  session_id: number | null;
}

export type EngineState = "idle" | "loading" | "running" | "error";

export interface StatusEvent {
  state: EngineState;
  detail: string | null;
  session_id: number | null;
  code: string | null;
}

export type DownloadState = "downloading" | "done" | "error";

export interface DownloadProgressEvent {
  model: string;
  downloaded_bytes: number;
  total_bytes: number;
  state: DownloadState;
  error: string | null;
}

export type LogLevel = "debug" | "info" | "warning" | "error";

export interface LogEvent {
  level: LogLevel;
  message: string;
}

export type BridgePhase = "starting" | "ready" | "restarting" | "stopped" | "failed";

export interface EngineInfo {
  version: string;
  protocol_version: number;
  platform: string;
  fake: boolean;
}

export interface BridgeStatus {
  phase: BridgePhase;
  detail: string | null;
  restarts: number;
  command: string | null;
  source: "env" | "dev_uv" | "sidecar" | null;
  info: EngineInfo | null;
}

export type OverlayPosition = "top" | "bottom";

export interface OverlayStyle {
  font_size: number;
  background_opacity: number;
  position: OverlayPosition;
  max_lines: number;
}

export interface OverlaySnapshot {
  style: OverlayStyle;
  click_through: boolean;
  visible: boolean;
  shortcut: string;
  shortcut_registered: boolean;
}

export interface DeviceInfo {
  id: string;
  name: string;
  kind: "loopback" | "microphone";
  is_default: boolean;
}

export interface GpuInfo {
  name: string;
  vram_bytes: number | null;
}

export interface HardwareInfo {
  os: string;
  os_version: string;
  machine: string;
  cpu_count: number | null;
  ram_bytes: number | null;
  gpus: GpuInfo[];
  apple_silicon: boolean;
}

/** One ASR path of a recommended combination (docs/specs/hardware.md). */
export interface AsrChoice {
  model: string;
  device: string;
  compute_type: string | null;
  /** 0 or null means automatic. */
  cpu_threads: number | null;
}

/** `hardware.detect.recommendation` (hardware.md `ModelCombination.to_dict()`). */
export interface Recommendation {
  fast_asr: AsrChoice | null;
  final_asr: AsrChoice | null;
  /** null when the translation server is not local, so nothing can be recommended. */
  translation_fast: string | null;
  translation_final: string | null;
  gpu_runtime_missing: boolean;
  reasons: string[];
  warnings: string[];
}

export interface HardwareReport {
  hardware: HardwareInfo;
  recommendation: Recommendation | null;
}

export interface AsrModel {
  id: string;
  size_bytes: number;
  license: string;
  speed_tier: string;
  downloaded: boolean;
  /** "fast" / "final" when the hardware recommendation picks this model. */
  recommended_for: string[];
}

/** Optional NVIDIA cuBLAS runtime the engine can download (hardware.md). */
export interface GpuRuntime {
  id: string;
  supported: boolean;
  installed: boolean;
  download_bytes: number | null;
  license: string | null;
  license_url: string | null;
}

export interface TranslationPreset {
  model: string;
  label: string;
  license: string;
  license_url: string | null;
  commercial_use: boolean;
  excluded_regions: string[];
  installed: boolean | null;
}

export interface ModelsList {
  asr: AsrModel[];
  gpu_runtime: GpuRuntime | null;
  translation: {
    base_url: string;
    installed: string[] | null;
    error: string | null;
    presets: TranslationPreset[];
  };
}

/** The subset of the engine settings (pipeline-engine.md "config") the UI edits. */
export interface Settings {
  mode: Mode;
  target_language: string;
  partner_language: PartnerLanguage;
  media_device_id: string | null;
  interpret_device_id: string | null;
  asr: { fast_model: string; final_model: string; device: "auto" | "cpu" | "cuda" };
  /** Whether the engine's settings have per-path compute types (schema v2). */
  asr_has_compute_types: boolean;
  translation: { base_url: string; fast_model: string; final_model: string; api_key_env: string };
  subtitle: OverlayStyle;
}

/** Error object returned by every Tauri command (Rust `RequestError`). */
export type CommandError =
  | { kind: "engine"; code: string; message: string }
  | { kind: "timeout"; method: string; timeout_ms: number }
  | { kind: "disconnected"; detail: string }
  | { kind: "invalid_input"; message: string };
