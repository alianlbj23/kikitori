/**
 * Shared types for data crossing the Rust ↔ webview boundary.
 *
 * Role: the TypeScript mirror of the engine protocol (docs/specs/app-architecture.md,
 * docs/specs/pipeline-engine.md) and of the bridge/overlay payloads defined in
 * app/src-tauri/src. Values of these types only exist after passing the
 * validators in `validate.ts`.
 */

import type { UiLocalePreference } from "./i18n";
import type { EngineMessage } from "./messages";

export type Mode = "media" | "interpret";

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
  /**
   * Translation models the session needs but the Ollama server lacks
   * (`code: "translation_model_missing"`, `data.models`); empty otherwise.
   */
  missing_translation_models: string[];
}

export type DownloadState = "downloading" | "done" | "error";

/** What a `download_progress` event is about; null from engines older than Milestone 13. */
export type DownloadKind = "asr" | "gpu_runtime" | "translation";

export interface DownloadProgressEvent {
  model: string;
  /** ASR and translation models may share a name, so the kind tells them apart. */
  kind: DownloadKind | null;
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
  /** A line stays at least this long before a newer one pushes it out (0 = never wait). */
  min_display_ms: number;
  /** Seconds after the last change before the overlay fades out; 0 = never. */
  idle_clear_secs: number;
}

/** What a global shortcut does (Rust `ShortcutAction`). */
export type ShortcutAction = "click_through" | "toggle_translation" | "clear_subtitles";

export interface ShortcutInfo {
  action: ShortcutAction;
  keys: string;
  /** False when another app owns the combination. */
  registered: boolean;
}

/** Which text the OBS file holds (`settings.subtitle.obs_output`). */
export type ObsContent = "off" | "original" | "translation" | "both";

/** `obs_set_output` result. */
export interface ObsStatus {
  content: ObsContent;
  /** Full path of the text file; null when the Documents folder is unknown. */
  path: string | null;
  error: string | null;
}

/** `output_info`: where transcripts and the OBS text go (null without a Documents folder). */
export interface OutputInfo {
  transcripts_dir: string | null;
  obs_file: string | null;
}

/** Argument of `open_output_folder`. */
export type OutputFolder = "transcripts" | "obs";

export interface OverlaySnapshot {
  style: OverlayStyle;
  click_through: boolean;
  visible: boolean;
  shortcut: string;
  shortcut_registered: boolean;
  /** UI locale the main window chose; null until it has sent one (follow the OS). */
  locale: string | null;
  /** Settings `translation.enabled`; false = show originals in the primary style. */
  show_translation: boolean;
  /** Every global shortcut; older Rust builds list only `shortcut` (click-through). */
  shortcuts: ShortcutInfo[];
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
  /** Plain strings from older engines, `{code, params, message}` from newer ones. */
  reasons: EngineMessage[];
  warnings: EngineMessage[];
  /**
   * Why translation models are (not) recommended: `recommended`, `remote`
   * (server not on this computer), `not_needed` (same spoken language) or
   * `unsupported` (no model supports the pair); null from older engines.
   */
  translation_status: TranslationStatus | null;
}

export type TranslationStatus = "recommended" | "remote" | "not_needed" | "unsupported";

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

/** `models.validate` result; messages are shown with `formatEngineMessage`. */
export interface CombinationCheck {
  ok: boolean;
  errors: EngineMessage[];
  warnings: EngineMessage[];
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

/** The subset of the engine settings (pipeline-engine.md "config", schema 3) the UI edits. */
export interface Settings {
  mode: Mode;
  /** Media mode's listening language (language list code) or "auto". */
  source_language: string;
  /** Media mode's subtitle language; in interpret mode, "my language". */
  target_language: string;
  /** Interpret mode's other language. */
  partner_language: string;
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
    /** false = original only: the engine translates nothing. */
    enabled: boolean;
    base_url: string;
    fast_model: string;
    final_model: string;
    api_key_env: string;
    /** User confirmed they are outside the EU, UK and South Korea. */
    allow_restricted: boolean;
  };
  subtitle: OverlayStyle;
  /** `subtitle.obs_output`: the OBS text file (stored next to the style, but not sent as style). */
  obs_output: ObsContent;
  /** The UI's own state in the engine's opaque `ui` object (the engine never reads it). */
  ui: UiState;
}

export interface UiState {
  /**
   * Fingerprints of recommendations the user answered with "don't remind me"
   * (`settings.preview_recommendation`); a different recommendation prompts again.
   */
  dismissed_recommendations: string[];
  /** Interface language (`ui.locale`); "system" follows the OS. */
  locale: UiLocalePreference;
  /** Save SRT and TXT of every session when it ends (`ui.auto_save_transcripts`). */
  auto_save_transcripts: boolean;
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

/** One entry of `languages.list` (the engine's language catalog). */
export interface LanguageInfo {
  /** Language list code used in settings and subtitles (e.g. `zh-TW`). */
  code: string;
  english_name: string;
  native_name: string;
  /** Whisper's code; zh-TW and zh-CN are both `zh`. */
  whisper_code: string;
  /** Written right to left (Arabic). */
  rtl: boolean;
  /** Written without spaces between words (ja, zh, th). */
  no_space: boolean;
}

/** A language combination as `settings.preview_languages` normalized it. */
export interface LanguageChoice {
  mode: Mode;
  source_language: string;
  target_language: string;
  partner_language: string;
}

/** One model of a previewed combination. */
export interface PreviewModel {
  model: string;
  roles: ModelRole[];
  /** null when the translation server could not be asked. */
  installed: boolean | null;
  size_bytes: number | null;
  /** Bytes to download before first use; 0 when installed. */
  download_bytes: number | null;
  /** Translation sizes are the preset's weight size, so only approximate. */
  size_approximate: boolean;
}

/** `settings.preview_languages`: what choosing these languages would use (nothing is saved). */
export interface LanguagePreview {
  languages: LanguageChoice;
  recommendation: Recommendation;
  /** Model settings that applying the recommendation would write. */
  patch: Record<string, unknown>;
  asr: PreviewModel[];
  translation: PreviewModel[];
  /** false when only originals are shown or both languages are the same spoken language. */
  translation_needed: boolean;
  download_bytes: number;
  download_size_known: boolean;
  /** Notes about the preview itself (e.g. `translation_disabled`). */
  messages: EngineMessage[];
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
