/**
 * Display formatting for the UI (timecodes, sizes, language names, statuses).
 *
 * Role: keeps every user-facing wording decision for raw protocol values in one
 * place, in Traditional Chinese.
 */

import type { BridgeStatus, EngineState } from "./types";

export const SAMPLE_RATE = 16_000;

/** Stream position as `m:ss` (or `h:mm:ss` past an hour). */
export function formatTimecode(samples: number, rate = SAMPLE_RATE): string {
  const total = Math.max(0, Math.floor(samples / rate));
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  const ss = String(s).padStart(2, "0");
  return h > 0 ? `${h}:${String(m).padStart(2, "0")}:${ss}` : `${m}:${ss}`;
}

export function formatBytes(bytes: number | null): string {
  if (bytes === null || !Number.isFinite(bytes) || bytes < 0) return "未知";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let value = bytes;
  let unit = 0;
  while (value >= 1000 && unit < units.length - 1) {
    value /= 1000;
    unit++;
  }
  const digits = unit === 0 || value >= 100 ? 0 : 1;
  return `${value.toFixed(digits)} ${units[unit]}`;
}

export function percent(done: number, total: number): number {
  if (total <= 0) return 0;
  return Math.min(100, Math.max(0, Math.floor((done / total) * 100)));
}

const LANGUAGE_SHORT: Record<string, string> = { ja: "日", en: "英", zh: "中", "zh-TW": "中" };
const LANGUAGE_NAME: Record<string, string> = {
  ja: "日語",
  en: "英語",
  zh: "中文",
  "zh-TW": "繁體中文",
};

/** One-character tag used in the transcript gutter. */
export function languageShort(code: string): string {
  return LANGUAGE_SHORT[code] ?? (code.slice(0, 2) || "？");
}

export function languageName(code: string): string {
  return LANGUAGE_NAME[code] ?? code;
}

/** Value for the HTML `lang` attribute, so CJK text gets the right glyph forms. */
export function htmlLang(code: string): string {
  if (code === "zh" || code === "zh-TW") return "zh-Hant-TW";
  return /^[A-Za-z]{2,3}(-[A-Za-z0-9]{1,8})*$/.test(code) ? code : "";
}

const SPEED_TIER: Record<string, string> = {
  fastest: "最快",
  fast: "快",
  medium: "中等",
  slow: "慢",
  slowest: "最慢",
};

export function speedTierLabel(tier: string): string {
  return SPEED_TIER[tier] ?? tier;
}

const OS_NAME: Record<string, string> = { windows: "Windows", macos: "macOS", linux: "Linux" };

export function osName(os: string): string {
  return OS_NAME[os] ?? os;
}

export type Tone = "quiet" | "busy" | "live" | "bad";

export interface StatusView {
  label: string;
  tone: Tone;
}

/**
 * What the status line says, combining the engine process (bridge) state and
 * the session state reported by the engine.
 */
export function describeStatus(bridge: BridgeStatus | null, session: EngineState): StatusView {
  if (!bridge || bridge.phase === "starting") return { label: "正在啟動引擎", tone: "busy" };
  switch (bridge.phase) {
    case "restarting":
      return { label: "引擎意外結束，正在重新啟動", tone: "busy" };
    case "failed":
      return { label: "引擎無法啟動", tone: "bad" };
    case "stopped":
      return { label: "引擎已停止", tone: "bad" };
    case "ready":
      break;
  }
  switch (session) {
    case "loading":
      return { label: "正在載入模型", tone: "busy" };
    case "running":
      return { label: "正在聆聽", tone: "live" };
    case "error":
      return { label: "發生錯誤，已停止", tone: "bad" };
    case "idle":
      return { label: "待命中", tone: "quiet" };
  }
}

const STATUS_CODE: Record<string, string> = {
  model_not_downloaded: "模型還沒下載。請到「模型」頁下載後再開始。",
  unknown_model: "設定裡的模型不存在。請到「模型」頁重新選擇。",
  device_not_found: "找不到選擇的音訊裝置。請重新選擇裝置。",
  permission_denied: "沒有使用麥克風或錄音的權限。請在系統設定中允許 kikitori。",
  device_lost: "音訊裝置中斷了。請確認裝置連線後重新開始。",
  capture_unavailable: "這台電腦無法擷取這種聲音來源。",
  capture_error: "擷取聲音時發生錯誤。",
  pipeline_error: "語音處理發生錯誤。",
  load_error: "載入模型失敗。",
};

/** Explanation for a `status: error` event, preferring our wording over raw detail. */
export function describeStatusError(code: string | null, detail: string | null): string {
  const known = code ? STATUS_CODE[code] : undefined;
  if (known) return known;
  return detail ? `發生錯誤：${detail}` : "發生未知的錯誤。";
}
