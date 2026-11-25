import { afterEach, describe, expect, it } from "vitest";

import { setLocale } from "./i18n";
import {
  CHART_HEIGHT,
  CHART_WIDTH,
  LOAD_HISTORY,
  applyLoad,
  emptyLoad,
  formatGb,
  formatRtf,
  loadDetail,
  loadFraction,
  loadLevel,
  loadPath,
  loadTextKey,
  loadView,
  pushLoad,
  pushReading,
  vramShare,
  type LoadInput,
} from "./load";
import type { GpuUsage, MetricsEvent } from "./types";

afterEach(() => setLocale("zh-TW"));

const GPU: GpuUsage = { name: "NVIDIA GeForce GTX 1080 Ti", vram_used_mb: 6246, vram_total_mb: 11264, util_pct: 40 };

function gpu(usedShare: number): GpuUsage {
  return { ...GPU, vram_total_mb: 10_000, vram_used_mb: usedShare * 10_000 };
}

function input(over: Partial<LoadInput> = {}): LoadInput {
  return { backlog: 0, final_busy: null, gpu: null, ...over };
}

function metrics(over: Partial<MetricsEvent> = {}): MetricsEvent {
  return {
    session_id: 1,
    level_dbfs: -20,
    backlog: 0,
    latency_ms_median: null,
    final_busy: null,
    rtf: null,
    gpu: null,
    ...over,
  };
}

describe("load level", () => {
  it("is unknown without a reading, or with neither busy share nor GPU sample", () => {
    expect(loadLevel(null)).toBe("unknown");
    expect(loadLevel(input())).toBe("unknown");
    expect(loadLevel(input({ backlog: 1 }))).toBe("unknown");
  });

  it("is ok below every threshold", () => {
    expect(loadLevel(input({ final_busy: 0 }))).toBe("ok");
    expect(loadLevel(input({ final_busy: 0.69, gpu: gpu(0.84), backlog: 1 }))).toBe("ok");
    expect(loadLevel(input({ gpu: gpu(0.5) }))).toBe("ok");
  });

  it("is tight from backlog 2, busy 0.7 or 85 % of the GPU memory", () => {
    expect(loadLevel(input({ backlog: 2 }))).toBe("tight");
    expect(loadLevel(input({ final_busy: 0.7 }))).toBe("tight");
    expect(loadLevel(input({ final_busy: 0.1, gpu: gpu(0.85) }))).toBe("tight");
    expect(loadLevel(input({ final_busy: 0.89, gpu: gpu(0.94) }))).toBe("tight");
  });

  it("is over from backlog 3, busy 0.9 or 95 % of the GPU memory", () => {
    expect(loadLevel(input({ backlog: 3 }))).toBe("over");
    expect(loadLevel(input({ final_busy: 0.9 }))).toBe("over");
    expect(loadLevel(input({ final_busy: 0.1, gpu: gpu(0.95) }))).toBe("over");
    // The worst signal decides.
    expect(loadLevel(input({ final_busy: 0.2, gpu: gpu(0.3), backlog: 7 }))).toBe("over");
  });

  it("ignores a GPU sample without a total", () => {
    expect(vramShare({ ...GPU, vram_total_mb: 0 })).toBeNull();
    expect(vramShare(null)).toBeNull();
    expect(vramShare({ ...GPU, vram_used_mb: 20_000 })).toBe(1);
    expect(loadLevel(input({ gpu: { ...GPU, vram_total_mb: 0 } }))).toBe("unknown");
  });
});

describe("load percentage", () => {
  it("is the largest of busy share, GPU memory share and backlog / 4, capped at 100 %", () => {
    expect(loadFraction(input({ final_busy: 0.3, gpu: gpu(0.6) }))).toBeCloseTo(0.6);
    expect(loadFraction(input({ final_busy: 0.8, gpu: gpu(0.6) }))).toBeCloseTo(0.8);
    expect(loadFraction(input({ final_busy: 0.1, backlog: 2 }))).toBeCloseTo(0.5);
    expect(loadFraction(input({ final_busy: 0.1, backlog: 9 }))).toBe(1);
    expect(loadFraction(input({ backlog: 3 }))).toBeCloseTo(0.75);
  });

  it("is unknown when the level is", () => {
    expect(loadFraction(null)).toBeNull();
    expect(loadFraction(input({ backlog: 1 }))).toBeNull();
  });
});

describe("load chart", () => {
  it("keeps the last 120 readings", () => {
    let h: (number | null)[] = [];
    for (let i = 0; i < LOAD_HISTORY + 5; i++) h = pushLoad(h, i);
    expect(h).toHaveLength(LOAD_HISTORY);
    expect(h[0]).toBe(5);
    expect(h.at(-1)).toBe(LOAD_HISTORY + 4);
  });

  it("draws the newest reading at the right edge, 100 % at the top", () => {
    expect(loadPath([], 238, 40)).toBe("");
    expect(loadPath([0.5], 238, 40)).toBe("M238 20");
    expect(loadPath([0, 1], 238, 40)).toBe("M236 40 L238 0");
  });

  it("breaks the line at unknown readings and clamps out-of-range ones", () => {
    expect(loadPath([0.25, null, 0.5, 2], 238, 40)).toBe("M232 30 M236 20 L238 0");
  });

  it("spans the whole box when full", () => {
    const d = loadPath(Array.from({ length: LOAD_HISTORY }, () => 0), CHART_WIDTH, CHART_HEIGHT);
    expect(d.startsWith(`M0 ${CHART_HEIGHT}`)).toBe(true);
    expect(d.endsWith(`L${CHART_WIDTH} ${CHART_HEIGHT}`)).toBe(true);
  });
});

describe("load words", () => {
  it("names each level in plain words", () => {
    expect(loadTextKey("ok")).toBe("load.ok");
    expect(loadTextKey("tight")).toBe("load.tight");
    expect(loadTextKey("over")).toBe("load.over");
    expect(loadTextKey("unknown")).toBe("load.unknown");
  });

  it("formats GPU memory and speed without jargon", () => {
    expect(formatGb(6246)).toBe("6.1");
    expect(formatGb(11264)).toBe("11");
    expect(formatRtf(0.42)).toBe("0.4");
    expect(formatRtf(0.04)).toBe("0.04");
    expect(loadDetail(metrics({ gpu: GPU, rtf: 0.42 }))).toBe("顯示卡記憶體 6.1／11 GB・辨識速度 0.4 倍時間");
    expect(loadDetail(metrics({ rtf: 1.3 }))).toBe("辨識速度 1.3 倍時間");
    expect(loadDetail(metrics({ gpu: GPU }))).toBe("顯示卡記憶體 6.1／11 GB");
    expect(loadDetail(metrics())).toBeNull();
    expect(loadDetail(null)).toBeNull();
    setLocale("en");
    expect(loadDetail(metrics({ gpu: GPU, rtf: 0.42 }))).not.toMatch(/VRAM|RTF/);
  });
});

describe("load state", () => {
  it("collects the session's readings and starts over for a new session", () => {
    let s = applyLoad(emptyLoad(), metrics({ final_busy: 0.5 }));
    s = applyLoad(s, metrics({ final_busy: null }));
    s = applyLoad(s, metrics({ final_busy: 0.95, rtf: 0.6 }));
    expect(s.history).toEqual([0.5, null, 0.95]);
    const view = loadView(s);
    expect(view.level).toBe("over");
    expect(view.percent).toBe(95);
    expect(view.text).toBe("跟不上：建議改用較小的模型");
    expect(view.detail).toBe("辨識速度 0.6 倍時間");
    expect(view.path).not.toBe("");

    const next = applyLoad(s, metrics({ session_id: 2, final_busy: 0.1 }));
    expect(next.history).toEqual([0.1]);
    // A late reading of the earlier session is dropped.
    expect(applyLoad(next, metrics({ session_id: 1, final_busy: 0.9 }))).toBe(next);
  });

  it("shows nothing measured before the first reading", () => {
    const view = loadView(emptyLoad());
    expect(view).toEqual({ level: "unknown", percent: null, text: "等第一句辨識完成後顯示", detail: null, path: "" });
  });
});

describe("readings without a session id (the compute host's own load)", () => {
  it("adds a serving reading to the chart and the small print", () => {
    let s = pushReading(emptyLoad(), { backlog: 0, final_busy: 0.4, gpu: null, rtf: 0.3 });
    s = pushReading(s, { backlog: 3, final_busy: 0.2, gpu: null, rtf: null });
    expect(s.history).toEqual([0.4, 0.75]);
    expect(loadView(s).level).toBe("over");
    expect(loadView(pushReading(emptyLoad(), { backlog: 0, final_busy: 0.4, gpu: null, rtf: 0.3 })).detail).toBe(
      "辨識速度 0.3 倍時間",
    );
  });
});
