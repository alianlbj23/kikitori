import { describe, expect, it } from "vitest";

import {
  ValidationError,
  asCommandError,
  parseBridgeStatus,
  parseDevices,
  parseDownloadProgress,
  parseHardware,
  parseModels,
  parseOverlayStyle,
  parseStatus,
  parseSubtitle,
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
    });
    expect(() => parseStatus({ state: "napping" })).toThrow(ValidationError);
  });

  it("parses download progress, defaulting state", () => {
    expect(parseDownloadProgress({ model: "base", downloaded_bytes: 1, total_bytes: 2 }).state).toBe("downloading");
    expect(() => parseDownloadProgress({ model: "base", downloaded_bytes: -1, total_bytes: 2 })).toThrow();
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

  it("parses hardware with a null recommendation", () => {
    const h = parseHardware({
      hardware: { os: "windows", os_version: "10", machine: "AMD64", cpu_count: 8, ram_bytes: null, gpus: [], apple_silicon: false },
      recommendation: null,
    });
    expect(h.recommendation).toBeNull();
    expect(h.hardware.ram_bytes).toBeNull();
  });

  it("parses models and keeps translation errors", () => {
    const m = parseModels({
      asr: [{ id: "base", size_bytes: 1, license: "MIT", speed_tier: "fast", downloaded: false }],
      translation: { base_url: "http://x", installed: null, error: "URLError", presets: [] },
    });
    expect(m.translation.installed).toBeNull();
    expect(m.translation.error).toBe("URLError");
  });

  it("validates overlay style ranges", () => {
    const ok = { font_size: 32, background_opacity: 0.5, position: "bottom", max_lines: 2 };
    expect(parseOverlayStyle(ok)).toEqual(ok);
    expect(() => parseOverlayStyle({ ...ok, font_size: 200 })).toThrow();
    expect(() => parseOverlayStyle({ ...ok, background_opacity: 2 })).toThrow();
    expect(() => parseOverlayStyle({ ...ok, position: "left" })).toThrow();
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
});

describe("hardware.md shapes", () => {
  it("parses the structured recommendation and memory_total_mb", () => {
    const h = parseHardware({
      hardware: {
        os: "windows",
        cpu_count: 16,
        ram_bytes: 1,
        gpus: [{ index: 0, name: "RTX", memory_total_mb: 1024 }],
        apple_silicon: false,
      },
      recommendation: {
        tier: "nvidia_8gb",
        fast_asr: { model: "small", device: "cuda", compute_type: "float16", cpu_threads: 0 },
        final_asr: { model: "large-v3-turbo", device: "cuda", compute_type: "float16", cpu_threads: 0 },
        translation_fast: null,
        translation_final: null,
        gpu_runtime_missing: false,
        reasons: ["r1"],
        warnings: [],
      },
    });
    expect(h.hardware.gpus[0].vram_bytes).toBe(1024 * 1024 * 1024);
    expect(h.recommendation?.fast_asr?.model).toBe("small");
    expect(h.recommendation?.translation_fast).toBeNull();
    expect(h.recommendation?.reasons).toEqual(["r1"]);
  });

  it("parses models.list with translation.server/models and gpu_runtime", () => {
    const m = parseModels({
      asr: [{ id: "small", size_bytes: 1, license: "MIT", speed_tier: "medium", downloaded: true, recommended_for: ["fast"] }],
      gpu_runtime: { id: "gpu-runtime", supported: true, installed: false, download_bytes: 5, license: "EULA", license_url: "https://x" },
      translation: {
        server: { base_url: "http://localhost:11434/v1", is_local: true, reachable: true, error: null },
        models: [
          { name: "qwen3.5:9b", label: "Qwen3.5 9B", installed: true, known: true, license: "Apache-2.0", license_url: null, commercial_use: true, excluded_regions: [] },
          { name: "mystery", installed: false, known: false, license: null, license_url: null, commercial_use: null, excluded_regions: null },
        ],
      },
    });
    expect(m.asr[0].recommended_for).toEqual(["fast"]);
    expect(m.gpu_runtime?.installed).toBe(false);
    expect(m.translation.base_url).toBe("http://localhost:11434/v1");
    expect(m.translation.installed).toEqual(["qwen3.5:9b"]);
    expect(m.translation.presets[1]).toMatchObject({ model: "mystery", label: "mystery", license: "未知", commercial_use: false });
  });

  it("reports unreachable servers as unknown installation", () => {
    const m = parseModels({
      asr: [],
      translation: {
        server: { base_url: "http://h", reachable: false, error: "URLError" },
        models: [{ name: "a", installed: null }],
      },
    });
    expect(m.translation.installed).toBeNull();
    expect(m.translation.error).toBe("URLError");
  });
});

