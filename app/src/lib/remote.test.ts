import { afterEach, describe, expect, it } from "vitest";

import { describeStatusError } from "./format";
import { setLocale, t } from "./i18n";
import {
  DEFAULT_COMPUTE,
  DEFAULT_COMPUTE_PORT,
  MAX_HOST_MODELS,
  PAIRING_FAILURES,
  addressKind,
  addressLabel,
  applyPairingEvent,
  computeFromSettings,
  computeModePatch,
  computeRailView,
  computeRole,
  digitsInputError,
  formatDigits,
  formatFingerprint,
  formatHostPort,
  hostHasMoreModels,
  hostInfoErrorText,
  hostModelOptions,
  hostSummary,
  isPairDialogFinished,
  isPairingDigits,
  isValidHost,
  listenHostOptions,
  needsLocalModels,
  needsOverConfirm,
  normalizeDigits,
  openPairDialog,
  pairAnswering,
  pairCommandDone,
  pairCommandFailed,
  pairDialogView,
  pairFailureText,
  pairedDate,
  parseHostInfo,
  parseHostInfoResult,
  parseListenPort,
  parsePairingState,
  parsePort,
  parseRemoteEvent,
  parseRemoteStatus,
  reconnectNotice,
  remoteErrorActions,
  remoteFieldForEnginePath,
  remoteFormDiffers,
  remoteFormFromSettings,
  remoteFormPatch,
  remoteModelPatch,
  roleChange,
  serverPeerText,
  serverStateText,
  shortGpuName,
  validateRemoteForm,
  withServerEvent,
  type ComputeSettings,
  type PairingState,
  type RemoteStatus,
} from "./remote";
import { parseSettingsResult } from "./settings";
import { ValidationError } from "./validate";

afterEach(() => setLocale("zh-TW"));

const SPKI_A = "05aa3f394a539a2362d9a1e4cd57cba2" + "0".repeat(32);
const SPKI_B = "ff".repeat(32);
const FP_A = "05aa 3f39 4a53 9a23 62d9 a1e4 cd57 cba2";

/** The `RemoteStatus` example of the T13 contract, filled in. */
function statusPayload(over: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    identity: { fingerprint: FP_A },
    identity_error: null,
    server: {
      enabled: false,
      listening: false,
      listen_host: "192.168.8.236",
      port: 47100,
      error: null,
      peer: { name: "DESKTOP-1", fingerprint: "ffff ffff", state: "connected" },
    },
    addresses: ["192.168.8.236", "100.88.94.10"],
    peers: [{ spki: SPKI_B, name: "DESKTOP-1", fingerprint: "ffff ffff ffff ffff", paired_at: "2026-11-20T03:04:05Z" }],
    servers: [],
    pairing: null,
    ...over,
  };
}

function remote(over: Partial<ComputeSettings> = {}): ComputeSettings {
  return { ...DEFAULT_COMPUTE, ...over };
}

function pairing(over: Partial<PairingState>): PairingState {
  return { role: "client", state: "waiting", digits: null, peer_name: null, code: null, ...over };
}

describe("settings.compute", () => {
  it("defaults when the engine has no compute section yet (no warning)", () => {
    const warnings: string[] = [];
    expect(computeFromSettings(undefined, (f) => warnings.push(f))).toEqual(DEFAULT_COMPUTE);
    expect(warnings).toEqual([]);
    expect(DEFAULT_COMPUTE.port).toBe(47100);
  });

  it("reads a valid section", () => {
    const c = computeFromSettings(
      {
        mode: "remote",
        host: "gpu-box.tail1234.ts.net",
        port: 47200,
        server_enabled: true,
        listen_host: "fd7a::1",
        listen_port: 47300,
        remote_fast_model: "",
        remote_final_model: "large-v3",
      },
      () => {
        throw new Error("no warning expected");
      },
    );
    expect(c).toEqual({
      mode: "remote",
      host: "gpu-box.tail1234.ts.net",
      port: 47200,
      server_enabled: true,
      listen_host: "fd7a::1",
      listen_port: 47300,
      remote_fast_model: "",
      remote_final_model: "large-v3",
    });
  });

  it("reads the models asked of the compute host, defaulting to its own setting", () => {
    const quiet = () => {
      throw new Error("no warning expected");
    };
    expect(DEFAULT_COMPUTE.remote_fast_model).toBe("");
    expect(DEFAULT_COMPUTE.remote_final_model).toBe("");
    expect(computeFromSettings({ mode: "remote" }, quiet).remote_final_model).toBe("");
    expect(computeFromSettings({ remote_fast_model: "large-v3-turbo" }, quiet).remote_fast_model).toBe("large-v3-turbo");
    const warnings: string[] = [];
    const c = computeFromSettings(
      { remote_fast_model: "../x", remote_final_model: 3 },
      (f) => warnings.push(f),
    );
    expect([c.remote_fast_model, c.remote_final_model]).toEqual(["", ""]);
    expect(warnings).toEqual(["compute.remote_fast_model", "compute.remote_final_model"]);
    expect(remoteModelPatch("large-v3")).toEqual({ compute: { remote_final_model: "large-v3" } });
    expect(remoteModelPatch("")).toEqual({ compute: { remote_final_model: "" } });
  });

  it("keeps the listening port apart from the port this computer connects to", () => {
    const quiet = () => {
      throw new Error("no warning expected");
    };
    // An engine from before `listen_port` listened on `port`.
    expect(computeFromSettings({ port: 47200 }, quiet).listen_port).toBe(47200);
    expect(computeFromSettings({ port: 65535 }, quiet).listen_port).toBe(DEFAULT_COMPUTE_PORT);
    const warnings: string[] = [];
    // 65535 is out: pairing listens on the next port.
    expect(computeFromSettings({ listen_port: 65535 }, (f) => warnings.push(f)).listen_port).toBe(DEFAULT_COMPUTE_PORT);
    expect(computeFromSettings({ listen_port: "47100" }, (f) => warnings.push(f)).listen_port).toBe(DEFAULT_COMPUTE_PORT);
    expect(warnings).toEqual(["compute.listen_port", "compute.listen_port"]);
    expect(parseListenPort("47101")).toBe(47101);
    expect(parseListenPort("65534")).toBe(65534);
    expect(parseListenPort("65535")).toBeNull();
    expect(parseListenPort("80")).toBeNull();
  });

  it("falls back field by field and names each malformed field", () => {
    const warnings: string[] = [];
    const c = computeFromSettings(
      { mode: "cloud", host: "http://x/y", port: 80, server_enabled: "yes", listen_host: "0.0.0.0; rm", listen_port: -1 },
      (f) => warnings.push(f),
    );
    expect(c).toEqual(DEFAULT_COMPUTE);
    expect(warnings).toEqual([
      "compute.mode",
      "compute.host",
      "compute.port",
      "compute.server_enabled",
      "compute.listen_host",
      "compute.listen_port",
    ]);
  });

  it("is part of parseSettingsResult", () => {
    const parsed = parseSettingsResult({ settings: { compute: { mode: "remote", host: "10.0.0.2", port: 47100 } } });
    expect(parsed.settings.compute).toEqual(remote({ mode: "remote", host: "10.0.0.2" }));
    expect(parsed.warnings).toEqual([]);
    expect(parseSettingsResult({ settings: {} }).settings.compute).toEqual(DEFAULT_COMPUTE);
    expect(parseSettingsResult({ settings: { compute: { port: "47100" } } }).warnings).toEqual(["compute.port"]);
  });

  it("builds the mode patch the engine deep-merges", () => {
    expect(computeModePatch("remote")).toEqual({ compute: { mode: "remote" } });
    expect(needsLocalModels(remote())).toBe(true);
    expect(needsLocalModels(remote({ mode: "remote" }))).toBe(false);
    expect(needsLocalModels(null)).toBe(true);
  });
});

describe("compute host form", () => {
  it("accepts IPs and host names like the engine and Rust", () => {
    for (const host of ["192.168.8.236", "fd7a:115c:a1e0::1", "gpu-box.tail1234.ts.net", "a"]) {
      expect(isValidHost(host), host).toBe(true);
    }
    for (const host of ["", "http://x", "a b", "x/y", "user@host", "[::1]", "a".repeat(254), "主機"]) {
      expect(isValidHost(host), host).toBe(false);
    }
    expect(isValidHost("a".repeat(253))).toBe(true);
  });

  it("parses ports in 1024–65535 only", () => {
    expect(parsePort(" 47100 ")).toBe(47100);
    expect(parsePort("1024")).toBe(1024);
    expect(parsePort("65535")).toBe(65535);
    for (const raw of ["", "1023", "65536", "47100.5", "-1", "4e4", "port"]) expect(parsePort(raw), raw).toBeNull();
  });

  it("validates: an empty host is fine to save but not to pair", () => {
    expect(validateRemoteForm({ host: "", port: "47100" }, false)).toEqual({});
    expect(validateRemoteForm({ host: " ", port: "47100" }, true)).toEqual({ host: t("validation.remoteHostEmpty") });
    expect(validateRemoteForm({ host: "10.0.0.2", port: "47100" }, true)).toEqual({});
    const errors = validateRemoteForm({ host: "http://10.0.0.2", port: "80" }, true);
    expect(errors.host).toBe(t("validation.remoteHost", { max: 253 }));
    expect(errors.port).toBe(t("validation.remotePort", { min: 1024, max: 65535 }));
  });

  it("round-trips the settings and only reports real changes", () => {
    const saved = remote({ host: "10.0.0.2", port: 47200 });
    const form = remoteFormFromSettings(saved);
    expect(form).toEqual({ host: "10.0.0.2", port: "47200" });
    expect(remoteFormDiffers(form, saved)).toBe(false);
    expect(remoteFormDiffers({ host: " 10.0.0.2 ", port: "47200" }, saved)).toBe(false);
    expect(remoteFormDiffers({ host: "10.0.0.3", port: "47200" }, saved)).toBe(true);
    expect(remoteFormPatch({ host: " 10.0.0.3 ", port: "47300" })).toEqual({ compute: { host: "10.0.0.3", port: 47300 } });
  });

  it("maps engine rejections back to the field", () => {
    expect(remoteFieldForEnginePath("compute.host: expected an IP address or host name")).toBe("host");
    expect(remoteFieldForEnginePath("compute.port: out of range")).toBe("port");
    expect(remoteFieldForEnginePath("compute.mode: bad")).toBeNull();
  });

  it("shows the port only when it is not the default, bracketing IPv6", () => {
    expect(formatHostPort("10.0.0.2", DEFAULT_COMPUTE_PORT)).toBe("10.0.0.2");
    expect(formatHostPort("10.0.0.2", 47200)).toBe("10.0.0.2:47200");
    expect(formatHostPort("fd7a::1", 47200)).toBe("[fd7a::1]:47200");
  });
});

describe("pairing digits and fingerprints", () => {
  it("normalizes full-width digits and every kind of space", () => {
    expect(normalizeDigits("１２３ ４５６")).toBe("123456");
    expect(normalizeDigits(" 123　456\t")).toBe("123456");
    expect(normalizeDigits("12a456")).toBe("12a456");
  });

  it("checks for exactly six ASCII digits", () => {
    expect(isPairingDigits("123456")).toBe(true);
    for (const v of ["12345", "1234567", "12345a", "１２３４５６", 123456, null]) expect(isPairingDigits(v)).toBe(false);
    expect(digitsInputError("１２３ ４５６")).toBeNull();
    expect(digitsInputError("12345")).toBe(t("pair.digitsInvalid"));
    expect(digitsInputError("")).toBe(t("pair.digitsInvalid"));
  });

  it("shows the digits as two groups", () => {
    expect(formatDigits("123456")).toBe("123 456");
    expect(formatDigits("12")).toBe("12");
  });

  it("formats a SPKI hash as the contract's fingerprint", () => {
    expect(formatFingerprint(SPKI_A)).toBe(FP_A);
    expect(formatFingerprint(SPKI_A.toUpperCase())).toBe(FP_A);
    expect(formatFingerprint(FP_A)).toBe(FP_A);
  });
});

describe("remote.status", () => {
  it("parses the contract's shape", () => {
    const s = parseRemoteStatus(statusPayload({ pairing: { role: "server", state: "enter_digits", peer_name: "A-PC" } }));
    expect(s.identity).toEqual({ fingerprint: FP_A });
    expect(s.server).toEqual({
      enabled: false,
      listening: false,
      listen_host: "192.168.8.236",
      port: 47100,
      error: null,
      peer: { name: "DESKTOP-1", fingerprint: "ffff ffff", state: "connected" },
    });
    expect(s.addresses).toEqual(["192.168.8.236", "100.88.94.10"]);
    expect(s.peers).toHaveLength(1);
    expect(s.peers[0]!.spki).toBe(SPKI_B);
    expect(s.servers).toEqual([]);
    expect(s.pairing).toEqual({ role: "server", state: "enter_digits", digits: null, peer_name: "A-PC", code: null });
  });

  it("accepts a missing identity with its error, and no listen host yet", () => {
    const s = parseRemoteStatus(
      statusPayload({
        identity: null,
        identity_error: "cert.pem unreadable",
        server: { enabled: false, listening: false, listen_host: "", port: 47100, error: null, peer: null },
      }),
    );
    expect(s.identity).toBeNull();
    expect(s.identity_error).toBe("cert.pem unreadable");
    expect(s.server.peer).toBeNull();
    expect(s.server.listen_host).toBe("");
  });

  it.each([
    ["no object", null],
    ["server missing", statusPayload({ server: undefined })],
    ["port out of range", statusPayload({ server: { ...(statusPayload().server as object), port: 80 } })],
    ["listen host not an IP", statusPayload({ server: { ...(statusPayload().server as object), listen_host: "x.com" } })],
    ["peer state unknown", statusPayload({ server: { ...(statusPayload().server as object), peer: { name: "x", fingerprint: "ffff", state: "busy" } } })],
    ["address not an IP", statusPayload({ addresses: ["192.168.1.2", "evil host"] })],
    ["spki not hex", statusPayload({ servers: [{ spki: "zz", name: "B", fingerprint: "ffff", paired_at: "x" }] })],
    ["fingerprint with markup", statusPayload({ identity: { fingerprint: "<b>05aa</b>" } })],
    ["name too long", statusPayload({ peers: [{ spki: SPKI_B, name: "n".repeat(65), fingerprint: "ffff", paired_at: "x" }] })],
    ["pairing digits not 6 digits", statusPayload({ pairing: { role: "client", state: "show_digits", digits: "12345" } })],
    ["pairing state unknown", statusPayload({ pairing: { role: "client", state: "dancing" } })],
    ["pairing code not an identifier", statusPayload({ pairing: { role: "client", state: "failed", code: "Bad Code!" } })],
    ["too many peers", statusPayload({ peers: new Array(257).fill(null) })],
  ])("rejects %s", (_name, payload) => {
    expect(() => parseRemoteStatus(payload)).toThrow(ValidationError);
  });

  it("drops duplicate addresses", () => {
    expect(parseRemoteStatus(statusPayload({ addresses: ["10.0.0.1", "10.0.0.1"] })).addresses).toEqual(["10.0.0.1"]);
  });
});

describe("engine://remote events", () => {
  it("parses pairing steps", () => {
    expect(parseRemoteEvent({ kind: "pairing", role: "client", state: "show_digits", digits: "123456" })).toEqual({
      kind: "pairing",
      pairing: { role: "client", state: "show_digits", digits: "123456", peer_name: null, code: null },
    });
  });

  it("parses server changes and applies them to the last status", () => {
    const ev = parseRemoteEvent({
      kind: "server",
      listening: true,
      peer: { name: "A-PC", fingerprint: "aaaa bbbb", state: "waiting_resume" },
      error: null,
    });
    expect(ev).toEqual({
      kind: "server",
      listening: true,
      peer: { name: "A-PC", fingerprint: "aaaa bbbb", state: "waiting_resume" },
      error: null,
    });
    if (ev.kind !== "server") throw new Error("expected a server event");
    const before = parseRemoteStatus(statusPayload());
    const after = withServerEvent(before, ev);
    expect(after.server.listening).toBe(true);
    expect(after.server.peer?.state).toBe("waiting_resume");
    expect(after.server.listen_host).toBe(before.server.listen_host);
    expect(after.peers).toBe(before.peers);
  });

  it("rejects unknown kinds and malformed bodies", () => {
    expect(() => parseRemoteEvent({ kind: "other" })).toThrow(ValidationError);
    expect(() => parseRemoteEvent({ kind: "server", listening: "yes", peer: null })).toThrow(ValidationError);
    expect(() => parseRemoteEvent({ kind: "pairing", role: "both", state: "waiting" })).toThrow(ValidationError);
    expect(() => parsePairingState({ role: "server", state: "waiting", peer_name: 7 })).toThrow(ValidationError);
  });
});

describe("pairing dialog: this computer pairs with a compute host (client)", () => {
  it("walks waiting → show_digits → confirm → done", () => {
    let d = openPairDialog("client", "10.0.0.2");
    expect(d).toMatchObject({ phase: "starting", busy: true });
    expect(pairDialogView(d)).toMatchObject({ text: t("pair.connecting", { host: "10.0.0.2" }), actions: ["cancel"] });

    // The engine may report a step before the `remote_pair` command itself answers.
    d = applyPairingEvent(d, pairing({ state: "show_digits", digits: "123456" }))!;
    d = pairCommandDone(d)!;
    expect(d.phase).toBe("show_digits");
    expect(pairDialogView(d)).toMatchObject({
      text: t("pair.showDigits"),
      digits: "123 456",
      actions: ["cancel"],
      busy: false,
    });

    d = applyPairingEvent(d, pairing({ state: "confirm" }))!;
    const confirm = pairDialogView(d);
    expect(confirm).toMatchObject({ text: t("pair.confirm"), actions: ["yes", "no"], digits: "123 456" });

    d = pairAnswering(d)!;
    expect(pairDialogView(d).busy).toBe(true);
    d = applyPairingEvent(d, pairing({ state: "done" }))!;
    expect(isPairDialogFinished(d)).toBe(true);
    expect(pairDialogView(d)).toMatchObject({ text: t("pair.doneClient"), actions: ["close"], tone: "ok", busy: false });
  });

  it("goes from starting to waiting when the command answers first", () => {
    expect(pairCommandDone(openPairDialog("client", "h"))).toMatchObject({ phase: "waiting", busy: false });
  });

  it("ignores events of the other role, steps the role never has, and anything after the end", () => {
    const d = applyPairingEvent(openPairDialog("client", "h"), pairing({ state: "show_digits", digits: "111222" }))!;
    expect(applyPairingEvent(d, pairing({ role: "server", state: "enter_digits" }))).toBe(d);
    expect(applyPairingEvent(d, pairing({ state: "enter_digits" }))).toBe(d);
    const failed = applyPairingEvent(d, pairing({ state: "failed", code: "mismatch" }))!;
    expect(applyPairingEvent(failed, pairing({ state: "done" }))).toBe(failed);
    expect(pairCommandDone(failed)).toBe(failed);
    expect(pairCommandFailed(failed, "x")).toBe(failed);
    // A closed dialog stays closed: only a button opens it.
    expect(applyPairingEvent(null, pairing({ state: "show_digits", digits: "111222" }))).toBeNull();
  });

  it("explains failures by code, and command rejections by their message", () => {
    let d = applyPairingEvent(openPairDialog("client", "h"), pairing({ state: "failed", code: "unreachable" }))!;
    expect(pairDialogView(d)).toMatchObject({ text: t("pair.failed.unreachable"), tone: "bad", actions: ["close"] });
    d = applyPairingEvent(openPairDialog("client", "h"), pairing({ state: "failed" }))!;
    expect(d.code).toBe("internal_error");
    d = pairCommandFailed(openPairDialog("client", "h"), "already pairing")!;
    expect(pairDialogView(d)).toMatchObject({ text: "already pairing", tone: "bad", busy: false });
  });
});

describe("pairing dialog: another computer pairs with this one (server)", () => {
  it("waits, then asks for the digits shown on the named computer", () => {
    let d = pairCommandDone(openPairDialog("server"))!;
    expect(pairDialogView(d)).toMatchObject({ text: t("pair.waitingServer"), actions: ["cancel"], digitsInput: false });
    d = applyPairingEvent(d, pairing({ role: "server", state: "enter_digits", peer_name: "A-PC" }))!;
    expect(pairDialogView(d)).toMatchObject({
      text: t("pair.enterDigits", { name: "A-PC" }),
      digitsInput: true,
      actions: ["submit", "cancel"],
      digits: null,
    });
    d = applyPairingEvent(d, pairing({ role: "server", state: "done" }))!;
    expect(pairDialogView(d).text).toBe(t("pair.doneServer", { name: "A-PC" }));
  });

  it("names an unnamed computer generically", () => {
    const d = applyPairingEvent(pairCommandDone(openPairDialog("server")), pairing({ role: "server", state: "enter_digits" }))!;
    expect(pairDialogView(d).text).toBe(t("pair.enterDigits", { name: t("pair.unknownPeer") }));
  });

  it("ignores client-only steps", () => {
    const d = pairCommandDone(openPairDialog("server"))!;
    expect(applyPairingEvent(d, pairing({ role: "server", state: "show_digits", digits: "123456" }))).toBe(d);
  });
});

describe("pairing failure texts", () => {
  it("has its own text for every failure code of the contract, in every locale", () => {
    for (const locale of ["zh-TW", "en", "ja"] as const) {
      setLocale(locale);
      const texts = PAIRING_FAILURES.map((code) => pairFailureText(code));
      for (const [i, text] of texts.entries()) {
        expect(text, PAIRING_FAILURES[i]).not.toBe(t("pair.failedOther", { code: PAIRING_FAILURES[i]! }));
        expect(text.trim()).not.toBe("");
      }
      expect(new Set(texts).size).toBe(texts.length);
    }
  });

  it("names codes it does not know (a newer engine)", () => {
    expect(pairFailureText("quantum_error")).toBe(t("pair.failedOther", { code: "quantum_error" }));
    expect(pairFailureText("toString")).toBe(t("pair.failedOther", { code: "toString" }));
    expect(pairFailureText(null)).toBe(t("pair.failedOther", { code: "?" }));
  });
});

describe("remote session status", () => {
  it("shows the reconnecting notice on running+true and clears it on false or when the session ends", () => {
    expect(reconnectNotice("running", true)).toBe("show");
    expect(reconnectNotice("running", false)).toBe("clear");
    expect(reconnectNotice("running", null)).toBe("keep");
    for (const state of ["idle", "error", "loading"] as const) expect(reconnectNotice(state, true)).toBe("clear");
  });

  it("offers to use this computer when the compute host is gone", () => {
    expect(remoteErrorActions("remote_lost")).toEqual(["use_local"]);
    expect(remoteErrorActions("remote_unreachable")).toEqual(["use_local"]);
    expect(remoteErrorActions("remote_not_paired")).toEqual(["open_settings", "use_local"]);
    expect(remoteErrorActions("remote_not_configured")).toEqual(["open_settings", "use_local"]);
    expect(remoteErrorActions("remote_model_not_downloaded")).toEqual([]);
    expect(remoteErrorActions("capture_error")).toEqual([]);
    expect(remoteErrorActions(null)).toEqual([]);
  });

  it("translates every new status error code, passing the compute host's own detail through", () => {
    for (const code of [
      "remote_not_configured",
      "remote_unreachable",
      "remote_not_paired",
      "remote_busy",
      "remote_incompatible",
      "remote_lost",
      "remote_model_not_downloaded",
      "remote_error",
      "remote_identity_error",
    ]) {
      expect(describeStatusError(code, null), code).toBe(t(`status.code.${code}` as Parameters<typeof t>[0]));
    }
    expect(describeStatusError("remote_error", "CUDA out of memory")).toBe(
      t("status.code.remote_error_detail", { detail: "CUDA out of memory" }),
    );
  });
});

describe("the rail's three roles", () => {
  it("serving decides the role unless sessions go to another computer", () => {
    expect(computeRole(remote())).toBe("local");
    expect(computeRole(remote({ mode: "remote" }))).toBe("remote");
    expect(computeRole(remote({ server_enabled: true }))).toBe("serve");
    // Only from a hand-edited file: the engine sends this computer's sessions away (it reads `mode`).
    expect(computeRole(remote({ mode: "remote", server_enabled: true }))).toBe("remote");
  });

  it("switching between local and remote saves the mode, turning serving off first", () => {
    expect(roleChange("local", remote(), null)).toEqual({ kind: "none" });
    expect(roleChange("remote", remote(), null)).toEqual({ kind: "set_mode", mode: "remote", stopServing: false });
    expect(roleChange("local", remote({ server_enabled: true }), null)).toEqual({
      kind: "set_mode",
      mode: "local",
      stopServing: true,
    });
    // The engine is serving although the settings read earlier said otherwise.
    const serving = parseRemoteStatus(statusPayload({ server: { ...(statusPayload().server as object), enabled: true } }));
    expect(roleChange("remote", remote(), serving)).toEqual({ kind: "set_mode", mode: "remote", stopServing: true });
  });

  it("serving uses the saved address, else the only one, else asks", () => {
    expect(roleChange("serve", remote({ server_enabled: true }), null)).toEqual({ kind: "none" });
    expect(roleChange("serve", remote({ listen_host: "10.0.0.5" }), null)).toEqual({
      kind: "serve",
      listenHost: "10.0.0.5",
    });
    const one = parseRemoteStatus(
      statusPayload({ server: { ...(statusPayload().server as object), listen_host: "" }, addresses: ["192.168.1.20"] }),
    );
    expect(roleChange("serve", remote(), one)).toEqual({ kind: "serve", listenHost: "192.168.1.20" });
    const two = parseRemoteStatus(statusPayload({ server: { ...(statusPayload().server as object), listen_host: "" } }));
    expect(roleChange("serve", remote(), two)).toEqual({
      kind: "choose_address",
      addresses: ["192.168.8.236", "100.88.94.10"],
    });
    const none = parseRemoteStatus(
      statusPayload({ server: { ...(statusPayload().server as object), listen_host: "" }, addresses: [] }),
    );
    expect(roleChange("serve", remote(), none)).toEqual({ kind: "no_address" });
    expect(roleChange("serve", remote(), null)).toEqual({ kind: "no_address" });
  });

  it("names where an address reaches in plain words", () => {
    expect(addressKind("100.88.94.10")).toBe("tailscale");
    expect(addressKind("100.64.0.1")).toBe("tailscale");
    expect(addressKind("100.127.255.254")).toBe("tailscale");
    expect(addressKind("100.128.0.1")).toBe("lan");
    expect(addressKind("100.63.0.1")).toBe("lan");
    expect(addressKind("192.168.8.236")).toBe("lan");
    expect(addressKind("fd7a:115c:a1e0::1")).toBe("tailscale");
    expect(addressKind("fd00::1")).toBe("lan");
    expect(addressLabel("192.168.8.236")).toBe(t("address.option", { label: t("address.lan"), address: "192.168.8.236" }));
    expect(addressLabel("100.88.94.10")).toContain("Tailscale");
  });
});

describe("rail and compute host section", () => {
  it("describes where processing runs, with the round trip while known", () => {
    expect(computeRailView(remote(), 12, null)).toEqual({
      hint: t("compute.hintLocal"),
      error: false,
      rtt: null,
      action: null,
    });
    expect(computeRailView(remote({ mode: "remote" }), null, null)).toEqual({
      hint: t("compute.hintNoHost"),
      error: false,
      rtt: null,
      action: "settings",
    });
    expect(computeRailView(remote({ mode: "remote", host: "10.0.0.2", port: 47200 }), 12.4, null)).toEqual({
      hint: t("compute.hintRemote", { host: "10.0.0.2:47200" }),
      error: false,
      rtt: t("compute.rtt", { ms: 12 }),
      action: null,
    });
  });

  it("describes serving: waiting (with pairing), working for someone, reconnecting, failing", () => {
    const serving = remote({ server_enabled: true, listen_host: "192.168.8.236" });
    const base = statusPayload().server as Record<string, unknown>;
    const at = (server: Record<string, unknown>) => parseRemoteStatus(statusPayload({ server: { ...base, ...server } }));
    expect(computeRailView(serving, null, at({ enabled: true, listening: true, peer: null }))).toEqual({
      hint: t("compute.hintServeWaiting"),
      error: false,
      rtt: null,
      action: "pair",
    });
    expect(computeRailView(serving, 5, at({ enabled: true, listening: true })).hint).toBe(
      t("compute.hintServeBusy", { name: "DESKTOP-1" }),
    );
    const waiting = at({ listening: true, peer: { name: "A-PC", fingerprint: "ffff", state: "waiting_resume" } });
    expect(computeRailView(serving, null, waiting).hint).toBe(t("compute.hintServeReconnect", { name: "A-PC" }));
    expect(computeRailView(serving, null, at({ error: "port in use", peer: null }))).toEqual({
      hint: t("compute.hintServeError", { error: "port in use" }),
      error: true,
      rtt: null,
      action: null,
    });
    expect(computeRailView(serving, null, null).hint).toBe(t("compute.hintServeOff"));
  });

  it("describes the server and the connected computer", () => {
    const s = parseRemoteStatus(statusPayload());
    expect(serverStateText(s.server)).toBe(t("serve.notListening"));
    expect(serverStateText({ ...s.server, listening: true, port: 47200 })).toBe(
      t("serve.listening", { address: "192.168.8.236:47200" }),
    );
    expect(serverStateText({ ...s.server, error: "port in use" })).toBe(t("serve.error", { error: "port in use" }));
    expect(serverPeerText(null)).toBe(t("serve.currentNone"));
    expect(serverPeerText({ name: "A-PC", fingerprint: "ffff", state: "connected" })).toBe(
      t("serve.peerConnected", { name: "A-PC" }),
    );
    expect(serverPeerText({ name: "A-PC", fingerprint: "ffff", state: "waiting_resume" })).toBe(
      t("serve.peerWaiting", { name: "A-PC" }),
    );
  });

  it("lists this computer's addresses, keeping a saved one no longer listed", () => {
    const s: RemoteStatus = parseRemoteStatus(statusPayload());
    expect(listenHostOptions(s, "")).toEqual(["192.168.8.236", "100.88.94.10"]);
    const moved = { ...s, server: { ...s.server, listen_host: "192.168.1.9" } };
    expect(listenHostOptions(moved, "")).toEqual(["192.168.1.9", "192.168.8.236", "100.88.94.10"]);
    expect(listenHostOptions(null, "10.0.0.5")).toEqual(["10.0.0.5"]);
    expect(listenHostOptions(null, "")).toEqual([]);
  });

  it("shows the pairing date locally, or the raw text when it does not parse", () => {
    expect(pairedDate("2026-11-20T03:04:05Z", "en")).toMatch(/2026/);
    expect(pairedDate("not a date", "en")).toBe("not a date");
  });
});

/** The `HostInfo` example of compute-capacity.md, with a few more models. */
function hostPayload(over: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    gpu: { name: "NVIDIA GeForce GTX 1080 Ti", vram_total_mb: 11264 },
    device: "cuda",
    compute_type: "int8",
    current: { fast: "large-v3-turbo", final: "large-v3" },
    recommended: { fast: "large-v3-turbo", final: "kotoba-whisper-v2.0" },
    models: [
      { id: "large-v3", downloaded: true, need_mb: 2140, fit: "ok" },
      { id: "kotoba-whisper-v2.0", downloaded: true, need_mb: 1600, fit: "tight" },
      { id: "huge-model", downloaded: true, need_mb: 12000, fit: "over" },
      { id: "small", downloaded: false, need_mb: 600, fit: "ok" },
    ],
    translation_model: "qwen3.5:9b",
    ...over,
  };
}

describe("compute host capacity", () => {
  it("parses a valid report", () => {
    const host = parseHostInfo(hostPayload());
    expect(host?.gpu).toEqual({ name: "NVIDIA GeForce GTX 1080 Ti", vram_total_mb: 11264 });
    expect(host?.device).toBe("cuda");
    expect(host?.models.map((m) => m.fit)).toEqual(["ok", "tight", "over", "ok"]);
    // The compute host could not measure its hardware: fit is unknown, the report is kept.
    const unknown = parseHostInfo({ ...hostPayload(), models: [{ id: "large-v3", downloaded: true, need_mb: 2140, fit: null }] });
    expect(unknown?.models[0]?.fit).toBeNull();
    expect(hostModelOptions(unknown, "").map((o) => o.label).join(" ")).toContain("無法判斷負荷");
    expect(parseHostInfoResult({ host: hostPayload() })).toEqual(host);
    const cpu = parseHostInfo(hostPayload({ gpu: null, device: "cpu", recommended: null, translation_model: null }));
    expect([cpu?.gpu, cpu?.recommended, cpu?.translation_model]).toEqual([null, null, null]);
    // 64 characters, counted like Rust (code points), not UTF-16 units.
    expect(parseHostInfo(hostPayload({ gpu: { name: "🎮".repeat(64), vram_total_mb: 1 } }))).not.toBeNull();
    expect(parseHostInfo(hostPayload({ gpu: { name: "🎮".repeat(65), vram_total_mb: 1 } }))).toBeNull();
  });

  it("drops the whole report when any field is malformed", () => {
    const bad: Record<string, unknown>[] = [
      { gpu: { name: "x".repeat(65), vram_total_mb: 1 } },
      { gpu: { name: "GPU", vram_total_mb: -1 } },
      { gpu: { name: "GPU", vram_total_mb: 2_000_000 } },
      { device: "rocm" },
      { compute_type: "INT8; rm" },
      { current: { fast: "large-v3-turbo" } },
      { current: { fast: "../x", final: "large-v3" } },
      { recommended: { fast: 1, final: "large-v3" } },
      { models: "large-v3" },
      { models: [{ id: "large-v3", downloaded: "yes", need_mb: 1, fit: "ok" }] },
      { models: [{ id: "large-v3", downloaded: true, need_mb: Number.NaN, fit: "ok" }] },
      { models: [{ id: "large-v3", downloaded: true, need_mb: 1, fit: "maybe" }] },
      { models: Array.from({ length: MAX_HOST_MODELS + 1 }, (_, i) => ({ id: `m${i}`, downloaded: true, need_mb: 1, fit: "ok" })) },
      { translation_model: "../x" },
    ];
    for (const over of bad) expect(parseHostInfo(hostPayload(over)), JSON.stringify(over)).toBeNull();
    for (const none of [null, undefined, "host", []]) expect(parseHostInfo(none)).toBeNull();
    expect(parseHostInfoResult({ host: null })).toBeNull();
    expect(parseHostInfoResult(null)).toBeNull();
  });

  it("names the graphics card briefly", () => {
    expect(shortGpuName("NVIDIA GeForce GTX 1080 Ti")).toBe("GTX 1080 Ti");
    expect(shortGpuName("NVIDIA RTX A6000")).toBe("RTX A6000");
    expect(shortGpuName("NVIDIA")).toBe("NVIDIA");
    const host = parseHostInfo(hostPayload())!;
    expect(hostSummary(host)).toBe("GTX 1080 Ti・11 GB");
    expect(hostSummary({ ...host, gpu: null, device: "cpu" })).toBe(t("host.cpu"));
  });

  it("offers the compute host's setting and its downloaded models, labelled", () => {
    const host = parseHostInfo(hostPayload())!;
    const options = hostModelOptions(host, "");
    expect(options.map((o) => o.value)).toEqual(["", "large-v3", "kotoba-whisper-v2.0", "huge-model"]);
    expect(options.map((o) => o.label)).toEqual([
      "照運算主機的設定（large-v3）",
      "large-v3（跑得動）",
      "kotoba-whisper-v2.0（建議・可能吃緊）",
      "huge-model（超出負荷）",
    ]);
    expect(options.map((o) => o.fit)).toEqual([null, "ok", "tight", "over"]);
    expect(hostHasMoreModels(host)).toBe(true);
    expect(hostHasMoreModels({ ...host, models: host.models.filter((m) => m.downloaded) })).toBe(false);
    expect(hostHasMoreModels(null)).toBe(false);
  });

  it("keeps a saved choice the compute host no longer has, marked", () => {
    const host = parseHostInfo(hostPayload())!;
    expect(hostModelOptions(host, "small").at(-1)).toEqual({ value: "small", label: "small（運算主機上沒有）", fit: null });
    expect(hostModelOptions(null, "")).toEqual([{ value: "", label: "照運算主機的設定", fit: null }]);
    expect(hostModelOptions(null, "large-v3").map((o) => o.value)).toEqual(["", "large-v3"]);
  });

  it("asks again only for a model reported as too heavy", () => {
    const host = parseHostInfo(hostPayload())!;
    expect(needsOverConfirm(host, "huge-model")).toBe(true);
    expect(needsOverConfirm(host, "kotoba-whisper-v2.0")).toBe(false);
    expect(needsOverConfirm(host, "")).toBe(false);
    expect(needsOverConfirm(null, "huge-model")).toBe(false);
  });

  it("explains a failed query with the session's words for the remote codes", () => {
    for (const code of ["remote_not_configured", "remote_unreachable", "remote_not_paired", "remote_incompatible", "remote_identity_error"]) {
      expect(hostInfoErrorText(code, "fallback")).toBe(describeStatusError(code, null));
    }
    expect(hostInfoErrorText("remote_unreachable", "x")).toContain("連不上運算主機");
    expect(hostInfoErrorText("internal_error", "fallback")).toBe("fallback");
    expect(hostInfoErrorText(null, "fallback")).toBe("fallback");
  });

  it("has every label in every interface language", () => {
    setLocale("en");
    const host = parseHostInfo(hostPayload())!;
    expect(hostModelOptions(host, "")[2]!.label).toBe("kotoba-whisper-v2.0 (recommended · May be tight)");
  });
});
