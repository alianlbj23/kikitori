/**
 * Human-readable text that comes from the engine.
 *
 * Role: the engine explains recommendations and checks (`reasons`, `warnings`,
 * `models.validate` errors) either as plain strings (older engines: Traditional
 * Chinese) or as `{code, params, message}` objects. The UI translates a known
 * `code` with its own dictionary (`engine.<code>`) and otherwise shows the
 * engine's English `message`, so a newer engine never shows a blank line.
 */

import { tDynamic, type MessageParams } from "./i18n";

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

/**
 * The text to show for an engine message in the interface language. `translate`
 * is the dictionary lookup (replaceable in tests).
 */
export function formatEngineMessage(m: EngineMessage, translate: Translate = tDynamic): string {
  if (typeof m === "string") return m;
  return translate(`engine.${m.code}`, m.params) ?? m.message;
}
