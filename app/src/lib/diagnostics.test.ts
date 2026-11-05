import { describe, expect, it } from "vitest";

import { MAX_RECENT_LOG, RecentLog, buildDiagnostics, redactSettings, redactText, type DiagnosticsInput } from "./diagnostics";
import { DEFAULT_SETTINGS } from "./settings";
import type { HardwareInfo, Settings } from "./types";

const HARDWARE: HardwareInfo = {
  os: "Windows",
  os_version: "11 (26200)",
  machine: "AMD64",
  cpu: { model: "AMD Ryzen 7", physical_cores: 8, logical_cores: 16, architecture: "x86_64" },
  cpu_count: 16,
  memory: { total_bytes: 32 * 1024 ** 3, available_bytes: 16 * 1024 ** 3 },
  gpus: [
    { index: 0, name: "NVIDIA GeForce RTX 5070", memory_total_mb: 12227, memory_used_mb: null, driver_version: "576.02", compute_capability: "12.0" },
  ],
  apple_silicon: false,
  cublas: { supported_platform: true, available: false, library_dirs: [], source: null },
  translation_server: {
    base_url: "http://localhost:11434/v1",
    is_local: true,
    reachable: false,
    models: null,
    error: "connection refused",
  },
  errors: [],
};

function input(over: Partial<DiagnosticsInput> = {}): DiagnosticsInput {
  return {
    appVersion: "0.1.0",
    uiLocale: "zh-TW",
    userAgent: "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    bridgePhase: "ready",
    bridgeDetail: null,
    engine: { version: "0.1.0", protocol_version: 1, platform: "win32", fake: false },
    sessionState: "idle",
    hardware: HARDWARE,
    settings: DEFAULT_SETTINGS,
    settingsPath: "C:\\Users\\alice\\AppData\\Roaming\\kikitori\\settings.json",
    recentLog: ["2026-11-06T00:00:00.000Z error translation server unreachable"],
    now: new Date("2026-11-06T01:02:03Z"),
    ...over,
  };
}

describe("redactText", () => {
  it("removes credentials, tokens and query strings", () => {
    expect(redactText("https://bob:hunter2@example.com/v1")).toBe("https://[redacted]@example.com/v1");
    expect(redactText("GET https://x.test/v1?api_key=abc123&x=1 failed")).toBe("GET https://x.test/v1?[redacted] failed");
    expect(redactText("Authorization: Bearer abcdefghijklmnop")).not.toContain("abcdefghijklmnop");
    expect(redactText("sent Bearer abcdefghijklmnop")).toBe("sent Bearer [redacted]");
    expect(redactText("api_key=sk-abcdefghijklmnopqrstuv")).toBe("api_key=[redacted]");
    expect(redactText('{"token": "abc.def"}')).toBe('{"token": "[redacted]"}');
    expect(redactText("used key sk-proj-ABCDEFGHIJKLMNOPQRST")).toBe("used key [redacted]");
    expect(redactText("hf_ABCDEFGHIJKLMNOPQRSTUVWX")).toBe("[redacted]");
  });

  it("drops the user name from home folders", () => {
    expect(redactText("C:\\Users\\alice\\AppData\\kikitori")).toBe("C:\\Users\\<user>\\AppData\\kikitori");
    expect(redactText("c:/Users/alice/x")).toBe("c:/Users/<user>/x");
    expect(redactText("/home/alice/.config/kikitori")).toBe("/home/<user>/.config/kikitori");
    expect(redactText("/Users/alice/Library")).toBe("/Users/<user>/Library");
  });

  it("leaves ordinary text alone", () => {
    const text = "translation server http://localhost:11434/v1 is not reachable (connection refused)";
    expect(redactText(text)).toBe(text);
  });
});

describe("redactSettings", () => {
  it("keeps the API key variable name but removes secret-looking values", () => {
    const settings = {
      ...DEFAULT_SETTINGS,
      translation: { ...DEFAULT_SETTINGS.translation, base_url: "https://me:pw@llm.example/v1" },
      extra: { api_key: "sk-123", password: "x", nested: { auth_token: "y" }, keyboard: "jp" },
    } as unknown as Settings;
    const out = redactSettings(settings) as Record<string, Record<string, unknown>>;
    expect(out.translation.api_key_env).toBe("KIKITORI_TRANSLATE_API_KEY");
    expect(out.translation.base_url).toBe("https://[redacted]@llm.example/v1");
    expect(out.extra.api_key).toBe("[redacted]");
    expect(out.extra.password).toBe("[redacted]");
    expect((out.extra.nested as Record<string, unknown>).auth_token).toBe("[redacted]");
    expect(out.extra.keyboard).toBe("jp");
    expect(out.subtitle).toEqual(DEFAULT_SETTINGS.subtitle);
  });

  it("does not trust a value in the env-name field that is not a variable name", () => {
    const out = redactSettings({ api_key_env: "sk-live-secret-value" }) as Record<string, unknown>;
    expect(out.api_key_env).toBe("[redacted]");
  });
});

describe("RecentLog", () => {
  it("keeps only the last entries, one line each, shortened", () => {
    const log = new RecentLog();
    for (let i = 0; i < MAX_RECENT_LOG + 5; i++) log.push("error", `line ${i}\nsecond`, new Date(0));
    const entries = log.entries();
    expect(entries).toHaveLength(MAX_RECENT_LOG);
    expect(entries[0]).toBe("1970-01-01T00:00:00.000Z error line 5 second");
    log.push("warning", "x".repeat(2000));
    expect(log.entries().at(-1)?.length).toBeLessThan(600);
  });
});

describe("buildDiagnostics", () => {
  it("lists versions, OS, hardware, settings and recent messages", () => {
    const text = buildDiagnostics(input());
    for (const part of [
      "version: 0.1.0",
      "engine: 0.1.0 (protocol 1, platform win32)",
      "os: Windows 11 (26200) (AMD64)",
      "cpu: AMD Ryzen 7 (8/16 cores/threads)",
      "gpu 0: NVIDIA GeForce RTX 5070, 12227 MiB",
      "cublas: missing",
      "translation server: http://localhost:11434/v1 (local, reachable: no, error: connection refused)",
      '"api_key_env": "KIKITORI_TRANSLATE_API_KEY"',
      "error translation server unreachable",
    ]) {
      expect(text).toContain(part);
    }
  });

  it("never contains the user name or secrets", () => {
    const text = buildDiagnostics(
      input({
        recentLog: ["error request to https://u:p@h/v1?key=zzz failed for C:\\Users\\alice\\x"],
        bridgeDetail: "spawn failed in /home/alice/kikitori",
      }),
    );
    expect(text).not.toContain("alice");
    expect(text).not.toContain("zzz");
    expect(text).not.toMatch(/u:p@/);
  });

  it("says what is missing before the engine is ready", () => {
    const text = buildDiagnostics(input({ engine: null, hardware: null, settings: null, bridgePhase: null, recentLog: [] }));
    expect(text).toContain("engine: not connected");
    expect(text).toContain("not detected yet");
    expect(text).toContain("not loaded");
    expect(text).toContain("none");
  });
});
