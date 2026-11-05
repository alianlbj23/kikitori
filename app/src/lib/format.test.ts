import { describe, expect, it } from "vitest";

import { describeStatus, formatBytes, formatTimecode, htmlLang, percent } from "./format";
import type { BridgeStatus } from "./types";

const bridge = (phase: BridgeStatus["phase"]): BridgeStatus => ({
  phase,
  detail: null,
  restarts: 0,
  command: null,
  source: null,
  info: null,
});

describe("format", () => {
  it("formats timecodes from 16 kHz sample counts", () => {
    expect(formatTimecode(0)).toBe("0:00");
    expect(formatTimecode(16000 * 75)).toBe("1:15");
    expect(formatTimecode(16000 * 3725)).toBe("1:02:05");
  });

  it("formats sizes and percentages", () => {
    expect(formatBytes(145_000_000)).toBe("145 MB");
    expect(formatBytes(1_620_000_000)).toBe("1.6 GB");
    expect(formatBytes(null)).toBe("未知");
    expect(percent(5, 10)).toBe(50);
    expect(percent(5, 0)).toBe(0);
    expect(percent(20, 10)).toBe(100);
  });

  it("maps languages to safe lang attributes", () => {
    expect(htmlLang("ja")).toBe("ja");
    expect(htmlLang("zh")).toBe("zh-Hant-TW");
    expect(htmlLang("x\" onload")).toBe("");
  });

  it("combines bridge and session state", () => {
    expect(describeStatus(null, "idle").tone).toBe("busy");
    expect(describeStatus(bridge("failed"), "running").tone).toBe("bad");
    expect(describeStatus(bridge("ready"), "running")).toEqual({ label: "正在聆聽", tone: "live" });
    expect(describeStatus(bridge("ready"), "idle").tone).toBe("quiet");
  });
});
