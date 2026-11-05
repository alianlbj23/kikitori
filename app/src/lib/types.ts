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

/** `hardware.gpus[]` (docs/specs/hardware.md `GpuInfo`). Memory is in MiB. */
export interface GpuInfo {
  index: number;
  name: string;
  memory_total_mb: number;
  memory_used_mb: number | null;
  driver_version: string | null;
  compute_capability: string | null;
}

export interface CpuInfo {
  model: string | null;
  physical_cores: number | null;
  logical_cores: number | null;
  architecture: string | null;
}

/** cuBLAS 12 availability for GPU speech recognition. */
export interface CublasInfo {
  supported_platform: boolean;
  available: boolean;
  library_dirs: string[];
  /** Where it was found (e.g. the downloaded GPU runtime); null when missing. */
  source: string | null;
}

export interface TranslationServerInfo {
  base_url: string;
  is_local: boolean;
  reachable: boolean;
  /** Model names the server reported; null when it could not be asked. */
  models: string[] | null;
  error: string | null;
}

/** `hardware.detect.hardware` (`HardwareInfo.to_dict()`). */
export interface HardwareInfo {
  os: string;
  os_version: string;
  machine: string;
  cpu: CpuInfo;
  cpu_count: number | null;
  memory: { total_bytes: number | null; available_bytes: number | null };
  gpus: GpuInfo[];
  apple_silicon: boolean;
  cublas: CublasInfo;
  translation_server: TranslationServerInfo | null;
  /** Probes that failed; detection still returns what it could find. */
  errors: string[];
}

export type AsrDevice = "auto" | "cpu" | "cuda";

/** One ASR path of a recommended combination (docs/specs/hardware.md). */
export interface AsrChoice {
  model: string;
  device: string;
  compute_type: string | null;
  /** 0 means automatic (always 0 on CUDA). */
  cpu_threads: number;
}

/** `ModelCombination.to_dict()`: the recommendation for this machine. */
export interface Recommendation {
  tier: string;
  fast_asr: AsrChoice;
  final_asr: AsrChoice;
  /** null when the translation server is not local, so nothing can be recommended. */
  translation_fast: string | null;
  translation_final: string | null;
  /** GB of (V)RAM left for the translation model; null when unknown. */
  translation_budget_gb: number | null;
  gpu_runtime_missing: boolean;
  reasons: string[];
  warnings: string[];
}

export interface HardwareReport {
  hardware: HardwareInfo;
  recommendation: Recommendation;
}

export type ModelRole = "fast" | "final";

export interface AsrModel {
  id: string;
  size_bytes: number;
  downloaded: boolean;
  license: string;
  speed_tier: string;
  /** Whether the model's no_speech probability can filter non-speech; null = unknown. */
  no_speech_reliable: boolean | null;
  cpu_ram_mb: number | null;
  vram_mb: number | null;
  vram_int8_mb: number | null;
  /** Paths the hardware recommendation picks this model for. */
  recommended_for: ModelRole[];
}

/** Optional NVIDIA cuBLAS runtime the engine can download (hardware.md). */
export interface GpuRuntime {
  id: string;
  supported: boolean;
  installed: boolean;
  package: string | null;
  version: string | null;
  path: string | null;
  download_bytes: number | null;
  installed_bytes: number | null;
  license: string | null;
  license_url: string | null;
  cublas_available: boolean;
  cublas_source: string | null;
}

/** `models.list.translation.models[]`: presets plus whatever the server has. */
export interface TranslationModel {
  name: string;
  label: string;
  /** null when the server could not be asked. */
  installed: boolean | null;
  /** false for models the engine has no license information about. */
  known: boolean;
  recommended_for: ModelRole[];
  license: string | null;
  license_url: string | null;
  commercial_use: boolean | null;
  /** Region codes the license does not cover (e.g. EU, UK, KR). */
  excluded_regions: string[];
  approx_vram_gb: number | null;
}

export interface ModelsList {
  asr: AsrModel[];
  gpu_runtime: GpuRuntime;
  translation: {
    server: { base_url: string; is_local: boolean; reachable: boolean; error: string | null };
    models: TranslationModel[];
  };
}

/** `models.validate` result. Messages are already in Traditional Chinese. */
export interface CombinationCheck {
  ok: boolean;
  errors: string[];
  warnings: string[];
}

export type ComputeType =
  | "int8"
  | "int8_float32"
  | "int8_float16"
  | "int8_bfloat16"
  | "int16"
  | "float16"
  | "bfloat16"
  | "float32";

/** The subset of the engine settings (pipeline-engine.md "config", schema 2) the UI edits. */
export interface Settings {
  mode: Mode;
  target_language: string;
  partner_language: PartnerLanguage;
  media_device_id: string | null;
  interpret_device_id: string | null;
  asr: {
    fast_model: string;
    final_model: string;
    /** Shared by both paths: the ASR layer stops trying CUDA after one failure. */
    device: AsrDevice;
    /** null = the default for the device actually used (CPU int8, CUDA float16). */
    fast_compute_type: ComputeType | null;
    final_compute_type: ComputeType | null;
    /** null = automatic. */
    fast_cpu_threads: number | null;
    final_cpu_threads: number | null;
    cuda_library_dirs: string[];
  };
  translation: {
    base_url: string;
    fast_model: string;
    final_model: string;
    api_key_env: string;
    /** User confirmed they are outside the EU, UK and South Korea. */
    allow_restricted: boolean;
  };
  subtitle: OverlayStyle;
  /** The UI's own state in the engine's opaque `ui` object (the engine never reads it). */
  ui: UiState;
}

export interface UiState {
  /**
   * Fingerprints of recommendations the user answered with "don't remind me"
   * (`settings.preview_recommendation`); a different recommendation prompts again.
   */
  dismissed_recommendations: string[];
}

/** `settings.preview_recommendation`: what applying would write, without saving. */
export interface RecommendationPreview {
  /** Exactly the partial settings `settings.apply_recommendation` would write. */
  patch: Record<string, unknown>;
  recommendation: Recommendation;
  /** Saving the patch would change the current settings. */
  differs: boolean;
  /** Stable id of the patch, remembered when the user dismisses the offer. */
  fingerprint: string;
}

/** `settings.apply_recommendation` result. */
export interface AppliedRecommendation {
  settings: Settings;
  path: string | null;
  /** Settings fields that were malformed and replaced by defaults. */
  warnings: string[];
  /** Always false after a save; kept so the shape matches `settings.get`. */
  isNew: boolean;
  /**
   * The partial settings the engine actually wrote; null when `only_if_new`
   * was sent and settings had already been saved (nothing written).
   */
  applied: Record<string, unknown> | null;
  recommendation: Recommendation;
}

/** `app_about`: license facts for the settings page. */
export interface AboutInfo {
  license: string;
  /** kikitori's LICENSE plus Rust/npm notices (`<resources>/licenses`); null in development builds. */
  app_licenses: string | null;
  /** Bundled engine's third-party license folder; null in development builds. */
  engine_licenses: string | null;
}

/** `update_check` result: a newer version announced by `latest.json`. */
export interface UpdateInfo {
  version: string;
  currentVersion: string;
  notes: string | null;
}

/** Progress reported by `update_install` (same events as the updater plugin's). */
export type UpdateDownloadEvent =
  | { event: "Started"; contentLength: number | null }
  | { event: "Progress"; chunkLength: number }
  | { event: "Finished" };

/** Argument of `open_third_party_licenses`: which bundled folder to open. */
export type LicenseFolder = "app" | "engine";

/** Error object returned by every Tauri command (Rust `RequestError`). */
export type CommandError =
  | { kind: "engine"; code: string; message: string }
  | { kind: "timeout"; method: string; timeout_ms: number }
  | { kind: "disconnected"; detail: string }
  | { kind: "invalid_input"; message: string };
