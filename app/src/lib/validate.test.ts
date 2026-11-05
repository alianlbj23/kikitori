import { describe, expect, it } from "vitest";

import engine from "./testdata/engine-fake-nvidia.json";
import {
  ValidationError,
  asCommandError,
  parseAbout,
  parseBridgeStatus,
  parseCombinationCheck,
  parseDevices,
  parseDownloadProgress,
  parseHardware,
  parseModels,
  parseObsStatus,
  parseOutputInfo,
  parseOverlaySnapshot,
  parseOverlayStyle,
  parseRecommendationPreview,
  parseSavedPath,
  parseShortcutAction,
  parseStatus,
  parseSubtitle,
  parseUpdateDownloadEvent,
  parseUpdateInfo,
} from "./validate";

const subtitle = {
  utterance_id: 1,
  original: "こんにちは",
  translation: "你好",
  source_language: "ja",
  target_language: "zh-TW",
  is_final: true,
  start_sample: 0,
  end_sample: 16000,
  session_id: 2,
};

describe("parseSubtitle", () => {
  it("accepts a valid payload and defaults a missing session id", () => {
    expect(parseSubtitle(subtitle).session_id).toBe(2);
    const { session_id: _omit, ...rest } = subtitle;
    expect(parseSubtitle(rest).session_id).toBeNull();
  });

  it.each([
    ["utterance_id", -1],
    ["utterance_id", 1.5],
    ["is_final", "true"],
    ["original", 5],
    ["source_language", "ja\"><img>"],
    ["end_sample", -3],
  ])("rejects bad %s", (field, value) => {
    expect(() => parseSubtitle({ ...subtitle, [field]: value })).toThrow(ValidationError);
  });

  it("rejects end before start and non-objects", () => {
    expect(() => parseSubtitle({ ...subtitle, start_sample: 20000 })).toThrow(ValidationError);
    expect(() => parseSubtitle(null)).toThrow(ValidationError);
    expect(() => parseSubtitle([subtitle])).toThrow(ValidationError);
  });
});

describe("other events", () => {
  it("parses status with optional fields", () => {
    expect(parseStatus({ state: "running" })).toEqual({
      state: "running",
      detail: null,
      session_id: null,
      code: null,
      missing_translation_models: [],
    });
    expect(() => parseStatus({ state: "napping" })).toThrow(ValidationError);
  });

  it("reads the missing translation models of translation_model_missing (real engine capture)", () => {
    const events = engine["status events"] as unknown[];
    const missing = parseStatus(events[events.length - 1]);
    expect(missing.code).toBe("translation_model_missing");
    expect(missing.missing_translation_models).toEqual(["qwen3.5:9b"]);
    // Names the engine could not pull are dropped; other codes never carry models.
    expect(
      parseStatus({ state: "error", code: "translation_model_missing", data: { models: ["ok:1b", "../x", "a b", 3, "ok:1b"] } })
        .missing_translation_models,
    ).toEqual(["ok:1b"]);
    expect(parseStatus({ state: "error", code: "load_error", data: { models: ["x"] } }).missing_translation_models).toEqual([]);
    expect(parseStatus({ state: "error", code: "translation_model_missing", data: [1] }).missing_translation_models).toEqual([]);
  });

  it("reads download_progress.kind, treating unknown kinds as none", () => {
    const events = engine["download_progress events"] as unknown[];
    const parsed = events.map(parseDownloadProgress);
    expect(parsed.every((p) => p.kind === "translation")).toBe(true);
    expect(parsed[parsed.length - 1]).toMatchObject({ model: "qwen3.5:9b", state: "done" });
    expect(parseDownloadProgress({ model: "base", downloaded_bytes: 0, total_bytes: 0 }).kind).toBeNull();
    expect(parseDownloadProgress({ model: "base", kind: "future", downloaded_bytes: 0, total_bytes: 0 }).kind).toBeNull();
  });

  it("parses download progress, defaulting state", () => {
    expect(parseDownloadProgress({ model: "base", downloaded_bytes: 1, total_bytes: 2 }).state).toBe("downloading");
    expect(() => parseDownloadProgress({ model: "base", downloaded_bytes: -1, total_bytes: 2 })).toThrow();
  });

  it("parses GPU runtime progress with state and error", () => {
    const failed = parseDownloadProgress({
      model: "gpu-runtime",
      downloaded_bytes: 10,
      total_bytes: 20,
      state: "error",
      error: "DownloadCancelledError: cancelled",
    });
    expect(failed).toMatchObject({ model: "gpu-runtime", state: "error", error: "DownloadCancelledError: cancelled" });
    expect(() =>
      parseDownloadProgress({ model: "gpu-runtime", downloaded_bytes: 0, total_bytes: 0, state: "paused" }),
    ).toThrow(ValidationError);
  });

  it("parses bridge status", () => {
    const s = parseBridgeStatus({
      phase: "ready",
      detail: null,
      restarts: 0,
      command: "uv run kikitori engine",
      source: "dev_uv",
      info: { version: "0.1.0", protocol_version: 1, platform: "windows", fake: true },
    });
    expect(s.info?.fake).toBe(true);
    expect(() => parseBridgeStatus({ phase: "bored", restarts: 0 })).toThrow();
  });
});

describe("command results", () => {
  it("accepts both device list shapes", () => {
    const d = { id: "x", name: "喇叭", kind: "loopback", is_default: true };
    expect(parseDevices({ devices: [d] })).toHaveLength(1);
    expect(parseDevices([d])).toHaveLength(1);
    expect(() => parseDevices({ devices: [{ ...d, kind: "camera" }] })).toThrow();
  });

  it("validates overlay style ranges", () => {
    const ok = { font_size: 32, background_opacity: 0.5, position: "bottom", max_lines: 2, min_display_ms: 0, idle_clear_secs: 600 };
    expect(parseOverlayStyle(ok)).toEqual(ok);
    expect(() => parseOverlayStyle({ ...ok, font_size: 200 })).toThrow();
    expect(() => parseOverlayStyle({ ...ok, background_opacity: 2 })).toThrow();
    expect(() => parseOverlayStyle({ ...ok, position: "left" })).toThrow();
    expect(() => parseOverlayStyle({ ...ok, min_display_ms: 10_001 })).toThrow();
    expect(() => parseOverlayStyle({ ...ok, idle_clear_secs: 601 })).toThrow();
    expect(() => parseOverlayStyle({ ...ok, idle_clear_secs: 1.5 })).toThrow();
  });

  it("defaults the reading rhythm when an older Rust build has no such fields", () => {
    const old = { font_size: 32, background_opacity: 0.5, position: "bottom", max_lines: 2 };
    expect(parseOverlayStyle(old)).toEqual({ ...old, min_display_ms: 1500, idle_clear_secs: 8 });
  });

  it("reads the shortcut list, dropping unknown actions, and the OBS and output results", () => {
    const snap = {
      style: { font_size: 32, background_opacity: 0.5, position: "bottom", max_lines: 2 },
      click_through: false,
      visible: true,
      shortcut: "CommandOrControl+Alt+K",
      shortcut_registered: true,
      shortcuts: [
        { action: "click_through", keys: "CommandOrControl+Alt+K", registered: true },
        { action: "toggle_translation", keys: "CommandOrControl+Alt+J", registered: false },
        { action: "future_action", keys: "X", registered: true },
      ],
    };
    expect(parseOverlaySnapshot(snap).shortcuts.map((s) => s.action)).toEqual(["click_through", "toggle_translation"]);
    expect(parseOverlaySnapshot({ ...snap, shortcuts: undefined }).shortcuts).toEqual([]);
    expect(() => parseOverlaySnapshot({ ...snap, shortcuts: [{ action: "clear_subtitles", keys: 1, registered: true }] })).toThrow();
    expect(parseShortcutAction("toggle_translation")).toBe("toggle_translation");
    expect(() => parseShortcutAction("rm -rf")).toThrow();
    expect(parseObsStatus({ content: "both", path: "C:/Docs/kikitori/obs/subtitle.txt", error: null })).toEqual({
      content: "both",
      path: "C:/Docs/kikitori/obs/subtitle.txt",
      error: null,
    });
    expect(() => parseObsStatus({ content: "all", path: null, error: null })).toThrow();
    expect(parseOutputInfo({ transcripts_dir: null, obs_file: null })).toEqual({ transcripts_dir: null, obs_file: null });
    expect(parseSavedPath("C:/Docs/kikitori/transcripts/a.srt")).toBe("C:/Docs/kikitori/transcripts/a.srt");
    expect(() => parseSavedPath("")).toThrow();
  });

  it("reads the overlay locale, tolerating older Rust builds and malformed tags", () => {
    const snap = {
      style: { font_size: 32, background_opacity: 0.5, position: "bottom", max_lines: 2 },
      click_through: false,
      visible: true,
      shortcut: "CommandOrControl+Alt+K",
      shortcut_registered: true,
    };
    expect(parseOverlaySnapshot({ ...snap, locale: "pt-BR" }).locale).toBe("pt-BR");
    expect(parseOverlaySnapshot({ ...snap, locale: null }).locale).toBeNull();
    expect(parseOverlaySnapshot(snap).locale).toBeNull();
    // Older Rust builds have no show_translation: translations were always shown.
    expect(parseOverlaySnapshot(snap).show_translation).toBe(true);
    expect(parseOverlaySnapshot({ ...snap, show_translation: false }).show_translation).toBe(false);
    expect(parseOverlaySnapshot({ ...snap, locale: "<script>" }).locale).toBeNull();
  });

  it("recognises serialized command errors only", () => {
    expect(asCommandError({ kind: "timeout", method: "x", timeout_ms: 10 })).toEqual({
      kind: "timeout",
      method: "x",
      timeout_ms: 10,
    });
    expect(asCommandError({ kind: "engine", code: 1 })).toBeNull();
    expect(asCommandError("boom")).toBeNull();
  });

  it("parses app_about", () => {
    expect(parseAbout({ license: "MIT", app_licenses: null, engine_licenses: null })).toEqual({
      license: "MIT",
      app_licenses: null,
      engine_licenses: null,
    });
    expect(
      parseAbout({
        license: "MIT",
        app_licenses: "C:/k/licenses",
        engine_licenses: "C:/k/kikitori-engine/THIRD_PARTY_LICENSES",
      }),
    ).toEqual({
      license: "MIT",
      app_licenses: "C:/k/licenses",
      engine_licenses: "C:/k/kikitori-engine/THIRD_PARTY_LICENSES",
    });
    // A release built without one of the folders still parses.
    expect(parseAbout({ license: "MIT", app_licenses: "/usr/lib/kikitori/licenses" }).engine_licenses).toBeNull();
    expect(() => parseAbout({ app_licenses: null })).toThrow(ValidationError);
    expect(() => parseAbout({ license: "MIT", app_licenses: 3 })).toThrow(ValidationError);
    expect(() => parseAbout({ license: "MIT", engine_licenses: ["x"] })).toThrow(ValidationError);
  });
});

/** Deep copy so each test can break one field of the captured engine output. */
const clone = <T>(v: T): T => JSON.parse(JSON.stringify(v)) as T;

describe("hardware.detect (real engine, fake NVIDIA hardware)", () => {
  const detect = engine["hardware.detect"];

  it("reads GPUs from memory_total_mb, not the old vram_bytes", () => {
    const h = parseHardware(detect);
    expect(h.hardware.gpus[0]).toEqual({
      index: 0,
      name: "Fake GeForce 8GB",
      memory_total_mb: 8188,
      memory_used_mb: 600,
      driver_version: "560.94",
      compute_capability: "8.9",
    });
    expect(h.hardware.cpu.physical_cores).toBe(8);
    expect(h.hardware.memory.total_bytes).toBe(34359738368);
    expect(h.hardware.cublas).toMatchObject({ supported_platform: true, available: false, source: null });
    expect(h.hardware.translation_server?.models).toEqual(["qwen3.5:4b"]);
    expect(h.hardware.errors).toEqual([]);
  });

  it("parses the full nested recommendation", () => {
    const r = parseHardware(detect).recommendation;
    expect(r.tier).toBe("cpu");
    expect(r.fast_asr).toEqual({ model: "base", device: "cpu", compute_type: "int8", cpu_threads: 4 });
    expect(r.final_asr.model).toBe("small");
    expect(r.translation_fast).toBe("qwen3.5:4b");
    expect(r.translation_budget_gb).toBe(7);
    expect(r.gpu_runtime_missing).toBe(true);
    expect(r.reasons.length).toBeGreaterThan(0);
    // Coded messages since Milestone 12 (hardware.md「訊息碼」).
    expect(r.warnings[0]).toMatchObject({ code: "gpu_runtime_missing", params: {} });
    expect(r.translation_status).toBe("recommended");
    expect(parseHardware({ ...detect, recommendation: { ...detect.recommendation, translation_status: "odd" } }).recommendation.translation_status).toBeNull();
  });

  it("accepts coded reasons and warnings from newer engines next to plain strings", () => {
    const v = clone(detect);
    const coded = { code: "gpu_runtime_missing", params: { name: "RTX" }, message: "Download the GPU runtime." };
    (v.recommendation as Record<string, unknown>).reasons = ["plain", coded];
    (v.recommendation as Record<string, unknown>).warnings = [coded, { code: 3 }];
    const r = parseHardware(v).recommendation;
    expect(r.reasons).toEqual(["plain", coded]);
    expect(r.warnings).toEqual([coded]);
  });

  it("accepts a CUDA recommendation without translation (remote server)", () => {
    const v = clone(detect);
    v.recommendation.fast_asr = { model: "small", device: "cuda", compute_type: "float16", cpu_threads: 0 };
    (v.recommendation as Record<string, unknown>).translation_fast = null;
    (v.recommendation as Record<string, unknown>).translation_budget_gb = null;
    const r = parseHardware(v).recommendation;
    expect(r.fast_asr.cpu_threads).toBe(0);
    expect(r.translation_fast).toBeNull();
    expect(r.translation_budget_gb).toBeNull();
  });

  it.each([
    ["a GPU without memory_total_mb", (v: Detect) => delete (v.hardware.gpus[0] as Partial<Gpu>).memory_total_mb],
    ["a missing recommendation", (v: Detect) => ((v as Record<string, unknown>).recommendation = null)],
    ["a missing final_asr", (v: Detect) => ((v.recommendation as Record<string, unknown>).final_asr = null)],
    ["a string thread count", (v: Detect) => ((v.recommendation.fast_asr as Record<string, unknown>).cpu_threads = "4")],
    ["a negative budget", (v: Detect) => (v.recommendation.translation_budget_gb = -1)],
    ["a non-boolean gpu_runtime_missing", (v: Detect) => ((v.recommendation as Record<string, unknown>).gpu_runtime_missing = "yes")],
  ])("rejects %s", (_name, breakIt) => {
    const v = clone(detect);
    breakIt(v);
    expect(() => parseHardware(v)).toThrow(ValidationError);
  });
});

type Detect = (typeof engine)["hardware.detect"];
type Gpu = Detect["hardware"]["gpus"][number];

describe("models.list (real engine, fake NVIDIA hardware)", () => {
  const list = engine["models.list"];

  it("parses ASR entries with the new fields, including unknown ones", () => {
    const m = parseModels(list);
    expect(m.asr[0]).toEqual({
      id: "base",
      size_bytes: 4194304,
      downloaded: true,
      license: "MIT",
      speed_tier: "fast",
      no_speech_reliable: true,
      cpu_ram_mb: 348,
      vram_mb: 600,
      vram_int8_mb: 450,
      recommended_for: ["fast"],
    });
    const turbo = m.asr.find((a) => a.id === "large-v3-turbo");
    expect(turbo?.no_speech_reliable).toBe(false);
    const missing = m.asr.find((a) => a.id === "fake-missing");
    expect(missing).toMatchObject({ no_speech_reliable: null, vram_mb: null, downloaded: false });
  });

  it("parses the GPU runtime with its license", () => {
    const rt = parseModels(list).gpu_runtime;
    expect(rt).toMatchObject({
      id: "gpu-runtime",
      supported: true,
      installed: false,
      package: "nvidia-cublas-cu12",
      license_url: "https://docs.nvidia.com/cuda/eula/index.html",
      cublas_available: false,
    });
    expect(rt.download_bytes).toBeGreaterThan(0);
  });

  it("parses translation models with license, commercial use and regions", () => {
    const t = parseModels(list).translation;
    expect(t.server).toEqual({ base_url: "http://localhost:11434/v1", is_local: true, reachable: true, error: null });
    const hy = t.models.find((x) => x.name.startsWith("hy-mt1.5-7b"));
    expect(hy?.excluded_regions).toEqual(["EU", "UK", "KR"]);
    const sakura = t.models.find((x) => x.label.startsWith("Sakura"));
    expect(sakura?.commercial_use).toBe(false);
    const qwen = t.models.find((x) => x.name === "qwen3.5:4b");
    expect(qwen).toMatchObject({ installed: true, known: true, recommended_for: ["fast", "final"], approx_vram_gb: 3.3 });
  });

  it("treats unknown server models and an unreachable server as unknown, not wrong", () => {
    const v = clone(list) as unknown as Record<string, Record<string, unknown>>;
    v.translation.server = { base_url: "http://h/v1", is_local: false, reachable: false, error: "URLError" };
    v.translation.models = [
      { name: "mystery", installed: null, known: false, recommended_for: [], license: null, license_url: null,
        commercial_use: null, excluded_regions: [], approx_vram_gb: null },
    ];
    const t = parseModels(v).translation;
    expect(t.server.error).toBe("URLError");
    expect(t.models[0]).toMatchObject({ label: "mystery", installed: null, known: false, commercial_use: null });
  });

  it("rejects missing gpu_runtime and malformed entries", () => {
    const noRuntime = clone(list) as unknown as Record<string, unknown>;
    delete noRuntime.gpu_runtime;
    expect(() => parseModels(noRuntime)).toThrow(ValidationError);
    const badAsr = clone(list);
    (badAsr.asr[0] as Record<string, unknown>).downloaded = "yes";
    expect(() => parseModels(badAsr)).toThrow(ValidationError);
    const badRegion = clone(list);
    (badRegion.translation.models[0] as Record<string, unknown>).approx_vram_gb = "big";
    expect(() => parseModels(badRegion)).toThrow(ValidationError);
  });
});

describe("models.validate", () => {
  it("parses the check result", () => {
    expect(parseCombinationCheck({ ok: true, errors: [], warnings: ["w"] })).toEqual({
      ok: true,
      errors: [],
      warnings: ["w"],
    });
    expect(parseCombinationCheck({ ok: false, errors: ["e"], warnings: [] }).ok).toBe(false);
  });

  it("keeps coded engine messages for translation in the UI", () => {
    const coded = { code: "vram_tight", params: { gb: 4 }, message: "Video memory is tight." };
    const check = parseCombinationCheck({ ok: true, errors: [], warnings: ["legacy", coded, 7] });
    expect(check.warnings).toEqual(["legacy", coded]);
  });

  it("never reports ok next to an error", () => {
    expect(parseCombinationCheck({ ok: true, errors: ["e"], warnings: [] }).ok).toBe(false);
  });

  it("rejects a missing ok flag", () => {
    expect(() => parseCombinationCheck({ errors: [] })).toThrow(ValidationError);
  });
});

describe("settings.preview_recommendation (real engine, fake NVIDIA hardware)", () => {
  it("parses the patch, recommendation, differs and fingerprint", () => {
    const p = parseRecommendationPreview(engine["settings.preview_recommendation"]);
    expect(p.differs).toBe(true);
    expect(p.fingerprint).toMatch(/^[0-9a-f]{16}$/);
    expect(p.patch).toEqual(engine["settings.apply_recommendation"].applied);
    expect(p.recommendation.tier).toBe("cpu");
  });

  it("rejects a missing differs flag, a malformed fingerprint or patch", () => {
    const good = engine["settings.preview_recommendation"];
    const { differs: _d, ...noDiffers } = good;
    expect(() => parseRecommendationPreview(noDiffers)).toThrow(ValidationError);
    expect(() => parseRecommendationPreview({ ...good, fingerprint: "<script>" })).toThrow(ValidationError);
    expect(() => parseRecommendationPreview({ ...good, patch: [] })).toThrow(ValidationError);
  });
});

describe("update_check / update_install", () => {
  it("parses a found update and no update", () => {
    expect(parseUpdateInfo({ version: "0.2.0", currentVersion: "0.1.0", notes: "kikitori v0.2.0" })).toEqual({
      version: "0.2.0",
      currentVersion: "0.1.0",
      notes: "kikitori v0.2.0",
    });
    expect(parseUpdateInfo({ version: "0.2.0", currentVersion: "0.1.0", notes: null })?.notes).toBeNull();
    expect(parseUpdateInfo(null)).toBeNull();
  });

  it("rejects a malformed update", () => {
    expect(() => parseUpdateInfo({ currentVersion: "0.1.0" })).toThrow(ValidationError);
    expect(() => parseUpdateInfo("0.2.0")).toThrow(ValidationError);
    expect(() => parseUpdateInfo({ version: "x".repeat(101), currentVersion: "0.1.0" })).toThrow(ValidationError);
  });

  it("parses download progress events", () => {
    expect(parseUpdateDownloadEvent({ event: "Started", data: { contentLength: 42 } })).toEqual({
      event: "Started",
      contentLength: 42,
    });
    expect(parseUpdateDownloadEvent({ event: "Started", data: { contentLength: null } })).toEqual({
      event: "Started",
      contentLength: null,
    });
    expect(parseUpdateDownloadEvent({ event: "Progress", data: { chunkLength: 7 } })).toEqual({
      event: "Progress",
      chunkLength: 7,
    });
    expect(parseUpdateDownloadEvent({ event: "Finished" })).toEqual({ event: "Finished" });
  });

  it("rejects unknown or malformed progress events", () => {
    expect(() => parseUpdateDownloadEvent({ event: "Paused" })).toThrow(ValidationError);
    expect(() => parseUpdateDownloadEvent({ event: "Progress", data: { chunkLength: -1 } })).toThrow(ValidationError);
    expect(() => parseUpdateDownloadEvent({ event: "Progress" })).toThrow(ValidationError);
  });
});
