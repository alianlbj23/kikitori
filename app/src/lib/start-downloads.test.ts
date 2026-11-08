import { afterEach, describe, expect, it, vi } from "vitest";

import { setLocale } from "./i18n";
import { StartDownloads, startDownloadsView, type StartDownloadsDeps } from "./start-downloads";
import engine from "./testdata/engine-fake-nvidia.json";
import type { DownloadProgressEvent } from "./types";
import { parseDownloadProgress } from "./validate";

afterEach(() => setLocale("zh-TW"));

class EngineError {
  constructor(readonly code: string) {}
}

/** Engine calls that resolve (or reject with an engine code) and record what was asked. */
function harness(failures: Record<string, string> = {}) {
  const calls: string[] = [];
  const starts = vi.fn(async () => undefined);
  const deps: StartDownloadsDeps = {
    downloadAsr: async (model) => {
      calls.push(`asr:${model}`);
      if (failures[`asr:${model}`]) throw new EngineError(failures[`asr:${model}`]!);
    },
    pullTranslation: async (model) => {
      calls.push(`translation:${model}`);
      if (failures[`translation:${model}`]) throw new EngineError(failures[`translation:${model}`]!);
    },
    start: starts,
    errorCode: (e) => (e instanceof EngineError ? e.code : null),
    describe: (e) => (e instanceof EngineError ? `failed: ${e.code}` : String(e)),
    onChange: () => undefined,
  };
  return { flow: new StartDownloads(deps), calls, starts };
}

function progress(
  kind: DownloadProgressEvent["kind"],
  model: string,
  state: DownloadProgressEvent["state"],
  done = 0,
  total = 100,
  error: string | null = null,
): DownloadProgressEvent {
  return { model, kind, downloaded_bytes: done, total_bytes: total, state, error };
}

/** Lets the rejected engine calls settle. */
const flush = () => new Promise((resolve) => setTimeout(resolve, 0));

describe("downloads before starting", () => {
  it("downloads every missing model, then starts once all are done", async () => {
    const { flow, calls, starts } = harness();
    flow.begin([
      { kind: "asr", model: "small", sizeBytes: 484_000_000 },
      { kind: "translation", model: "qwen3.5:9b", sizeBytes: null },
      { kind: "asr", model: "small", sizeBytes: 484_000_000 },
    ]);
    expect(calls).toEqual(["asr:small", "translation:qwen3.5:9b"]);
    expect(flow.state.phase).toBe("downloading");
    expect(flow.active).toBe(true);
    flow.onProgress(progress("asr", "small", "downloading", 40));
    expect(flow.state.items[0]).toMatchObject({ state: "downloading", downloadedBytes: 40 });
    flow.onProgress(progress("asr", "small", "done", 100));
    expect(starts).not.toHaveBeenCalled();
    // The captured pull of the real engine (0/0 first, total grows, then done).
    for (const ev of (engine["download_progress events"] as unknown[]).map(parseDownloadProgress)) flow.onProgress(ev);
    expect(starts).toHaveBeenCalledTimes(1);
    await flush();
    expect(flow.state.phase).toBe("idle");
    expect(flow.active).toBe(false);
  });

  it("tells same-named ASR and translation downloads apart, and ignores unrelated events", () => {
    const { flow, starts } = harness();
    flow.begin([{ kind: "translation", model: "small", sizeBytes: null }]);
    flow.onProgress(progress("asr", "small", "done"));
    flow.onProgress(progress("gpu_runtime", "gpu-runtime", "done"));
    flow.onProgress(progress("translation", "other", "done"));
    expect(starts).not.toHaveBeenCalled();
    expect(flow.tracks("translation", "small")).toBe(true);
    expect(flow.tracks("asr", "small")).toBe(false);
    flow.onProgress(progress("translation", "small", "done"));
    expect(starts).toHaveBeenCalledTimes(1);
  });

  it("matches events without a kind (older engines) to ASR downloads", () => {
    const { flow, starts } = harness();
    flow.begin([{ kind: "asr", model: "base", sizeBytes: 1 }]);
    flow.onProgress(progress(null, "base", "done"));
    expect(starts).toHaveBeenCalledTimes(1);
  });

  it("keeps the total growing as Ollama reports more layers", () => {
    const { flow } = harness();
    flow.begin([{ kind: "translation", model: "m", sizeBytes: 1000 }]);
    flow.onProgress(progress("translation", "m", "downloading", 0, 0));
    expect(flow.state.items[0]!.totalBytes).toBe(1000);
    flow.onProgress(progress("translation", "m", "downloading", 10, 5000));
    expect(flow.state.items[0]!.totalBytes).toBe(5000);
  });

  it("stops at a failed download and retries only what failed", async () => {
    const { flow, calls, starts } = harness();
    flow.begin([
      { kind: "asr", model: "small", sizeBytes: 1 },
      { kind: "asr", model: "large-v3-turbo", sizeBytes: 1 },
    ]);
    flow.onProgress(progress("asr", "small", "done"));
    flow.onProgress(progress("asr", "large-v3-turbo", "error", 5, 100, "OSError: disk full"));
    expect(flow.state.phase).toBe("failed");
    expect(flow.state.items[1]!.error).toBe("OSError: disk full");
    expect(flow.active).toBe(false);
    expect(starts).not.toHaveBeenCalled();

    calls.length = 0;
    flow.retry();
    expect(calls).toEqual(["asr:large-v3-turbo"]);
    expect(flow.state.phase).toBe("downloading");
    flow.onProgress(progress("asr", "large-v3-turbo", "done"));
    expect(starts).toHaveBeenCalledTimes(1);
    await flush();
  });

  it("waits for the other downloads before reporting a failure", () => {
    const { flow } = harness();
    flow.begin([
      { kind: "asr", model: "a", sizeBytes: 1 },
      { kind: "asr", model: "b", sizeBytes: 1 },
    ]);
    flow.onProgress(progress("asr", "a", "error", 0, 1, "boom"));
    expect(flow.state.phase).toBe("downloading");
    flow.onProgress(progress("asr", "b", "done"));
    expect(flow.state.phase).toBe("failed");
  });

  it.each([
    ["pull_unsupported", "pull_unsupported"],
    ["translation_server_unreachable", "server_unreachable"],
    ["translation_server_auth_failed", "auth_failed"],
  ] as const)("stops with instructions when the pull answers %s", async (code, blocker) => {
    const { flow, starts } = harness({ "translation:qwen3.5:9b": code });
    flow.begin([{ kind: "translation", model: "qwen3.5:9b", sizeBytes: null }]);
    await flush();
    expect(flow.state.phase).toBe("blocked");
    expect(flow.state.blocker).toBe(blocker);
    expect(starts).not.toHaveBeenCalled();
    setLocale("en");
    const view = startDownloadsView(flow.state, "http://localhost:11434/v1")!;
    expect(view.tone).toBe("bad");
    expect(view.canRetry).toBe(true);
    expect(view.message).toContain("http://localhost:11434/v1");
    if (blocker === "pull_unsupported") expect(view.message).toContain("qwen3.5:9b");
    if (blocker === "server_unreachable") expect(view.message).toContain("ollama serve");
    if (blocker === "auth_failed") expect(view.message).toContain("API key");
  });

  it("treats download_in_progress as already downloading", async () => {
    const { flow, starts } = harness({ "asr:small": "download_in_progress" });
    flow.begin([{ kind: "asr", model: "small", sizeBytes: 1 }]);
    await flush();
    expect(flow.state.phase).toBe("downloading");
    flow.onProgress(progress("asr", "small", "done"));
    expect(starts).toHaveBeenCalledTimes(1);
  });

  it("reports other request errors on the item", async () => {
    const { flow } = harness({ "asr:small": "unknown_model" });
    flow.begin([{ kind: "asr", model: "small", sizeBytes: 1 }]);
    await flush();
    expect(flow.state.phase).toBe("failed");
    expect(flow.state.items[0]!.error).toBe("failed: unknown_model");
  });

  it("does not loop when the engine still misses a model it just downloaded", async () => {
    const { flow, calls, starts } = harness();
    flow.begin([{ kind: "translation", model: "m", sizeBytes: null }]);
    flow.onProgress(progress("translation", "m", "done"));
    expect(starts).toHaveBeenCalledTimes(1);
    await flush();
    calls.length = 0;
    // The restarted session reports the same model missing again.
    flow.begin([{ kind: "translation", model: "m", sizeBytes: null }]);
    expect(calls).toEqual([]);
    expect(flow.state.phase).toBe("failed");
    expect(flow.state.items[0]!.error).toContain("m");
    // A new press of Start forgets earlier rounds.
    flow.reset();
    flow.begin([{ kind: "translation", model: "m", sizeBytes: null }]);
    expect(calls).toEqual(["translation:m"]);
  });

  it("cancel stops waiting and ignores later progress", () => {
    const { flow, starts } = harness();
    flow.begin([{ kind: "asr", model: "small", sizeBytes: 1 }]);
    flow.cancel();
    expect(flow.state.phase).toBe("idle");
    flow.onProgress(progress("asr", "small", "done"));
    expect(starts).not.toHaveBeenCalled();
    expect(startDownloadsView(flow.state, "")).toBeNull();
  });
});

describe("download panel text", () => {
  it("shows progress per model and what happens next", () => {
    setLocale("en");
    const { flow } = harness();
    flow.begin([
      { kind: "asr", model: "small", sizeBytes: 484_000_000 },
      { kind: "translation", model: "qwen3.5:9b", sizeBytes: null },
    ]);
    flow.onProgress(progress("asr", "small", "downloading", 242_000_000, 484_000_000));
    const view = startDownloadsView(flow.state, "")!;
    expect(view.items[0]).toEqual({
      label: "Speech recognition model small",
      percent: 50,
      text: "50%, 242 MB of 484 MB",
      tone: "busy",
    });
    expect(view.items[1]).toMatchObject({ label: "Translation model qwen3.5:9b", text: "Preparing the download" });
    expect(view.message).toContain("automatically");
    expect(view).toMatchObject({ canRetry: false, canCancel: true, tone: "busy" });
  });
});
