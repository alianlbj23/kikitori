/**
 * "Copy diagnostics": a plain-text report for bug reports.
 *
 * Role: pure functions that assemble what a maintainer needs to reproduce a
 * problem (docs/specs/competitive-features.md F8) — app and engine versions,
 * OS, the hardware detection summary, the current settings and the last
 * engine messages — and remove what must not leave the computer:
 *
 * - settings keys that look like secrets are replaced; `api_key_env` is kept
 *   because it is only the *name* of an environment variable (the key itself
 *   is never read by the UI);
 * - credentials and query strings in URLs, bearer tokens, `key=value` secrets
 *   and well-known API key shapes in any text;
 * - the user name in home-folder paths (`C:\Users\<name>`, `/home/<name>`).
 *
 * The report is in English on purpose: it is read by maintainers, not shown
 * as interface text, and stays the same whatever the interface language.
 */

import type { BridgePhase, EngineInfo, EngineState, HardwareInfo, Settings } from "./types";

export const MAX_RECENT_LOG = 20;
const MAX_LOG_CHARS = 500;
const REDACTED = "[redacted]";

/** Settings keys whose values may be secrets. */
const SECRET_KEY = /(^|_)(key|token|secret|password|passwd|credential|auth|cookie)s?($|_)|apikey|api_key/i;
/** Keys that hold only the *name* of an environment variable. */
const ENV_NAME_KEYS = new Set(["api_key_env"]);
const ENV_NAME = /^[A-Za-z_][A-Za-z0-9_]{0,99}$/;

/** Removes secrets and user names from free text (log lines, URLs, paths). */
export function redactText(text: string): string {
  return (
    text
      // scheme://user:pass@host → scheme://[redacted]@host
      .replace(/\b([a-z][a-z0-9+.-]*:\/\/)[^\s/@]+@/gi, `$1${REDACTED}@`)
      // query strings may carry tokens (?api_key=…)
      .replace(/(\b[a-z][a-z0-9+.-]*:\/\/[^\s?#]*)\?[^\s#]*/gi, `$1?${REDACTED}`)
      .replace(/\b(bearer|basic)\s+[A-Za-z0-9._~+/=-]{8,}/gi, `$1 ${REDACTED}`)
      .replace(
        /\b(api[_-]?key|access[_-]?token|token|secret|password|passwd|authorization)(["']?\s*[:=]\s*["']?)[^\s"',;&]+/gi,
        `$1$2${REDACTED}`,
      )
      .replace(/\b(sk|pk|rk)-[A-Za-z0-9_-]{16,}/g, REDACTED)
      .replace(/\b(hf|gh[pousr]|glpat|xox[abp])[_-][A-Za-z0-9_-]{16,}/g, REDACTED)
      // home folders: keep the shape of the path, drop the user name
      .replace(/\b([A-Za-z]:[\\/]+Users[\\/]+)[^\\/\s"']+/gi, "$1<user>")
      .replace(/(\/(?:Users|home)\/)[^/\s"']+/g, "$1<user>")
  );
}

/** A deep copy of `value` with secret-looking keys replaced and strings redacted. */
export function redactSettings(value: unknown, key = ""): unknown {
  if (ENV_NAME_KEYS.has(key)) {
    return typeof value === "string" && ENV_NAME.test(value) ? value : REDACTED;
  }
  if (key !== "" && SECRET_KEY.test(key)) return REDACTED;
  if (typeof value === "string") return redactText(value);
  if (Array.isArray(value)) return value.map((v) => redactSettings(v));
  if (typeof value === "object" && value !== null) {
    return Object.fromEntries(Object.entries(value).map(([k, v]) => [k, redactSettings(v, k)]));
  }
  return value;
}

/** The last engine messages (log events, status errors), newest last. */
export class RecentLog {
  private readonly lines: string[] = [];

  constructor(private readonly limit = MAX_RECENT_LOG) {}

  push(level: string, message: string, at: Date = new Date()): void {
    const text = message.length > MAX_LOG_CHARS ? `${message.slice(0, MAX_LOG_CHARS)}…` : message;
    this.lines.push(`${at.toISOString()} ${level} ${text.replace(/\s+/g, " ")}`);
    if (this.lines.length > this.limit) this.lines.splice(0, this.lines.length - this.limit);
  }

  entries(): readonly string[] {
    return this.lines;
  }
}

export interface DiagnosticsInput {
  appVersion: string | null;
  uiLocale: string;
  userAgent: string;
  bridgePhase: BridgePhase | null;
  bridgeDetail: string | null;
  engine: EngineInfo | null;
  sessionState: EngineState;
  hardware: HardwareInfo | null;
  settings: Settings | null;
  settingsPath: string | null;
  recentLog: readonly string[];
  now: Date;
}

function gb(bytes: number | null): string {
  return bytes === null ? "unknown" : `${(bytes / 1024 ** 3).toFixed(1)} GiB`;
}

function hardwareLines(hw: HardwareInfo): string[] {
  const cpu = hw.cpu;
  const cores = [cpu.physical_cores, cpu.logical_cores].map((n) => (n === null ? "?" : String(n))).join("/");
  const out = [
    `os: ${hw.os} ${hw.os_version} (${hw.machine})`,
    `cpu: ${cpu.model ?? "unknown"} (${cores} cores/threads)`,
    `memory: ${gb(hw.memory.total_bytes)} total, ${gb(hw.memory.available_bytes)} available`,
  ];
  if (hw.gpus.length === 0) out.push("gpu: none detected");
  for (const g of hw.gpus) {
    out.push(
      `gpu ${g.index}: ${g.name}, ${g.memory_total_mb} MiB, driver ${g.driver_version ?? "?"}, compute ${g.compute_capability ?? "?"}`,
    );
  }
  if (hw.apple_silicon) out.push("apple silicon: yes");
  out.push(
    `cublas: ${hw.cublas.supported_platform ? (hw.cublas.available ? `available (${hw.cublas.source ?? "?"})` : "missing") : "not used on this platform"}`,
  );
  const ts = hw.translation_server;
  out.push(
    ts
      ? `translation server: ${ts.base_url} (${ts.is_local ? "local" : "remote"}, reachable: ${ts.reachable ? "yes" : "no"}` +
          `${ts.models === null ? "" : `, ${ts.models.length} models`}${ts.error ? `, error: ${ts.error}` : ""})`
      : "translation server: not detected",
  );
  for (const e of hw.errors) out.push(`detection error: ${e}`);
  return out;
}

/** The whole report as plain text, already redacted. */
export function buildDiagnostics(i: DiagnosticsInput): string {
  const engine = i.engine
    ? `${i.engine.version} (protocol ${i.engine.protocol_version}, platform ${i.engine.platform}${i.engine.fake ? ", fake engine" : ""})`
    : "not connected";
  const sections: string[][] = [
    ["kikitori diagnostics", `generated: ${i.now.toISOString()}`],
    [
      "[app]",
      `version: ${i.appVersion ?? "unknown"}`,
      `interface language: ${i.uiLocale}`,
      `user agent: ${i.userAgent}`,
    ],
    [
      "[engine]",
      `bridge: ${i.bridgePhase ?? "unknown"}${i.bridgeDetail ? ` (${i.bridgeDetail})` : ""}`,
      `engine: ${engine}`,
      `session: ${i.sessionState}`,
    ],
    ["[hardware]", ...(i.hardware ? hardwareLines(i.hardware) : ["not detected yet (open the Models page to detect)"])],
    [
      "[settings]",
      `path: ${i.settingsPath ?? "unknown"}`,
      i.settings ? JSON.stringify(redactSettings(i.settings), null, 2) : "not loaded",
    ],
    [`[recent engine messages] (last ${MAX_RECENT_LOG})`, ...(i.recentLog.length ? i.recentLog : ["none"])],
  ];
  return redactText(sections.map((lines) => lines.join("\n")).join("\n\n")) + "\n";
}
