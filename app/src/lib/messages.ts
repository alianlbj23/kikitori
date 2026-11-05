/**
 * Human-readable text that comes from the engine.
 *
 * Role: the engine explains recommendations and checks (`reasons`, `warnings`,
 * `models.validate` errors) either as plain strings (older engines: Traditional
 * Chinese) or as `{code, params, message}` objects. The UI translates a known
 * `code` with its own dictionary (`engine.<code>`) and otherwise shows the
 * engine's English `message`, so a newer engine never shows a blank line.
 */

import { joinList, languageName, regionName, t, tDynamic, type MessageParams } from "./i18n";

export interface CodedEngineMessage {
  /** Stable id, e.g. `gpu_runtime_missing`; looked up as `engine.<code>`. */
  code: string;
  /** Values for the dictionary placeholders. */
  params: Readonly<Record<string, string | number>>;
  /** English fallback written by the engine. */
  message: string;
}

/** A string (legacy, shown as-is) or a coded message. */
export type EngineMessage = string | CodedEngineMessage;

const CODE = /^[a-z0-9_.]{1,100}$/;
const MAX_TEXT = 2000;
const MAX_PARAMS = 20;

/**
 * Validates one engine message from an untrusted payload; null when it is
 * neither a bounded string nor a well-formed `{code, params?, message}` object.
 * Params keep only string and finite-number values (others are dropped): they
 * are only ever substituted into text.
 */
export function parseEngineMessage(v: unknown): EngineMessage | null {
  if (typeof v === "string") return v.length <= MAX_TEXT ? v : null;
  if (typeof v !== "object" || v === null || Array.isArray(v)) return null;
  const o = v as Record<string, unknown>;
  if (typeof o.code !== "string" || !CODE.test(o.code)) return null;
  if (typeof o.message !== "string" || o.message.length > MAX_TEXT) return null;
  const params: Record<string, string | number> = {};
  if (typeof o.params === "object" && o.params !== null && !Array.isArray(o.params)) {
    for (const [k, p] of Object.entries(o.params).slice(0, MAX_PARAMS)) {
      if ((typeof p === "string" && p.length <= MAX_TEXT) || (typeof p === "number" && Number.isFinite(p))) {
        params[k] = p;
      }
    }
  }
  return { code: o.code, params, message: o.message };
}

/** Validates a list of engine messages, dropping malformed items (at most `maxItems`). */
export function parseEngineMessages(v: unknown, maxItems = 50): EngineMessage[] {
  if (!Array.isArray(v)) return [];
  return v
    .map(parseEngineMessage)
    .filter((m): m is EngineMessage => m !== null)
    .slice(0, maxItems);
}

export type Translate = (key: string, params: MessageParams) => string | null;

const LANGUAGE_PARAMS = new Set(["language", "source_language", "target_language"]);

/**
 * One parameter as the sentence should show it (hardware.md「訊息碼」): language
 * codes become language names (`auto` → "detect automatically"), the ASR path
 * and translation field ids become the labels the models page uses, region
 * codes become names, and `*_gb` amounts get one decimal like the English
 * template (`{ram_gb:.1f}`). Model and GPU names stay as they are.
 */
export function localizeParam(name: string, value: string | number): string | number {
  if (typeof value === "number") {
    return name.endsWith("_gb") ? value.toFixed(1) : value;
  }
  if (LANGUAGE_PARAMS.has(name)) return value === "auto" ? t("language.auto") : languageName(value);
  if (name === "path" && value === "fast_asr") return t("engineParam.fast_asr");
  if (name === "path" && value === "final_asr") return t("engineParam.final_asr");
  if (name === "field" && value === "translation_fast") return t("engineParam.translation_fast");
  if (name === "field" && value === "translation_final") return t("engineParam.translation_final");
  if (name === "regions") {
    return joinList(
      value
        .split(",")
        .map((r) => r.trim())
        .filter((r) => r !== "")
        .map(regionName),
    );
  }
  return value;
}

/**
 * The text to show for an engine message in the interface language. `translate`
 * is the dictionary lookup (replaceable in tests); parameters are localized
 * first (`localizeParam`). Unknown codes show the engine's English `message`.
 */
export function formatEngineMessage(m: EngineMessage, translate: Translate = tDynamic): string {
  if (typeof m === "string") return m;
  const params: Record<string, string | number> = {};
  for (const [name, value] of Object.entries(m.params)) params[name] = localizeParam(name, value);
  return translate(`engine.${m.code}`, params) ?? m.message;
}
