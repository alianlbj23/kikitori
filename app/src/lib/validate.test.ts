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
  parseMetrics,
  parseModelDeleteResult,
  parseModels,
  parseObsStatus,
  parseOutputInfo,
  parseOverlayNotice,
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
    const ok = {
      font_size: 32,
      background_opacity: 0.5,
      position: "bottom",
      max_lines: 2,
      min_display_ms: 0,
      idle_clear_secs: 600,
      pace_by_length: true,
      max_line_em: 13,
      original_scale_percent: 120,
      original_position: "below",
      furigana_first_only: true,
    };
    expect(parseOverlayStyle(ok)).toEqual(ok);
    expect(parseOverlayStyle({ ...ok, max_line_em: 0 }).max_line_em).toBe(0);
    expect(() => parseOverlayStyle({ ...ok, max_line_em: 5 })).toThrow();
    expect(() => parseOverlayStyle({ ...ok, original_scale_percent: 151 })).toThrow();
    expect(() => parseOverlayStyle({ ...ok, original_position: "left" })).toThrow();
    expect(() => parseOverlayStyle({ ...ok, pace_by_length: "yes" })).toThrow();
    expect(() => parseOverlayStyle({ ...ok, font_size: 200 })).toThrow();
    expect(() => parseOverlayStyle({ ...ok, background_opacity: 2 })).toThrow();
    expect(() => parseOverlayStyle({ ...ok, position: "left" })).toThrow();
    expect(() => parseOverlayStyle({ ...ok, min_display_ms: 10_001 })).toThrow();
    expect(() => parseOverlayStyle({ ...ok, idle_clear_secs: 601 })).toThrow();
    expect(() => parseOverlayStyle({ ...ok, idle_clear_secs: 1.5 })).toThrow();
  });

  it("defaults the reading rhythm when an older Rust build has no such fields", () => {
    const old = { font_size: 32, background_opacity: 0.5, position: "bottom", max_lines: 2 };
    expect(parseOverlayStyle(old)).toEqual({ ...old, min_display_ms: 1500, idle_clear_secs: 8, pace_by_length: false, max_line_em: 0, original_scale_percent: 62, original_position: "above", furigana_first_only: false });
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
      disk_bytes: 4194304,
    });
    const turbo = m.asr.find((a) => a.id === "large-v3-turbo");
    expect(turbo?.no_speech_reliable).toBe(false);
    const missing = m.asr.find((a) => a.id === "fake-missing");
    expect(missing).toMatchObject({ no_speech_reliable: null, vram_mb: null, downloaded: false, disk_bytes: null });
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

describe("furigana (docs/specs/furigana.md)", () => {
  const japanese = { ...subtitle, original: "今日は食べる" };

  it("keeps a valid ruby list and drops an invalid one without losing the subtitle", () => {
    const ruby = [
      [0, 2, "きょう"],
      [3, 4, "た"],
    ];
    expect(parseSubtitle({ ...japanese, ruby }).ruby).toEqual(ruby);
    expect(parseSubtitle({ ...japanese, ruby: [] }).ruby).toEqual([]);
    expect(parseSubtitle(japanese).ruby).toBeNull();
    for (const bad of [
      "x",
      [[0, 2]],
      [[0, 2, 3]],
      [[0, 9, "きょう"]],
      [
        [3, 4, "た"],
        [0, 2, "きょう"],
      ],
      [[0, 2, "キョウ"]],
      [[0, 2, "<b>"]],
    ]) {
      const parsed = parseSubtitle({ ...japanese, ruby: bad });
      expect(parsed.ruby, JSON.stringify(bad)).toBeNull();
      expect(parsed.original).toBe("今日は食べる");
    }
  });

  it("recognises the dictionary download kind", () => {
    const p = parseDownloadProgress({
      model: "furigana-dictionary",
      kind: "furigana",
      downloaded_bytes: 0,
      total_bytes: 72238136,
      state: "downloading",
      error: null,
    });
    expect(p.kind).toBe("furigana");
  });

  it("reads models.list.furigana and tolerates engines without it", () => {
    const furigana = {
      id: "furigana-dictionary",
      installed: true,
      edition: "core",
      version: "20260428",
      download_bytes: 72238136,
      installed_bytes: 217391698,
      disk_bytes: 217392119,
      path: "C:/fake/furigana/system_core.dic",
      license: "Apache-2.0",
      license_url: "https://example.invalid/LEGAL",
      in_use: false,
    };
    const list = engine["models.list"];
    expect(parseModels({ ...list, furigana }).furigana).toEqual(furigana);
    expect(parseModels(list).furigana).toBeNull();
    expect(() => parseModels({ ...list, furigana: { ...furigana, installed: "yes" } })).toThrow(ValidationError);
  });
});

describe("competitive features: subtitle hints, metrics, model deletion", () => {
  it("keeps translation_stable_chars on partials only (G1)", () => {
    const partial = { ...subtitle, is_final: false, translation_stable_chars: 3 };
    expect(parseSubtitle(partial)).toMatchObject({ translation_stable_chars: 3 });
    expect(parseSubtitle({ ...subtitle, translation_stable_chars: 3 })).toMatchObject({
      translation_stable_chars: null,
    });
    expect(parseSubtitle({ ...subtitle, is_final: false })).toMatchObject({ translation_stable_chars: null });
    expect(() => parseSubtitle({ ...partial, translation_stable_chars: -1 })).toThrow(ValidationError);
  });

  it("keeps a known speaker and drops anything else (G3)", () => {
    expect(parseSubtitle({ ...subtitle, speaker: "self" }).speaker).toBe("self");
    expect(parseSubtitle({ ...subtitle, speaker: "partner" }).speaker).toBe("partner");
    expect(parseSubtitle({ ...subtitle, speaker: "narrator" }).speaker).toBeNull();
    expect(parseSubtitle({ ...subtitle, speaker: 1 }).speaker).toBeNull();
    expect(parseSubtitle(subtitle).speaker).toBeNull();
  });

  it("keeps stable_chars on partials and latency_ms on finals only", () => {
    const partial = { ...subtitle, is_final: false, stable_chars: 2, latency_ms: 900 };
    expect(parseSubtitle(partial)).toMatchObject({ stable_chars: 2, latency_ms: null });
    const final = { ...subtitle, stable_chars: 2, latency_ms: 900 };
    expect(parseSubtitle(final)).toMatchObject({ stable_chars: null, latency_ms: 900 });
    // Older engines send neither.
    expect(parseSubtitle({ ...subtitle, is_final: false })).toMatchObject({ stable_chars: null, latency_ms: null });
    expect(parseSubtitle({ ...subtitle, latency_ms: null }).latency_ms).toBeNull();
    expect(() => parseSubtitle({ ...partial, stable_chars: -1 })).toThrow(ValidationError);
    expect(() => parseSubtitle({ ...partial, stable_chars: "2" })).toThrow(ValidationError);
    expect(() => parseSubtitle({ ...final, latency_ms: 1.5 })).toThrow(ValidationError);
  });

  it("parses metrics and clamps the level", () => {
    expect(parseMetrics({ session_id: 3, level_dbfs: -20.5, backlog: 1, latency_ms_median: 800 })).toEqual({
      session_id: 3,
      level_dbfs: -20.5,
      backlog: 1,
      latency_ms_median: 800,
      speech_level_dbfs: null,
      background_level_dbfs: null,
    });
    // G8 split levels: kept when in range, otherwise unknown (the event itself is fine).
    const split = parseMetrics({ level_dbfs: -20, backlog: 0, speech_level_dbfs: -18, background_level_dbfs: 3 });
    expect([split.speech_level_dbfs, split.background_level_dbfs]).toEqual([-18, null]);
    expect(parseMetrics({ level_dbfs: -200, backlog: 0 })).toEqual({
      session_id: null,
      level_dbfs: -120,
      backlog: 0,
      latency_ms_median: null,
      speech_level_dbfs: null,
      background_level_dbfs: null,
    });
    expect(parseMetrics({ level_dbfs: 4, backlog: 0 }).level_dbfs).toBe(0);
    for (const bad of [
      { backlog: 0 },
      { level_dbfs: "-3", backlog: 0 },
      { level_dbfs: -3, backlog: -1 },
      { level_dbfs: -3, backlog: 0, latency_ms_median: "9" },
      null,
    ]) {
      expect(() => parseMetrics(bad), JSON.stringify(bad)).toThrow(ValidationError);
    }
  });

  it("reads disk_bytes when present and null otherwise", () => {
    const list = clone(engine["models.list"]) as { asr: Record<string, unknown>[] };
    list.asr[0]!.disk_bytes = 150_000_000;
    delete list.asr[1]!.disk_bytes;
    const m = parseModels(list);
    expect(m.asr[0]!.disk_bytes).toBe(150_000_000);
    expect(m.asr[1]!.disk_bytes).toBeNull();
    list.asr[0]!.disk_bytes = -1;
    expect(() => parseModels(list)).toThrow(ValidationError);
  });

  it("parses the models.delete result", () => {
    expect(parseModelDeleteResult({ model: "small", freed_bytes: 484_000_000 })).toEqual({
      model: "small",
      freed_bytes: 484_000_000,
    });
    expect(() => parseModelDeleteResult({ model: "small" })).toThrow(ValidationError);
    expect(() => parseModelDeleteResult({ model: 1, freed_bytes: 0 })).toThrow(ValidationError);
  });
});

describe("parseOverlayNotice (G3)", () => {
  it("accepts a short sentence with its language", () => {
    expect(parseOverlayNotice({ text: "もう一度？", lang: "ja" })).toEqual({ text: "もう一度？", lang: "ja" });
  });

  it("rejects empty, overlong or untagged notices", () => {
    expect(() => parseOverlayNotice({ text: " ", lang: "ja" })).toThrow(ValidationError);
    expect(() => parseOverlayNotice({ text: "あ".repeat(201), lang: "ja" })).toThrow(ValidationError);
    expect(() => parseOverlayNotice({ text: "hi", lang: "not a tag" })).toThrow(ValidationError);
    expect(() => parseOverlayNotice({ text: 1, lang: "ja" })).toThrow(ValidationError);
    expect(() => parseOverlayNotice(null)).toThrow(ValidationError);
  });
});

describe("speaker labels (docs/specs/speaker-labels.md)", () => {
  it("keeps voice 1-64 on finals and drops it elsewhere without losing the line", () => {
    expect(parseSubtitle({ ...subtitle, voice: 2 }).voice).toBe(2);
    expect(parseSubtitle({ ...subtitle, voice: 64 }).voice).toBe(64);
    for (const bad of [0, 65, 1.5, "2", -1, null]) {
      expect(parseSubtitle({ ...subtitle, voice: bad }).voice).toBeNull();
    }
    expect(parseSubtitle({ ...subtitle, is_final: false, voice: 2 }).voice).toBeNull();
    expect(parseSubtitle(subtitle).voice).toBeNull();
  });

  it("reads models.list.speaker_model and tolerates engines without it", () => {
    const speaker_model = {
      id: "speaker-embedding",
      name: "3D-Speaker CAM++",
      installed: false,
      download_bytes: 28281164,
      installed_bytes: 28281164,
      disk_bytes: null,
      path: null,
      license: "Apache-2.0",
      license_url: "https://example.invalid/model",
    };
    const list = engine["models.list"];
    expect(parseModels({ ...list, speaker_model }).speaker_model).toEqual(speaker_model);
    expect(parseModels(list).speaker_model).toBeNull();
    expect(() => parseModels({ ...list, speaker_model: { ...speaker_model, installed: 1 } })).toThrow(ValidationError);
  });

  it("reads overlay speaker names, blanking bad entries", () => {
    const snap = {
      style: { font_size: 32, background_opacity: 0.5, position: "bottom", max_lines: 2 },
      click_through: false,
      visible: true,
      shortcut: "CommandOrControl+Alt+K",
      shortcut_registered: true,
    };
    expect(parseOverlaySnapshot(snap).speaker_names).toEqual([]);
    expect(parseOverlaySnapshot({ ...snap, speaker_names: ["ぺこら", ""] }).speaker_names).toEqual(["ぺこら", ""]);
    expect(parseOverlaySnapshot({ ...snap, speaker_names: [1, "あ".repeat(41), "ok"] }).speaker_names).toEqual(["", "", "ok"]);
    expect(parseOverlaySnapshot({ ...snap, speaker_names: Array(17).fill("x") }).speaker_names).toEqual([]);
  });
});
