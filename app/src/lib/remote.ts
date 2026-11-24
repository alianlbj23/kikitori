/**
 * Remote compute (docs/specs/remote-compute.md, T14): the UI's pure logic.
 *
 * Role: everything the main window needs to decide about remote compute
 * without touching the DOM — validating the untrusted `remote.status` result
 * and `engine://remote` events (T13 contract), host/port and pairing-digit
 * input, the pairing dialog's state machine and its wording, the left rail's
 * "運算位置" text, and which extra actions a remote session status offers.
 * `main.ts` only renders these views and calls the Tauri commands.
 *
 * Rust validates every command argument and event again; the checks here exist
 * so a mistake is pointed at the right field at once (and as defence in depth,
 * like `validate.ts`).
 */

import { t, type StringKey } from "./i18n";
import type { EngineState } from "./types";
import { ValidationError } from "./validate";

// ---------------------------------------------------------------- settings `compute`

export type ComputeMode = "local" | "remote";
export const COMPUTE_MODES: readonly ComputeMode[] = ["local", "remote"];

/** `settings.compute` (engine `ComputeSettings`). */
export interface ComputeSettings {
  mode: ComputeMode;
  /** The compute host (IP or host name); "" = not set yet. */
  host: string;
  /** The compute host's main port (A: where this computer connects). */
  port: number;
  /** This computer accepts other computers (B). */
  server_enabled: boolean;
  /** The IP this computer listens on as a compute host; "" = not chosen. */
  listen_host: string;
  /** This computer's listening port as a compute host (B); pairing uses the next port. */
  listen_port: number;
}

export const DEFAULT_COMPUTE_PORT = 47100;
export const COMPUTE_PORT_RANGE: readonly [number, number] = [1024, 65535];
/** The listening port's range: pairing listens on the next port, so 65535 itself is out. */
export const LISTEN_PORT_RANGE: readonly [number, number] = [1024, 65534];
export const MAX_HOST_CHARS = 253;
/** Same character set as the engine (`_COMPUTE_HOST`) and Rust: no scheme, path, spaces or credentials. */
const HOST_PATTERN = /^[A-Za-z0-9.:-]+$/;
/** IPv4 or IPv6 text; the exact form is the engine's to check (`ipaddress.ip_address`). */
const LISTEN_HOST_PATTERN = /^[0-9A-Fa-f.:]{2,45}$/;

export const DEFAULT_COMPUTE: ComputeSettings = {
  mode: "local",
  host: "",
  port: DEFAULT_COMPUTE_PORT,
  server_enabled: false,
  listen_host: "",
  listen_port: DEFAULT_COMPUTE_PORT,
};

export function isValidHost(host: string): boolean {
  return host.length >= 1 && host.length <= MAX_HOST_CHARS && HOST_PATTERN.test(host);
}

export function isValidPort(port: unknown): port is number {
  return (
    typeof port === "number" && Number.isInteger(port) && port >= COMPUTE_PORT_RANGE[0] && port <= COMPUTE_PORT_RANGE[1]
  );
}

export function isValidListenPort(port: unknown): port is number {
  return isValidPort(port) && port <= LISTEN_PORT_RANGE[1];
}

type Obj = Record<string, unknown>;

function asObj(v: unknown): Obj {
  return typeof v === "object" && v !== null && !Array.isArray(v) ? (v as Obj) : {};
}

/**
 * `settings.compute`, falling back field by field like the rest of
 * `parseSettingsResult` (`warn` names each malformed field). A missing section
 * (an engine without remote compute) is the default, not a warning.
 */
export function computeFromSettings(v: unknown, warn: (field: string) => void): ComputeSettings {
  if (v === undefined) return { ...DEFAULT_COMPUTE };
  const o = asObj(v);
  const d = DEFAULT_COMPUTE;
  const mode = (COMPUTE_MODES as readonly unknown[]).includes(o.mode) ? (o.mode as ComputeMode) : d.mode;
  if (o.mode !== undefined && mode !== o.mode) warn("compute.mode");
  let host = d.host;
  if (typeof o.host === "string" && (o.host === "" || isValidHost(o.host))) host = o.host;
  else if (o.host !== undefined) warn("compute.host");
  let port = d.port;
  if (isValidPort(o.port)) port = o.port;
  else if (o.port !== undefined) warn("compute.port");
  let serverEnabled = d.server_enabled;
  if (typeof o.server_enabled === "boolean") serverEnabled = o.server_enabled;
  else if (o.server_enabled !== undefined) warn("compute.server_enabled");
  let listenHost = d.listen_host;
  if (typeof o.listen_host === "string" && (o.listen_host === "" || LISTEN_HOST_PATTERN.test(o.listen_host))) {
    listenHost = o.listen_host;
  } else if (o.listen_host !== undefined) warn("compute.listen_host");
  // An engine from before `listen_port` listened on `port`.
  let listenPort = isValidListenPort(port) ? port : d.listen_port;
  if (isValidListenPort(o.listen_port)) listenPort = o.listen_port;
  else if (o.listen_port !== undefined) warn("compute.listen_port");
  return { mode, host, port, server_enabled: serverEnabled, listen_host: listenHost, listen_port: listenPort };
}

// ---------------------------------------------------------------- the rail's three roles

/**
 * What this computer does, as the rail offers it: only itself (`local`), its
 * audio handled by another computer (`remote`), or handling other computers'
 * audio (`serve`; the user can still run sessions here while nobody is connected).
 */
export type ComputeRole = "local" | "remote" | "serve";
export const COMPUTE_ROLES: readonly ComputeRole[] = ["local", "remote", "serve"];

export function computeRole(c: ComputeSettings): ComputeRole {
  // The engine sets `mode` to local when serving starts. A hand-edited file with both on still sends
  // this computer's sessions away (the engine reads `mode`), so the rail says so rather than "serve".
  if (c.mode === "remote") return "remote";
  return c.server_enabled ? "serve" : "local";
}

/** What choosing a role has to do (`roleChange`). */
export type RoleChange =
  | { kind: "none" }
  /** Save `compute.mode`, after turning serving off when it is on. */
  | { kind: "set_mode"; mode: ComputeMode; stopServing: boolean }
  /** `remote_server_set(true, listenHost)`. */
  | { kind: "serve"; listenHost: string }
  /** Several addresses and none chosen yet: the user picks one. */
  | { kind: "choose_address"; addresses: string[] }
  /** No private-network or Tailscale address to listen on. */
  | { kind: "no_address" };

/**
 * The steps behind a role choice. Serving needs a listen address: the saved
 * one, else this computer's only address; with several the user chooses (the
 * engine never listens on every interface by default).
 */
export function roleChange(target: ComputeRole, c: ComputeSettings, status: RemoteStatus | null): RoleChange {
  const serving = c.server_enabled || (status?.server.enabled ?? false);
  if (target !== "serve") {
    if (!serving && c.mode === target) return { kind: "none" };
    return { kind: "set_mode", mode: target, stopServing: serving };
  }
  if (serving) return { kind: "none" };
  const saved = status?.server.listen_host || c.listen_host;
  if (saved) return { kind: "serve", listenHost: saved };
  const addresses = status?.addresses ?? [];
  if (addresses.length === 1) return { kind: "serve", listenHost: addresses[0]! };
  if (addresses.length > 1) return { kind: "choose_address", addresses: [...addresses] };
  return { kind: "no_address" };
}

/** Where an address reaches: Tailscale (100.64.0.0/10, fd7a:115c:a1e0::/48) or the local network. */
export type AddressKind = "tailscale" | "lan";

export function addressKind(address: string): AddressKind {
  const v4 = /^(\d{1,3})\.(\d{1,3})\.\d{1,3}\.\d{1,3}$/.exec(address);
  if (v4) {
    const a = Number(v4[1]);
    const b = Number(v4[2]);
    return a === 100 && b >= 64 && b <= 127 ? "tailscale" : "lan";
  }
  return address.toLowerCase().startsWith("fd7a:115c:a1e0:") ? "tailscale" : "lan";
}

/** "這個網路（家裡或公司）：192.168.1.20": the address with where it reaches, in plain words. */
export function addressLabel(address: string): string {
  const label = t(addressKind(address) === "tailscale" ? "address.tailscale" : "address.lan");
  return t("address.option", { label, address });
}

/** Patch that switches where recognition and translation run (the engine deep-merges `compute`). */
export function computeModePatch(mode: ComputeMode): Record<string, unknown> {
  return { compute: { mode } };
}

// ---------------------------------------------------------------- A: compute host form

/** Raw values of the "遠端運算" form (strings straight from the inputs). */
export interface RemoteForm {
  host: string;
  port: string;
}

export type RemoteFormErrors = Partial<Record<keyof RemoteForm, string>>;

export function remoteFormFromSettings(c: ComputeSettings): RemoteForm {
  return { host: c.host, port: String(c.port) };
}

/** The port typed into a form, or null when it is not an integer in range. */
export function parsePort(raw: string): number | null {
  const text = raw.trim();
  if (!/^\d{1,5}$/.test(text)) return null;
  const n = Number.parseInt(text, 10);
  return isValidPort(n) ? n : null;
}

/** The listening port typed on the compute-host side, or null outside `LISTEN_PORT_RANGE`. */
export function parseListenPort(raw: string): number | null {
  const n = parsePort(raw);
  return n !== null && isValidListenPort(n) ? n : null;
}

/** Checks the form; `requireHost` is for 配對 (saving an empty host means "not set"). */
export function validateRemoteForm(form: RemoteForm, requireHost: boolean): RemoteFormErrors {
  const errors: RemoteFormErrors = {};
  const host = form.host.trim();
  if (host === "") {
    if (requireHost) errors.host = t("validation.remoteHostEmpty");
  } else if (!isValidHost(host)) {
    errors.host = t("validation.remoteHost", { max: MAX_HOST_CHARS });
  }
  if (parsePort(form.port) === null) {
    errors.port = t("validation.remotePort", { min: COMPUTE_PORT_RANGE[0], max: COMPUTE_PORT_RANGE[1] });
  }
  return errors;
}

/** Patch for `settings.set`; call only after `validateRemoteForm` passed. */
export function remoteFormPatch(form: RemoteForm): Record<string, unknown> {
  return { compute: { host: form.host.trim(), port: parsePort(form.port) ?? DEFAULT_COMPUTE_PORT } };
}

/** The form holds something other than the saved settings. */
export function remoteFormDiffers(form: RemoteForm, c: ComputeSettings): boolean {
  return form.host.trim() !== c.host || parsePort(form.port) !== c.port;
}

/** Maps the engine's `"compute.host: …"` rejection back to the form field. */
export function remoteFieldForEnginePath(message: string): keyof RemoteForm | null {
  const path = message.split(":", 1)[0]?.trim() ?? "";
  if (path === "compute.host") return "host";
  if (path === "compute.port") return "port";
  return null;
}

/** `host` or `host:port` (`[v6]:port`) when the port is not the default. */
export function formatHostPort(host: string, port: number): string {
  if (port === DEFAULT_COMPUTE_PORT) return host;
  return host.includes(":") ? `[${host}]:${port}` : `${host}:${port}`;
}

// ---------------------------------------------------------------- pairing digits and fingerprints

const FULLWIDTH_ZERO = 0xff10;
const FULLWIDTH_NINE = 0xff19;

/**
 * Pairing digits as typed: full-width digits (an IME left on) become ASCII and
 * every kind of space is removed, so "１２３ ４５６" is "123456". Anything else
 * is kept, so `isPairingDigits` can reject it instead of silently dropping it.
 */
export function normalizeDigits(raw: string): string {
  let out = "";
  for (const ch of raw) {
    const cp = ch.codePointAt(0)!;
    if (cp >= FULLWIDTH_ZERO && cp <= FULLWIDTH_NINE) out += String.fromCharCode(0x30 + cp - FULLWIDTH_ZERO);
    else if (!/\s/u.test(ch)) out += ch;
  }
  return out;
}

export function isPairingDigits(v: unknown): v is string {
  return typeof v === "string" && /^[0-9]{6}$/.test(v);
}

/** "123456" → "123 456": two groups are easier to compare between two screens. */
export function formatDigits(digits: string): string {
  return isPairingDigits(digits) ? `${digits.slice(0, 3)} ${digits.slice(3)}` : digits;
}

const SPKI_PATTERN = /^[0-9a-f]{64}$/;
const FINGERPRINT_PATTERN = /^[0-9a-f]{4}( [0-9a-f]{4}){0,15}$/;

/**
 * A SPKI hash (64 hex) as the contract's fingerprint: the first 16 bytes in
 * groups of four hex characters. An already grouped fingerprint is returned as is.
 */
export function formatFingerprint(value: string): string {
  const v = value.trim().toLowerCase();
  if (SPKI_PATTERN.test(v)) return (v.slice(0, 32).match(/.{4}/g) ?? []).join(" ");
  return v;
}

// ---------------------------------------------------------------- remote.status / engine://remote

export type PairingRole = "server" | "client";
/** `remote_unpair` role: `peer` = a computer allowed in (B), `server` = a paired compute host (A). */
export type UnpairRole = "peer" | "server";
export const PAIRING_STATES = ["waiting", "enter_digits", "show_digits", "confirm", "done", "failed"] as const;
export type PairingStep = (typeof PAIRING_STATES)[number];

/** Failure codes of `PairingState.code` (T13). */
export const PAIRING_FAILURES = [
  "pairing_closed",
  "concurrent_pairing",
  "mismatch",
  "rejected",
  "timeout",
  "protocol_error",
  "bad_commitment",
  "bad_signature",
  "bad_certificate",
  "cancelled",
  "connection_lost",
  "unreachable",
  "refused",
  "expired",
  "trust_store_error",
  "identity_error",
  "internal_error",
] as const;
export type PairingFailure = (typeof PAIRING_FAILURES)[number];

export interface PairingState {
  role: PairingRole;
  state: PairingStep;
  /** `show_digits` (client): the 6 digits to type on the compute host. */
  digits: string | null;
  /** The other computer's name, ≤ 64 characters. */
  peer_name: string | null;
  /** `failed` only: a known failure code or another identifier from a newer engine. */
  code: string | null;
}

export interface TrustedEntry {
  spki: string;
  name: string;
  fingerprint: string;
  /** ISO 8601 UTC. */
  paired_at: string;
}

export type PeerState = "connected" | "waiting_resume";

export interface ServerPeer {
  name: string;
  fingerprint: string;
  state: PeerState;
}

export interface RemoteServerStatus {
  enabled: boolean;
  listening: boolean;
  listen_host: string;
  port: number;
  error: string | null;
  peer: ServerPeer | null;
}

export interface RemoteStatus {
  identity: { fingerprint: string } | null;
  identity_error: string | null;
  server: RemoteServerStatus;
  addresses: string[];
  /** Computers allowed to connect to this one (B). */
  peers: TrustedEntry[];
  /** Compute hosts this computer paired with (A). */
  servers: TrustedEntry[];
  pairing: PairingState | null;
}

export type RemoteEvent =
  | { kind: "pairing"; pairing: PairingState }
  | { kind: "server"; listening: boolean; peer: ServerPeer | null; error: string | null };

const MAX_NAME_CHARS = 64;
const MAX_ERROR_CHARS = 500;
const MAX_LIST = 256;
const IDENTIFIER = /^[a-z][a-z0-9_]{0,63}$/;

function obj(v: unknown, path: string): Obj {
  if (typeof v !== "object" || v === null || Array.isArray(v)) throw new ValidationError(path, "expected an object");
  return v as Obj;
}

function boundedStr(o: Obj, key: string, path: string, max: number): string {
  const v = o[key];
  if (typeof v !== "string") throw new ValidationError(`${path}.${key}`, "expected a string");
  if (v.length > max) throw new ValidationError(`${path}.${key}`, `longer than ${max}`);
  return v;
}

function optBoundedStr(o: Obj, key: string, path: string, max: number): string | null {
  if (o[key] === undefined || o[key] === null) return null;
  return boundedStr(o, key, path, max);
}

function flag(o: Obj, key: string, path: string): boolean {
  if (typeof o[key] !== "boolean") throw new ValidationError(`${path}.${key}`, "expected a boolean");
  return o[key] as boolean;
}

function fingerprint(o: Obj, key: string, path: string): string {
  const v = boundedStr(o, key, path, 80);
  if (!FINGERPRINT_PATTERN.test(v)) throw new ValidationError(`${path}.${key}`, "not a fingerprint");
  return v;
}

function list(o: Obj, key: string, path: string): unknown[] {
  const v = o[key];
  if (!Array.isArray(v)) throw new ValidationError(`${path}.${key}`, "expected an array");
  if (v.length > MAX_LIST) throw new ValidationError(`${path}.${key}`, `more than ${MAX_LIST} entries`);
  return v;
}

export function parseTrustedEntry(v: unknown, path: string): TrustedEntry {
  const o = obj(v, path);
  const spki = boundedStr(o, "spki", path, 64);
  if (!SPKI_PATTERN.test(spki)) throw new ValidationError(`${path}.spki`, "expected 64 lowercase hex characters");
  return {
    spki,
    name: boundedStr(o, "name", path, MAX_NAME_CHARS),
    fingerprint: fingerprint(o, "fingerprint", path),
    paired_at: boundedStr(o, "paired_at", path, 40),
  };
}

function parsePeer(v: unknown, path: string): ServerPeer | null {
  if (v === null || v === undefined) return null;
  const o = obj(v, path);
  const state = o.state;
  if (state !== "connected" && state !== "waiting_resume") {
    throw new ValidationError(`${path}.state`, "expected connected or waiting_resume");
  }
  return { name: boundedStr(o, "name", path, MAX_NAME_CHARS), fingerprint: fingerprint(o, "fingerprint", path), state };
}

/** `PairingState` (or the body of a `pairing` event). */
export function parsePairingState(v: unknown, path = "pairing"): PairingState {
  const o = obj(v, path);
  const role = o.role;
  if (role !== "server" && role !== "client") throw new ValidationError(`${path}.role`, "expected server or client");
  const state = o.state;
  if (typeof state !== "string" || !(PAIRING_STATES as readonly string[]).includes(state)) {
    throw new ValidationError(`${path}.state`, "unknown pairing state");
  }
  const digits = o.digits === undefined || o.digits === null ? null : o.digits;
  if (digits !== null && !isPairingDigits(digits)) throw new ValidationError(`${path}.digits`, "expected 6 digits");
  const code = optBoundedStr(o, "code", path, 64);
  if (code !== null && !IDENTIFIER.test(code)) throw new ValidationError(`${path}.code`, "not an identifier");
  return {
    role,
    state: state as PairingStep,
    digits,
    peer_name: optBoundedStr(o, "peer_name", path, MAX_NAME_CHARS),
    code,
  };
}

/** `remote.status` (and the result of `server_set`, `pair_open`, `unpair`). */
export function parseRemoteStatus(v: unknown): RemoteStatus {
  const p = "remote.status";
  const o = obj(v, p);
  const identity =
    o.identity === null || o.identity === undefined
      ? null
      : { fingerprint: fingerprint(obj(o.identity, `${p}.identity`), "fingerprint", `${p}.identity`) };
  const s = obj(o.server, `${p}.server`);
  const sp = `${p}.server`;
  const port = s.port;
  if (!isValidPort(port)) throw new ValidationError(`${sp}.port`, "expected a port number");
  const listenHost = boundedStr(s, "listen_host", sp, 45);
  if (listenHost !== "" && !LISTEN_HOST_PATTERN.test(listenHost)) {
    throw new ValidationError(`${sp}.listen_host`, "not an IP address");
  }
  const addresses = list(o, "addresses", p).map((a, i) => {
    if (typeof a !== "string" || !LISTEN_HOST_PATTERN.test(a)) {
      throw new ValidationError(`${p}.addresses[${i}]`, "not an IP address");
    }
    return a;
  });
  return {
    identity,
    identity_error: optBoundedStr(o, "identity_error", p, MAX_ERROR_CHARS),
    server: {
      enabled: flag(s, "enabled", sp),
      listening: flag(s, "listening", sp),
      listen_host: listenHost,
      port,
      error: optBoundedStr(s, "error", sp, MAX_ERROR_CHARS),
      peer: parsePeer(s.peer, `${sp}.peer`),
    },
    addresses: [...new Set(addresses)],
    peers: list(o, "peers", p).map((e, i) => parseTrustedEntry(e, `${p}.peers[${i}]`)),
    servers: list(o, "servers", p).map((e, i) => parseTrustedEntry(e, `${p}.servers[${i}]`)),
    pairing: o.pairing === null || o.pairing === undefined ? null : parsePairingState(o.pairing, `${p}.pairing`),
  };
}

/** `engine://remote` payload. */
export function parseRemoteEvent(v: unknown): RemoteEvent {
  const p = "remote";
  const o = obj(v, p);
  if (o.kind === "pairing") return { kind: "pairing", pairing: parsePairingState(o, p) };
  if (o.kind === "server") {
    return {
      kind: "server",
      listening: flag(o, "listening", p),
      peer: parsePeer(o.peer, `${p}.peer`),
      error: optBoundedStr(o, "error", p, MAX_ERROR_CHARS),
    };
  }
  throw new ValidationError(`${p}.kind`, "expected pairing or server");
}

/** A `server` event applied to the last status, so the section updates before `remote.status` answers. */
export function withServerEvent(status: RemoteStatus, ev: Extract<RemoteEvent, { kind: "server" }>): RemoteStatus {
  return { ...status, server: { ...status.server, listening: ev.listening, peer: ev.peer, error: ev.error } };
}

// ---------------------------------------------------------------- pairing dialog

export type PairPhase = "starting" | PairingStep;

export interface PairDialog {
  role: PairingRole;
  phase: PairPhase;
  /** Client: the compute host being paired with (shown while connecting). */
  target: string | null;
  digits: string | null;
  peerName: string | null;
  /** `failed`: the engine's failure code, or null when `message` explains it. */
  code: string | null;
  /** `failed` because a command was rejected: its explanation (lib/api describeError). */
  message: string | null;
  /** A command for the current step is in flight; the step's buttons wait. */
  busy: boolean;
}

/** The steps an engine reports for each role (T13 `PairingState`). */
const ROLE_STEPS: Readonly<Record<PairingRole, readonly PairingStep[]>> = {
  server: ["waiting", "enter_digits", "done", "failed"],
  client: ["waiting", "show_digits", "confirm", "done", "failed"],
};

/** The dialog right after the user pressed 配對 / 允許新的電腦配對, before the command answers. */
export function openPairDialog(role: PairingRole, target: string | null = null): PairDialog {
  return { role, phase: "starting", target, digits: null, peerName: null, code: null, message: null, busy: true };
}

export function isPairDialogFinished(d: PairDialog): boolean {
  return d.phase === "done" || d.phase === "failed";
}

/**
 * Next dialog state for a `pairing` event. Events of the other role (this
 * computer pairing in both directions is impossible, but a stale event is not)
 * or a step that role never reports are ignored, and a finished dialog stays as
 * it is until the user closes it, so a late event cannot overwrite the result.
 * A closed dialog (null) stays closed: the dialog only opens from a button.
 */
export function applyPairingEvent(d: PairDialog | null, ev: PairingState): PairDialog | null {
  if (!d || ev.role !== d.role || isPairDialogFinished(d)) return d;
  if (!ROLE_STEPS[d.role].includes(ev.state)) return d;
  return {
    ...d,
    phase: ev.state,
    // `confirm` repeats no digits; the ones shown before stay next to the question.
    digits: ev.digits ?? d.digits,
    peerName: ev.peer_name ?? d.peerName,
    code: ev.state === "failed" ? (ev.code ?? "internal_error") : null,
    message: null,
    busy: false,
  };
}

/** The command that started or answered a step was accepted; the engine's events take it from here. */
export function pairCommandDone(d: PairDialog | null): PairDialog | null {
  if (!d || isPairDialogFinished(d)) return d;
  return { ...d, busy: false, phase: d.phase === "starting" ? "waiting" : d.phase };
}

/** A pairing command was rejected (`message` is what to show). */
export function pairCommandFailed(d: PairDialog | null, message: string): PairDialog | null {
  if (!d || isPairDialogFinished(d)) return d;
  return { ...d, phase: "failed", code: null, message, busy: false };
}

/** The user answered a step (yes/no, digits); its buttons wait for the engine. */
export function pairAnswering(d: PairDialog | null): PairDialog | null {
  return d && !isPairDialogFinished(d) ? { ...d, busy: true } : d;
}

export type PairAction = "yes" | "no" | "submit" | "cancel" | "close";

export interface PairDialogView {
  title: string;
  text: string;
  /** The digits to compare, as "123 456"; null when none are shown. */
  digits: string | null;
  /** Show the 6-digit input (server, `enter_digits`). */
  digitsInput: boolean;
  actions: PairAction[];
  tone: "info" | "ok" | "bad";
  busy: boolean;
}

const FAILURE_TEXT: Readonly<Record<PairingFailure, StringKey>> = {
  pairing_closed: "pair.failed.pairing_closed",
  concurrent_pairing: "pair.failed.concurrent_pairing",
  mismatch: "pair.failed.mismatch",
  rejected: "pair.failed.rejected",
  timeout: "pair.failed.timeout",
  protocol_error: "pair.failed.protocol_error",
  bad_commitment: "pair.failed.bad_commitment",
  bad_signature: "pair.failed.bad_signature",
  bad_certificate: "pair.failed.bad_certificate",
  cancelled: "pair.failed.cancelled",
  connection_lost: "pair.failed.connection_lost",
  unreachable: "pair.failed.unreachable",
  refused: "pair.failed.refused",
  expired: "pair.failed.expired",
  trust_store_error: "pair.failed.trust_store_error",
  identity_error: "pair.failed.identity_error",
  internal_error: "pair.failed.internal_error",
};

/** Why pairing failed, in the interface language; unknown codes (a newer engine) are named. */
export function pairFailureText(code: string | null): string {
  const key = code && Object.hasOwn(FAILURE_TEXT, code) ? FAILURE_TEXT[code as PairingFailure] : null;
  return key ? t(key) : t("pair.failedOther", { code: code ?? "?" });
}

function peerLabel(d: PairDialog): string {
  return d.peerName && d.peerName.trim() !== "" ? d.peerName : t("pair.unknownPeer");
}

export function pairDialogView(d: PairDialog): PairDialogView {
  const title = t(d.role === "client" ? "pair.titleClient" : "pair.titleServer");
  const base = { title, digits: null, digitsInput: false, busy: d.busy };
  switch (d.phase) {
    case "starting":
    case "waiting":
      return {
        ...base,
        text:
          d.role === "client"
            ? t("pair.connecting", { host: d.target ?? t("pair.unknownPeer") })
            : t("pair.waitingServer"),
        actions: ["cancel"],
        tone: "info",
      };
    case "show_digits":
      return {
        ...base,
        text: t("pair.showDigits"),
        digits: d.digits ? formatDigits(d.digits) : null,
        actions: ["cancel"],
        tone: "info",
      };
    case "confirm":
      return {
        ...base,
        text: t("pair.confirm"),
        digits: d.digits ? formatDigits(d.digits) : null,
        actions: ["yes", "no"],
        tone: "info",
      };
    case "enter_digits":
      return { ...base, text: t("pair.enterDigits", { name: peerLabel(d) }), digitsInput: true, actions: ["submit", "cancel"], tone: "info" };
    case "done":
      return {
        ...base,
        text: d.role === "client" ? t("pair.doneClient") : t("pair.doneServer", { name: peerLabel(d) }),
        actions: ["close"],
        tone: "ok",
        busy: false,
      };
    case "failed":
      return { ...base, text: d.message ?? pairFailureText(d.code), actions: ["close"], tone: "bad", busy: false };
  }
}

/** Message for the digit input, or null when it holds 6 digits (after `normalizeDigits`). */
export function digitsInputError(raw: string): string | null {
  return isPairingDigits(normalizeDigits(raw)) ? null : t("pair.digitsInvalid");
}

// ---------------------------------------------------------------- session and rail

/** What the "重新連線中" notice should do for a `status` event. */
export type ReconnectNotice = "show" | "clear" | "keep";

/**
 * `status running` carries `data.reconnecting` on a remote session (T13):
 * true shows the notice, false clears it, no flag keeps it. Any other state
 * ends the running session, so the notice goes too.
 */
export function reconnectNotice(state: EngineState, reconnecting: boolean | null): ReconnectNotice {
  if (state !== "running") return "clear";
  if (reconnecting === true) return "show";
  if (reconnecting === false) return "clear";
  return "keep";
}

/** Extra buttons on a session error notice. */
export type RemoteErrorAction = "use_local" | "open_settings";

export function remoteErrorActions(code: string | null): RemoteErrorAction[] {
  switch (code) {
    case "remote_lost":
    case "remote_unreachable":
      return ["use_local"];
    case "remote_not_configured":
    case "remote_not_paired":
      return ["open_settings", "use_local"];
    default:
      return [];
  }
}

export interface ComputeRailView {
  hint: string;
  /** The hint reports a problem. */
  error: boolean;
  /** "延遲 12 ms" while a remote session reports its round trip; null otherwise. */
  rtt: string | null;
  /**
   * The shortcut under the hint: the settings section (no computer to send to
   * yet) or pairing a new computer (serving and waiting).
   */
  action: "settings" | "pair" | null;
}

/** The plain-words line under the three roles. */
export function computeRailView(c: ComputeSettings, rttMs: number | null, status: RemoteStatus | null): ComputeRailView {
  const role = computeRole(c);
  if (role === "local") return { hint: t("compute.hintLocal"), error: false, rtt: null, action: null };
  if (role === "remote") {
    if (c.host === "") return { hint: t("compute.hintNoHost"), error: false, rtt: null, action: "settings" };
    return {
      hint: t("compute.hintRemote", { host: formatHostPort(c.host, c.port) }),
      error: false,
      rtt: rttMs === null ? null : t("compute.rtt", { ms: Math.round(rttMs) }),
      action: null,
    };
  }
  const server = status?.server ?? null;
  if (server?.error) {
    return { hint: t("compute.hintServeError", { error: server.error }), error: true, rtt: null, action: null };
  }
  const peer = server?.peer ?? null;
  if (peer) {
    const key = peer.state === "connected" ? "compute.hintServeBusy" : "compute.hintServeReconnect";
    return { hint: t(key, { name: peer.name }), error: false, rtt: null, action: null };
  }
  if (server?.listening) return { hint: t("compute.hintServeWaiting"), error: false, rtt: null, action: "pair" };
  return { hint: t("compute.hintServeOff"), error: false, rtt: null, action: null };
}

/** Local ASR models (and the local translation server) are needed only when computing here. */
export function needsLocalModels(c: ComputeSettings | null | undefined): boolean {
  return (c?.mode ?? "local") === "local";
}

/** Text of the "目前連線中的電腦" line. */
export function serverPeerText(peer: ServerPeer | null): string {
  if (!peer) return t("serve.currentNone");
  return t(peer.state === "connected" ? "serve.peerConnected" : "serve.peerWaiting", { name: peer.name });
}

/** Text of the compute host's listening state. */
export function serverStateText(server: RemoteServerStatus): string {
  if (server.error) return t("serve.error", { error: server.error });
  if (server.listening) return t("serve.listening", { address: formatHostPort(server.listen_host, server.port) });
  return t("serve.notListening");
}

/** The listen-address menu: the computer's addresses, plus a saved one no longer listed. */
export function listenHostOptions(status: RemoteStatus | null, saved: string): string[] {
  const addresses = status?.addresses ?? [];
  const current = status?.server.listen_host || saved;
  return current && !addresses.includes(current) ? [current, ...addresses] : addresses;
}

/** `paired_at` as a local date, or the raw text when it does not parse. */
export function pairedDate(isoUtc: string, locale: string): string {
  const ms = Date.parse(isoUtc);
  if (!Number.isFinite(ms)) return isoUtc;
  try {
    return new Intl.DateTimeFormat(locale, { dateStyle: "medium" }).format(new Date(ms));
  } catch (e) {
    console.error("paired_at formatting failed:", e);
    return isoUtc;
  }
}
