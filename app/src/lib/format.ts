/**
 * Display formatting for the UI (timecodes, sizes, language names, statuses).
 *
 * Role: keeps every user-facing wording decision for raw protocol values in one
 * place; the words themselves come from the i18n dictionary, language and
 * region names from `Intl.DisplayNames` (`lib/i18n.ts`).
 */

import { getLocale, joinList, languageName, regionName, t, tp, type StringKey } from "./i18n";
import type { BridgeStatus, EngineState } from "./types";

export { languageName } from "./i18n";

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
  if (bytes === null || !Number.isFinite(bytes) || bytes < 0) return t("common.unknown");
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

/**
 * Short tag for the transcript gutter. Chinese and Japanese interfaces use the
 * first character of the language's name (日／英／中), as the design specifies;
 * other interfaces use the uppercase language subtag (JA, EN), since a first
 * letter is ambiguous there.
 */
export function languageShort(code: string): string {
  const primary = code.split("-", 1)[0] ?? "";
  if (!/^[A-Za-z]{2,3}$/.test(primary)) return "?";
  const ui = getLocale();
  if (ui === "zh-TW" || ui === "zh-CN" || ui === "ja") {
    const name = languageName(primary);
    if (name !== primary) return Array.from(name)[0] ?? primary;
  }
  return primary.toUpperCase();
}

/** Value for the HTML `lang` attribute, so CJK text gets the right glyph forms. */
export function htmlLang(code: string): string {
  if (code === "zh" || code === "zh-TW") return "zh-Hant-TW";
  return /^[A-Za-z]{2,3}(-[A-Za-z0-9]{1,8})*$/.test(code) ? code : "";
}

const SPEED_TIER: Readonly<Record<string, StringKey>> = {
  fastest: "speed.fastest",
  fast: "speed.fast",
  medium: "speed.medium",
  slow: "speed.slow",
  slowest: "speed.slowest",
};

export function speedTierLabel(tier: string): string {
  const key = SPEED_TIER[tier];
  return key ? t(key) : tier;
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
  if (!bridge || bridge.phase === "starting") return { label: t("status.starting"), tone: "busy" };
  switch (bridge.phase) {
    case "restarting":
      return { label: t("status.restarting"), tone: "busy" };
    case "failed":
      return { label: t("status.failed"), tone: "bad" };
    case "stopped":
      return { label: t("status.stopped"), tone: "bad" };
    case "ready":
      break;
  }
  switch (session) {
    case "loading":
      return { label: t("status.loading"), tone: "busy" };
    case "running":
      return { label: t("status.running"), tone: "live" };
    case "error":
      return { label: t("status.error"), tone: "bad" };
    case "idle":
      return { label: t("status.idle"), tone: "quiet" };
  }
}

const STATUS_CODE: Readonly<Record<string, StringKey>> = {
  model_not_downloaded: "status.code.model_not_downloaded",
  unknown_model: "status.code.unknown_model",
  device_not_found: "status.code.device_not_found",
  permission_denied: "status.code.permission_denied",
  device_lost: "status.code.device_lost",
  capture_unavailable: "status.code.capture_unavailable",
  capture_error: "status.code.capture_error",
  pipeline_error: "status.code.pipeline_error",
  load_error: "status.code.load_error",
  translation_model_missing: "status.code.translation_model_missing",
  translation_server_auth_failed: "status.code.translation_server_auth_failed",
};

/** Explanation for a `status: error` event, preferring our wording over raw detail. */
export function describeStatusError(code: string | null, detail: string | null): string {
  const known = code ? STATUS_CODE[code] : undefined;
  if (known) return t(known);
  return detail ? t("status.errorDetail", { detail }) : t("status.errorUnknown");
}

/** Memory reported in MiB (nvidia-smi, model tables) as GB with one decimal. */
export function formatMiB(mb: number | null): string {
  if (mb === null || !Number.isFinite(mb) || mb < 0) return t("common.unknown");
  if (mb < 1024) return `${Math.round(mb)} MB`;
  return `${(mb / 1024).toFixed(1)} GB`;
}

const TIER: Readonly<Record<string, StringKey>> = {
  nvidia_8gb: "tier.nvidia_8gb",
  nvidia_4gb: "tier.nvidia_4gb",
  nvidia_lt4gb: "tier.nvidia_lt4gb",
  cpu: "tier.cpu",
  cpu_low: "tier.cpu_low",
  apple_silicon: "tier.apple_silicon",
};

/** Hardware tier id from the recommendation (hardware.md), in plain words. */
export function tierLabel(tier: string): string {
  const key = TIER[tier];
  return key ? t(key) : tier;
}

export function deviceLabel(device: string): string {
  if (device === "cuda") return "NVIDIA GPU";
  if (device === "cpu") return "CPU";
  if (device === "auto") return t("common.auto");
  return device;
}

/** Thread count of a recommendation or setting; 0 / null mean automatic. */
export function threadsLabel(threads: number | null): string {
  return threads ? tp("threads.count", threads) : t("threads.auto");
}

export function computeTypeLabel(computeType: string | null): string {
  return computeType ?? t("computeType.auto");
}

/** License-excluded region codes as names, e.g. ["EU", "KR"] → "歐盟、南韓". */
export function regionNames(codes: readonly string[]): string {
  return joinList(codes.map(regionName));
}
