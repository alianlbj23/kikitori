/**
 * Typed wrappers around the Tauri commands and events.
 *
 * Role: the only module that calls `invoke` / `listen`. Every result and event
 * payload goes through a validator from `validate.ts` before the UI sees it, and
 * every failure is turned into a Traditional Chinese sentence by `describeError`.
 */

import { invoke } from "@tauri-apps/api/core";
import { listen, type UnlistenFn } from "@tauri-apps/api/event";

import type {
  BridgeStatus,
  DeviceInfo,
  DownloadProgressEvent,
  HardwareReport,
  LogEvent,
  Mode,
  ModelsList,
  OverlaySnapshot,
  OverlayStyle,
  Settings,
  StatusEvent,
  SubtitleEvent,
} from "./types";
import { parseSettingsResult } from "./settings";
import {
  asCommandError,
  parseBridgeStatus,
  parseDevices,
  parseDownloadProgress,
  parseHardware,
  parseLog,
  parseModels,
  parseOverlaySnapshot,
  parseStatus,
  parseSubtitle,
} from "./validate";

export const EVENTS = {
  subtitle: "engine://subtitle",
  status: "engine://status",
  download: "engine://download-progress",
  log: "engine://log",
  bridge: "bridge://state",
  overlay: "overlay://state",
} as const;

export interface SettingsResult {
  settings: Settings;
  warnings: string[];
}

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
  async modelsDownload(model: string): Promise<void> {
    await invoke("models_download", { model });
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
};

const ENGINE_CODES: Record<string, string> = {
  invalid_settings: "設定值不正確",
  settings_io_error: "無法儲存設定檔，請確認磁碟空間與權限",
  session_active: "已經在執行中，請先停止",
  device_not_found: "找不到選擇的音訊裝置，請重新選擇",
  capture_unavailable: "這台電腦無法擷取這種聲音來源",
  capture_error: "擷取聲音時發生錯誤",
  unknown_model: "引擎不認得這個模型",
  download_in_progress: "這個模型已經在下載中",
  unknown_method: "引擎版本與程式不相符，不支援這個操作",
  invalid_params: "傳給引擎的參數不正確",
  invalid_request: "傳給引擎的請求格式不正確",
  internal_error: "引擎內部錯誤",
};

/** User-facing explanation for any error thrown by the wrappers above. */
export function describeError(e: unknown): string {
  const ce = asCommandError(e);
  if (ce) {
    switch (ce.kind) {
      case "engine": {
        const head = ENGINE_CODES[ce.code] ?? `引擎回報錯誤（${ce.code}）`;
        return ce.code === "invalid_settings" ? `${head}：${ce.message}` : `${head}。`;
      }
      case "timeout":
        return `引擎在 ${Math.round(ce.timeout_ms / 1000)} 秒內沒有回應。請稍後再試，或重新啟動引擎。`;
      case "disconnected":
        return "引擎目前沒有執行。請等它重新啟動，或在狀態列按「重新啟動引擎」。";
      case "invalid_input":
        return `輸入的值不正確：${ce.message}`;
    }
  }
  if (e instanceof Error) return `發生錯誤：${e.message}`;
  return `發生錯誤：${String(e)}`;
}
