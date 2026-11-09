import { describe, expect, it } from "vitest";

import { showGpuReminder, type GpuReminderInput } from "./gpu-reminder";
import type { GpuRuntime } from "./types";

const RUNTIME: GpuRuntime = {
  id: "gpu-runtime",
  supported: true,
  installed: false,
  package: "nvidia-cublas-cu12",
  version: "12.8.4.1",
  path: null,
  download_bytes: 600_000_000,
  installed_bytes: null,
  license: "NVIDIA CUDA EULA",
  license_url: "https://docs.nvidia.com/cuda/eula/index.html",
  cublas_available: false,
  cublas_source: null,
};

function input(over: Partial<GpuReminderInput> = {}): GpuReminderInput {
  return { runtimeMissing: true, runtime: RUNTIME, downloading: false, dismissed: false, closed: false, ...over };
}

describe("showGpuReminder", () => {
  it("reminds an NVIDIA machine that has not downloaded the component", () => {
    expect(showGpuReminder(input())).toBe(true);
    // The models list may still be loading; the engine's recommendation already knows.
    expect(showGpuReminder(input({ runtime: null }))).toBe(true);
  });

  it("stays quiet when there is nothing to do", () => {
    expect(showGpuReminder(input({ runtimeMissing: false }))).toBe(false); // no NVIDIA card, or GPU already usable
    expect(showGpuReminder(input({ runtimeMissing: null }))).toBe(false); // not detected yet
    expect(showGpuReminder(input({ runtime: { ...RUNTIME, installed: true } }))).toBe(false);
    expect(showGpuReminder(input({ runtime: { ...RUNTIME, supported: false } }))).toBe(false); // macOS
    expect(showGpuReminder(input({ downloading: true }))).toBe(false);
  });

  it("respects the permanent dismissal and closing it for this launch", () => {
    expect(showGpuReminder(input({ dismissed: true }))).toBe(false);
    expect(showGpuReminder(input({ closed: true }))).toBe(false);
  });
});
