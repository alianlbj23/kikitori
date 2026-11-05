import { describe, expect, it, vi } from "vitest";

import buildRs from "../../src-tauri/build.rs?raw";
import capabilityJson from "../../src-tauri/capabilities/main.json";
import apiSource from "./api.ts?raw";
import {
  FirstRunGate,
  autoAppliedNotice,
  blockingDownloads,
  combinationSummary,
  defaultsKeptNotice,
  dismissedWith,
  machineSummary,
  missingModels,
  missingModelsView,
  offerText,
  runFirstRun,
  shouldOfferRecommendation,
  type FirstRunDeps,
} from "./recommend";
import { DEFAULT_SETTINGS, parseAppliedRecommendation } from "./settings";
import engine from "./testdata/engine-fake-nvidia.json";
import type { AsrModel, DownloadProgressEvent, HardwareReport, Recommendation } from "./types";
import { parseHardware, parseModels, parseRecommendationPreview } from "./validate";

/** This machine (docs/specs/hardware.md "本機實測"): RTX 5070 12 GB with cuBLAS. */
function rtx5070(): HardwareReport {
  const report = parseHardware(engine["hardware.detect"]);
  report.hardware.gpus = [
    {
      index: 0,
      name: "NVIDIA GeForce RTX 5070",
      memory_total_mb: 12227,
      memory_used_mb: 5206,
      driver_version: "621.01",
      compute_capability: "12.0",
    },
  ];
  report.recommendation = gpuRec();
  return report;
}

function gpuRec(overrides: Partial<Recommendation> = {}): Recommendation {
  return {
    tier: "nvidia_8gb",
    fast_asr: { model: "small", device: "cuda", compute_type: "float16", cpu_threads: 0 },
    final_asr: { model: "large-v3-turbo", device: "cuda", compute_type: "float16", cpu_threads: 0 },
    translation_fast: "qwen3.5:9b",
    translation_final: "qwen3.5:9b",
    translation_budget_gb: 8.21,
    gpu_runtime_missing: false,
    reasons: [],
    warnings: [],
    ...overrides,
  };
}

const appliedGpu = () => ({ ...parseAppliedRecommendation(engine["settings.apply_recommendation"]), recommendation: gpuRec() });

describe("first-run notice", () => {
  it("names the machine and what was chosen (the RTX 5070 example)", () => {
    expect(autoAppliedNotice(rtx5070().hardware, gpuRec())).toBe(
      "已依這台電腦（NVIDIA GeForce RTX 5070，12 GB）套用建議組合：即時 small、完整 large-v3-turbo（GPU），翻譯 qwen3.5:9b。",
    );
  });

  it("is built from the recommendation, including a missing GPU runtime", () => {
    const report = parseHardware(engine["hardware.detect"]);
    const text = autoAppliedNotice(report.hardware, report.recommendation);
    expect(text).toContain("Fake GeForce 8GB，8 GB");
    expect(text).toContain("即時 base、完整 small（CPU），翻譯 qwen3.5:4b");
    expect(text).toContain("GPU 加速元件");
  });

  it("describes CPU-only and Apple machines by processor and memory", () => {
    const report = parseHardware(engine["hardware.detect"]);
    report.hardware.gpus = [];
    expect(machineSummary(report.hardware)).toBe("Fake 8-Core CPU，32 GB 記憶體");
    report.hardware.apple_silicon = true;
    report.hardware.memory.total_bytes = 16 * 1024 ** 3;
    expect(machineSummary(report.hardware)).toBe("Apple Silicon，16 GB 記憶體");
    report.hardware.apple_silicon = false;
    report.hardware.cpu.model = null;
    report.hardware.memory.total_bytes = null;
    expect(machineSummary(report.hardware)).toBe("這台電腦的處理器");
  });

  it("uses the largest GPU when there are several", () => {
    const report = rtx5070();
    report.hardware.gpus.unshift({ ...report.hardware.gpus[0]!, index: 1, name: "Small GPU", memory_total_mb: 4096 });
    expect(machineSummary(report.hardware)).toBe("NVIDIA GeForce RTX 5070，12 GB");
  });

  it("summarises different translation models, mixed devices and no translation", () => {
    expect(combinationSummary(gpuRec({ translation_fast: "a:1b", translation_final: "b:9b" }))).toBe(
      "即時 small、完整 large-v3-turbo（GPU），即時翻譯 a:1b、完整翻譯 b:9b",
    );
    expect(
      combinationSummary(
        gpuRec({ fast_asr: { model: "base", device: "cpu", compute_type: "int8", cpu_threads: 4 } }),
      ),
    ).toBe("即時 base（CPU）、完整 large-v3-turbo（GPU），翻譯 qwen3.5:9b");
    expect(combinationSummary(gpuRec({ translation_fast: null, translation_final: null }))).toBe(
      "即時 small、完整 large-v3-turbo（GPU），翻譯模型沿用目前設定（翻譯服務不在本機）",
    );
  });

  it("says the safe defaults were kept when detection fails", () => {
    const text = defaultsKeptNotice("detect", "引擎在 45 秒內沒有回應。", DEFAULT_SETTINGS.asr);
    expect(text).toContain("無法偵測這台電腦的硬體");
    expect(text).toContain("引擎在 45 秒內沒有回應");
    expect(text).toContain("即時 base、完整 small");
    const apply = defaultsKeptNotice("apply", "無法儲存設定檔。", DEFAULT_SETTINGS.asr);
    expect(apply).toContain("無法套用建議組合");
    // A timed-out apply may still have been written: do not claim the defaults are in use.
    expect(apply).not.toContain("先使用預設的組合");
    expect(apply).toContain("「模型」頁");
  });
});

describe("first-run flow", () => {
  function deps(overrides: Partial<FirstRunDeps> = {}): FirstRunDeps & { detect: ReturnType<typeof vi.fn>; apply: ReturnType<typeof vi.fn> } {
    return {
      detect: vi.fn(async () => rtx5070()),
      apply: vi.fn(async () => appliedGpu()),
      userChoseModels: () => false,
      ...overrides,
    } as FirstRunDeps & { detect: ReturnType<typeof vi.fn>; apply: ReturnType<typeof vi.fn> };
  }

  it("waits for detection, then applies once and builds the notice", async () => {
    const d = deps();
    const outcome = await runFirstRun(d, DEFAULT_SETTINGS);
    expect(d.detect).toHaveBeenCalledTimes(1);
    expect(d.apply).toHaveBeenCalledTimes(1);
    expect(d.detect.mock.invocationCallOrder[0]).toBeLessThan(d.apply.mock.invocationCallOrder[0]!);
    expect(outcome.kind).toBe("applied");
    if (outcome.kind !== "applied") return;
    expect(outcome.notice).toBe(
      "已依這台電腦（NVIDIA GeForce RTX 5070，12 GB）套用建議組合：即時 small、完整 large-v3-turbo（GPU），翻譯 qwen3.5:9b。",
    );
  });

  it("keeps the defaults and says so when detection fails", async () => {
    const d = deps({ detect: vi.fn(async () => Promise.reject(new Error("nvidia-smi hung"))) });
    const outcome = await runFirstRun(d, DEFAULT_SETTINGS);
    expect(d.apply).not.toHaveBeenCalled();
    expect(outcome.kind).toBe("kept_defaults");
    if (outcome.kind === "kept_defaults") expect(outcome.notice).toContain("nvidia-smi hung");
  });

  it("keeps the defaults when applying fails", async () => {
    const d = deps({ apply: vi.fn(async () => Promise.reject(new Error("disk full"))) });
    const outcome = await runFirstRun(d, DEFAULT_SETTINGS);
    expect(outcome.kind).toBe("kept_defaults");
    if (outcome.kind === "kept_defaults") {
      expect(outcome.notice).toContain("無法套用建議組合");
      expect(outcome.report).not.toBeNull();
    }
  });

  it("asks the engine to apply only if nothing was saved, and reports a refusal as skipped", async () => {
    const d = deps({ apply: vi.fn(async () => ({ ...appliedGpu(), applied: null })) });
    expect((await runFirstRun(d, DEFAULT_SETTINGS)).kind).toBe("skipped");
    expect(d.apply).toHaveBeenCalledTimes(1);
  });

  it("does not override a combination the user saved while detection ran", async () => {
    let chose = false;
    const d = deps({
      detect: vi.fn(async () => {
        chose = true;
        return rtx5070();
      }),
      userChoseModels: () => chose,
    });
    expect((await runFirstRun(d, DEFAULT_SETTINGS)).kind).toBe("skipped");
    expect(d.apply).not.toHaveBeenCalled();
  });

  it("gate: starts only for a new settings file, once per window", () => {
    const gate = new FirstRunGate();
    expect(gate.claim({ isNew: false, userChoseModels: false })).toBe(false);
    expect(gate.claim({ isNew: true, userChoseModels: true })).toBe(false);
    expect(gate.claim({ isNew: true, userChoseModels: false })).toBe(true);
    // An engine restart reloads settings; a failed first run must not loop.
    expect(gate.claim({ isNew: true, userChoseModels: false })).toBe(false);
  });
});

describe("recommendation banner", () => {
  const preview = () => parseRecommendationPreview(engine["settings.preview_recommendation"]);

  it("is offered when the saved combination differs and was not dismissed", () => {
    const p = preview();
    expect(p.differs).toBe(true);
    expect(shouldOfferRecommendation(p, [])).toBe(true);
    expect(offerText(p)).toBe("有更適合這台電腦的組合：即時 base、完整 small（CPU），翻譯 qwen3.5:4b。");
  });

  it("is hidden once applied (differs = false) or when there is no preview", () => {
    expect(shouldOfferRecommendation({ ...preview(), differs: false }, [])).toBe(false);
    expect(shouldOfferRecommendation(null, [])).toBe(false);
  });

  it("remembers dismissal per fingerprint and reappears for a new one", () => {
    const p = preview();
    const dismissed = dismissedWith([], p.fingerprint);
    expect(shouldOfferRecommendation(p, dismissed)).toBe(false);
    const changed = { ...p, fingerprint: "0123456789abcdef" };
    expect(shouldOfferRecommendation(changed, dismissed)).toBe(true);
  });

  it("keeps a short, duplicate-free dismissal list", () => {
    expect(dismissedWith(["a", "b"], "a")).toEqual(["b", "a"]);
    const many = Array.from({ length: 12 }, (_, i) => `f${i}`);
    const kept = dismissedWith(many, "new");
    expect(kept).toHaveLength(10);
    expect(kept[kept.length - 1]).toBe("new");
    expect(kept).not.toContain("f0");
  });
});

describe("missing models", () => {
  const models = (): AsrModel[] => parseModels(engine["models.list"]).asr;
  const progress = (model: string, state: DownloadProgressEvent["state"], done = 0, total = 100): DownloadProgressEvent => ({
    model,
    downloaded_bytes: done,
    total_bytes: total,
    state,
    error: state === "error" ? "timed out" : null,
  });

  it("lists exactly the chosen models that are not downloaded, once each", () => {
    const list = models().map((m) => (m.id === "large-v3-turbo" ? { ...m, downloaded: false } : m));
    expect(missingModels({ fast_model: "small", final_model: "large-v3-turbo" }, list).map((m) => m.id)).toEqual([
      "large-v3-turbo",
    ]);
    expect(missingModels({ fast_model: "fake-missing", final_model: "fake-missing" }, list).map((m) => m.id)).toEqual([
      "fake-missing",
    ]);
    expect(missingModels({ fast_model: "base", final_model: "small" }, list)).toEqual([]);
    // Unknown ids cannot be downloaded; the engine reports unknown_model instead.
    expect(missingModels({ fast_model: "nope", final_model: "small" }, list)).toEqual([]);
  });

  it("offers a download, then shows progress, failure and completion", () => {
    const required = [
      { id: "large-v3-turbo", size_bytes: 1_600_000_000 },
      { id: "medium", size_bytes: 1_500_000_000 },
    ];
    const list = models().map((m) => (m.id === "large-v3-turbo" ? { ...m, downloaded: false } : m));
    list.push({ ...list[0]!, id: "medium", downloaded: false });
    const offer = missingModelsView(required, list, new Map());
    expect(offer.state).toBe("offer");
    expect(offer.text).toBe("還需要下載 large-v3-turbo、medium（共約 3.1 GB）才能開始。");

    const downloading = missingModelsView(
      required,
      list,
      new Map([["large-v3-turbo", progress("large-v3-turbo", "downloading", 40, 100)]]),
    );
    expect(downloading.state).toBe("downloading");
    expect(downloading.text).toContain("large-v3-turbo 40%");

    const failed = missingModelsView(required, list, new Map([["medium", progress("medium", "error")]]));
    expect(failed.state).toBe("failed");
    expect(failed.text).toContain("medium 下載失敗：timed out");

    const done = missingModelsView(
      required,
      list.map((m) => ({ ...m, downloaded: true })),
      new Map(),
    );
    expect(done).toEqual({ state: "done", text: "模型已下載完成，可以按「開始」了。" });
  });

  it("blocks starting while a chosen model is still downloading", () => {
    const downloads = new Map([
      ["large-v3-turbo", progress("large-v3-turbo", "downloading", 1, 4)],
      ["tiny", progress("tiny", "downloading")],
    ]);
    expect(blockingDownloads({ fast_model: "small", final_model: "large-v3-turbo" }, downloads)).toEqual([
      "large-v3-turbo",
    ]);
    expect(blockingDownloads({ fast_model: "small", final_model: "base" }, downloads)).toEqual([]);
  });
});

describe("commands and permissions", () => {
  it("every command the UI invokes is declared in build.rs and allowed for the main window", () => {
    const invoked = [...apiSource.matchAll(/invoke\("([a-z_]+)"/g)].map((m) => m[1]!);
    expect(invoked).toContain("settings_preview_recommendation");
    const declared = [...buildRs.matchAll(/"([a-z_]+)",/g)].map((m) => m[1]!);
    const allowed = (capabilityJson as { permissions: unknown[] }).permissions.filter(
      (p): p is string => typeof p === "string",
    );
    for (const command of invoked) {
      expect(declared, command).toContain(command);
      expect(allowed, command).toContain(`allow-${command.replaceAll("_", "-")}`);
    }
  });
});
