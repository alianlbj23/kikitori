/**
 * Typed wrappers around the Tauri commands and events.
 *
 * Role: the only module that calls `invoke` / `listen`. Every result and event
 * payload goes through a validator from `validate.ts` before the UI sees it, and
 * every failure is turned into a sentence in the interface language by `describeError`.
 */

import { Channel, invoke } from "@tauri-apps/api/core";
import { listen, type UnlistenFn } from "@tauri-apps/api/event";
import { openUrl } from "@tauri-apps/plugin-opener";

import { NVIDIA_EULA_URL } from "./combination";
import { t, tp, type StringKey } from "./i18n";
import { OLLAMA_DOWNLOAD_URL } from "./translation-guide";
import type {
  AboutInfo,
  AppliedRecommendation,
  BridgeStatus,
  CombinationCheck,
  DeviceInfo,
  DownloadProgressEvent,
  HardwareReport,
  LanguageChoice,
  LanguageInfo,
  LanguagePreview,
  LicenseFolder,
  LogEvent,
  Mode,
  ModelsList,
  ObsContent,
  ObsStatus,
  OutputFolder,
  OutputInfo,
  OverlaySnapshot,
  OverlayStyle,
  RecommendationPreview,
  ShortcutAction,
  StatusEvent,
  SubtitleEvent,
  UpdateDownloadEvent,
  UpdateInfo,
} from "./types";
import { parseLanguageList, parseLanguagePreview } from "./languages";
import { parseAppliedRecommendation, parseSettingsResult, type SettingsResult } from "./settings";
import {
  asCommandError,
  parseAbout,
  parseBridgeStatus,
  parseCombinationCheck,
  parseDevices,
  parseDownloadProgress,
  parseHardware,
  parseLog,
  parseModels,
  parseObsStatus,
  parseOutputInfo,
  parseOverlaySnapshot,
  parseRecommendationPreview,
  parseSavedPath,
  parseShortcutAction,
  parseStatus,
  parseSubtitle,
  parseUpdateDownloadEvent,
  parseUpdateInfo,
} from "./validate";

export const EVENTS = {
  subtitle: "engine://subtitle",
  status: "engine://status",
  download: "engine://download-progress",
  log: "engine://log",
  bridge: "bridge://state",
  overlay: "overlay://state",
  /** A global shortcut the main window handles (`toggle_translation`). */
  shortcut: "shortcut://action",
  /** The "clear subtitles" shortcut: the overlay empties its lines. */
  overlayClear: "overlay://clear",
} as const;

export const api = {
  async bridgeStatus(): Promise<BridgeStatus> {
    return parseBridgeStatus(await invoke("bridge_status"));
  },
  async restartEngine(): Promise<BridgeStatus> {
    return parseBridgeStatus(await invoke("engine_restart"));
  },
  async hardwareDetect(): Promise<HardwareReport> {
    return parseHardware(await invoke("hardware_detect"));
  },
  async modelsList(): Promise<ModelsList> {
    return parseModels(await invoke("models_list"));
  },
  /**
   * Starts a download. `acceptLicense` is only for the GPU runtime and must
   * come from the user ticking the NVIDIA CUDA EULA box.
   */
  async modelsDownload(model: string, acceptLicense = false): Promise<void> {
    await invoke("models_download", acceptLicense ? { model, acceptLicense } : { model });
  },
  /** Checks partial settings against this machine without saving them. */
  async modelsValidate(settings: Record<string, unknown> | null): Promise<CombinationCheck> {
    return parseCombinationCheck(await invoke("models_validate", settings ? { settings } : {}));
  },
  /**
   * Writes the engine's recommendation into the settings (the engine owns the
   * rules). `onlyIfNew` is the first-run auto-apply: the engine writes nothing
   * (`applied: null`) if settings were saved in the meantime.
   */
  async applyRecommendation(onlyIfNew = false): Promise<AppliedRecommendation> {
    return parseAppliedRecommendation(
      await invoke("settings_apply_recommendation", onlyIfNew ? { onlyIfNew: true } : {}),
    );
  },
  /**
   * What `applyRecommendation` would write and whether it differs from the
   * saved settings, without saving: the UI never compares settings itself.
   */
  async previewRecommendation(): Promise<RecommendationPreview> {
    return parseRecommendationPreview(await invoke("settings_preview_recommendation"));
  },
  /**
   * Downloads a translation model on the user's Ollama server. Rejects with
   * `pull_unsupported` (not Ollama), `translation_server_unreachable` or
   * `translation_server_auth_failed`; progress arrives as `download_progress`
   * events with `kind: "translation"`.
   */
  async modelsPullTranslation(model: string): Promise<void> {
    await invoke("models_pull_translation", { model });
  },
  /** The engine's listening / subtitle language list (the UI keeps no copy). */
  async languagesList(): Promise<LanguageInfo[]> {
    return parseLanguageList(await invoke("languages_list"));
  },
  /** The models a language combination would use and what is not installed yet (nothing is saved). */
  async previewLanguages(languages: Partial<LanguageChoice>): Promise<LanguagePreview> {
    return parseLanguagePreview(await invoke("settings_preview_languages", { languages }));
  },
  async devicesList(): Promise<DeviceInfo[]> {
    return parseDevices(await invoke("devices_list"));
  },
  async settingsGet(): Promise<SettingsResult> {
    return parseSettingsResult(await invoke("settings_get"));
  },
  async settingsSet(patch: Record<string, unknown>): Promise<SettingsResult> {
    return parseSettingsResult(await invoke("settings_set", { patch }));
  },
  async sessionStart(mode: Mode, deviceId: string | null): Promise<number | null> {
    const result = (await invoke("session_start", { mode, deviceId })) as Record<string, unknown>;
    const id = result?.session_id;
    return typeof id === "number" && Number.isSafeInteger(id) ? id : null;
  },
  async sessionStop(): Promise<void> {
    await invoke("session_stop");
  },
  /** "顯示翻譯": saves `translation.enabled` and switches a running session at once. */
  async sessionSetTranslation(enabled: boolean): Promise<SettingsResult> {
    return parseSettingsResult(await invoke("session_set_translation", { enabled }));
  },
  async about(): Promise<AboutInfo> {
    return parseAbout(await invoke("app_about"));
  },
  /** Opens a bundled license folder; Rust computes the path from the name. */
  async openThirdPartyLicenses(folder: LicenseFolder): Promise<void> {
    await invoke("open_third_party_licenses", { folder });
  },
  /**
   * Checks `latest.json` for a newer version. Goes through Rust (not the
   * updater plugin's JavaScript API) so installing can stop the engine first.
   */
  async updateCheck(timeoutMs: number): Promise<UpdateInfo | null> {
    return parseUpdateInfo(await invoke("update_check", { timeoutMs }));
  },
  /**
   * Downloads, verifies and installs the update found by `updateCheck`. On
   * Windows the app exits during this call (the engine is shut down first).
   */
  async updateInstall(onEvent: (event: UpdateDownloadEvent) => void, timeoutMs: number): Promise<void> {
    const channel = new Channel<unknown>();
    channel.onmessage = (message) => {
      let event: UpdateDownloadEvent;
      try {
        event = parseUpdateDownloadEvent(message);
      } catch (e) {
        console.error("dropping malformed update progress:", e);
        return;
      }
      onEvent(event);
    };
    await invoke("update_install", { onEvent: channel, timeoutMs });
  },
  /** Opens the NVIDIA CUDA EULA (one of the two URLs the opener permission allows). */
  async openNvidiaEula(): Promise<void> {
    await openUrl(NVIDIA_EULA_URL);
  },
  /** Opens Ollama's official download page (the other allowed URL). */
  async openOllamaDownload(): Promise<void> {
    await openUrl(OLLAMA_DOWNLOAD_URL);
  },
  /** Where transcripts and the OBS text file go. */
  async outputInfo(): Promise<OutputInfo> {
    return parseOutputInfo(await invoke("output_info"));
  },
  /**
   * Saves a transcript into `<Documents>/kikitori/transcripts/`; Rust validates
   * the name and picks the folder. Resolves to the saved file's full path.
   */
  async transcriptSave(fileName: string, contents: string): Promise<string> {
    return parseSavedPath(await invoke("transcript_save", { fileName, contents }));
  },
  /** Opens the transcripts or OBS folder; Rust computes the path from the name. */
  async openOutputFolder(folder: OutputFolder): Promise<void> {
    await invoke("open_output_folder", { folder });
  },
  /** Turns the OBS text file on or off and chooses what it holds. */
  async obsSetOutput(content: ObsContent): Promise<ObsStatus> {
    return parseObsStatus(await invoke("obs_set_output", { content }));
  },
  async overlayState(): Promise<OverlaySnapshot> {
    return parseOverlaySnapshot(await invoke("overlay_get_state"));
  },
  async overlaySetStyle(style: OverlayStyle): Promise<OverlaySnapshot> {
    return parseOverlaySnapshot(await invoke("overlay_set_style", { style }));
  },
  async overlaySetClickThrough(enabled: boolean): Promise<OverlaySnapshot> {
    return parseOverlaySnapshot(await invoke("overlay_set_click_through", { enabled }));
  },
  async overlaySetVisible(visible: boolean): Promise<OverlaySnapshot> {
    return parseOverlaySnapshot(await invoke("overlay_set_visible", { visible }));
  },
  /** Tells the overlay window which interface language to use. */
  async overlaySetLocale(locale: string): Promise<OverlaySnapshot> {
    return parseOverlaySnapshot(await invoke("overlay_set_locale", { locale }));
  },
  /** Tells the overlay whether translations are shown (originals are promoted when not). */
  async overlaySetShowTranslation(enabled: boolean): Promise<OverlaySnapshot> {
    return parseOverlaySnapshot(await invoke("overlay_set_show_translation", { enabled }));
  },
};

/**
 * Subscribes to an event, validating each payload. Invalid payloads are logged
 * and dropped: one bad event must not stop the subtitle stream.
 */
async function on<T>(name: string, parse: (v: unknown) => T, handler: (v: T) => void): Promise<UnlistenFn> {
  return listen<unknown>(name, (event) => {
    let value: T;
    try {
      value = parse(event.payload);
    } catch (e) {
      console.error(`dropping invalid ${name} payload:`, e);
      return;
    }
    handler(value);
  });
}

export const events = {
  subtitle: (h: (v: SubtitleEvent) => void) => on(EVENTS.subtitle, parseSubtitle, h),
  status: (h: (v: StatusEvent) => void) => on(EVENTS.status, parseStatus, h),
  download: (h: (v: DownloadProgressEvent) => void) => on(EVENTS.download, parseDownloadProgress, h),
  log: (h: (v: LogEvent) => void) => on(EVENTS.log, parseLog, h),
  bridge: (h: (v: BridgeStatus) => void) => on(EVENTS.bridge, parseBridgeStatus, h),
  overlay: (h: (v: OverlaySnapshot) => void) => on(EVENTS.overlay, parseOverlaySnapshot, h),
  shortcut: (h: (v: ShortcutAction) => void) => on(EVENTS.shortcut, parseShortcutAction, h),
  overlayClear: (h: () => void) => on(EVENTS.overlayClear, () => undefined, () => h()),
};

const ENGINE_CODES: Readonly<Record<string, StringKey>> = {
  invalid_settings: "error.code.invalid_settings",
  settings_io_error: "error.code.settings_io_error",
  session_active: "error.code.session_active",
  device_not_found: "error.code.device_not_found",
  capture_unavailable: "error.code.capture_unavailable",
  capture_error: "error.code.capture_error",
  unknown_model: "error.code.unknown_model",
  download_in_progress: "error.code.download_in_progress",
  license_not_accepted: "error.code.license_not_accepted",
  gpu_runtime_unsupported: "error.code.gpu_runtime_unsupported",
  pull_unsupported: "error.code.pull_unsupported",
  translation_server_unreachable: "error.code.translation_server_unreachable",
  translation_server_auth_failed: "error.code.translation_server_auth_failed",
  unknown_method: "error.code.unknown_method",
  invalid_params: "error.code.invalid_params",
  invalid_request: "error.code.invalid_request",
  internal_error: "error.code.internal_error",
};

/** User-facing explanation for any error thrown by the wrappers above. */
export function describeError(e: unknown): string {
  const ce = asCommandError(e);
  if (ce) {
    switch (ce.kind) {
      case "engine": {
        const key = ENGINE_CODES[ce.code];
        const head = key ? t(key) : t("error.code.other", { code: ce.code });
        return ce.code === "invalid_settings"
          ? t("error.engineDetail", { head, message: ce.message })
          : t("error.engineSentence", { head });
      }
      case "timeout":
        return tp("error.timeout", Math.round(ce.timeout_ms / 1000));
      case "disconnected":
        return t("error.disconnected");
      case "invalid_input":
        return t("error.invalidInput", { message: ce.message });
    }
  }
  if (e instanceof Error) return t("error.generic", { message: e.message });
  return t("error.generic", { message: String(e) });
}
