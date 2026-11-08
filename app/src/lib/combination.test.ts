import { describe, expect, it } from "vitest";

import {
  Latest,
  NVIDIA_EULA_URL,
  afterGpuRuntimeInstalled,
  canOpenLicenseUrl,
  canSave,
  type CheckView,
} from "./combination";
import { OLLAMA_DOWNLOAD_URL } from "./translation-guide";

describe("canSave", () => {
  it.each<[CheckView, boolean]>([
    [{ state: "checked", check: { ok: true, errors: [], warnings: ["slow on CPU"] } }, true],
    [{ state: "checked", check: { ok: false, errors: ["no cuBLAS"], warnings: [] } }, false],
    [{ state: "checking" }, false],
    [{ state: "local_errors" }, false],
    [{ state: "rejected", message: "asr.fast_cpu_threads: bad" }, false],
    // A check that could not run must not lock the user out; settings.set still validates.
    [{ state: "unavailable", message: "timeout" }, true],
  ])("%j → %s", (view, expected) => {
    expect(canSave(view)).toBe(expected);
  });
});

describe("Latest", () => {
  it("applies only the newest of overlapping answers, whatever order they arrive in", async () => {
    const latest = new Latest();
    const applied: string[] = [];
    const resolvers: Array<(v: string) => void> = [];
    const request = (): Promise<string> => new Promise((resolve) => resolvers.push(resolve));

    const run = async () => {
      const token = latest.next();
      const answer = await request();
      if (latest.isCurrent(token)) applied.push(answer);
    };
    const first = run();
    const second = run();
    // The engine answers the newer request first, then the stale one.
    resolvers[1]("second");
    resolvers[0]("first");
    await Promise.all([first, second]);
    expect(applied).toEqual(["second"]);
  });

  it("invalidate makes every outstanding token stale", () => {
    const latest = new Latest();
    const token = latest.next();
    expect(latest.isCurrent(token)).toBe(true);
    latest.invalidate();
    expect(latest.isCurrent(token)).toBe(false);
  });
});

describe("afterGpuRuntimeInstalled", () => {
  it("restarts the engine only when nothing would be interrupted", () => {
    expect(afterGpuRuntimeInstalled(false, 0)).toBe("restart");
    expect(afterGpuRuntimeInstalled(true, 0)).toBe("offer");
    expect(afterGpuRuntimeInstalled(false, 1)).toBe("offer");
  });
});

describe("canOpenLicenseUrl", () => {
  it("only allows the NVIDIA EULA page the opener permission covers", () => {
    expect(canOpenLicenseUrl(NVIDIA_EULA_URL)).toBe(true);
    expect(canOpenLicenseUrl("https://docs.nvidia.com/cuda/eula/index.html#x")).toBe(false);
    expect(canOpenLicenseUrl("https://example.com/")).toBe(false);
    expect(canOpenLicenseUrl(null)).toBe(false);
  });
});

describe("opener permission", () => {
  it("is scoped to exactly the URLs the UI opens (NVIDIA EULA, Ollama download page)", async () => {
    const capability = (await import("../../src-tauri/capabilities/main.json")).default as {
      permissions: Array<string | { identifier: string; allow?: Array<{ url?: string }> }>;
    };
    const opener = capability.permissions.filter(
      (p) => (typeof p === "string" ? p : p.identifier).startsWith("opener:"),
    );
    expect(opener).toEqual([
      { identifier: "opener:allow-open-url", allow: [{ url: NVIDIA_EULA_URL }, { url: OLLAMA_DOWNLOAD_URL }] },
    ]);
  });
});
