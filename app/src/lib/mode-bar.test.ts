import { afterEach, describe, expect, it } from "vitest";

import { setLocale } from "./i18n";
import { linkState, modeBarView, type ModeBarInput } from "./mode-bar";
import { DEFAULT_COMPUTE, type ComputeSettings, type HostInfo, type ServingEvent } from "./remote";
import { applyServing, emptyServing, servePanelView, type ServingState } from "./serve";

afterEach(() => setLocale("zh-TW"));

const host: HostInfo = {
  gpu: { name: "NVIDIA GeForce GTX 1080 Ti", vram_total_mb: 11264 },
  device: "cuda",
  compute_type: "int8",
  current: { fast: "large-v3-turbo", final: "large-v3", translation: null },
  recommended: null,
  models: [],
  translation_model: null,
  translation_models: null,
};

function input(compute: Partial<ComputeSettings>, over: Partial<ModeBarInput> = {}): ModeBarInput {
  return {
    compute: { ...DEFAULT_COMPUTE, ...compute },
    host: null,
    rttMs: null,
    sessionActive: false,
    reconnecting: false,
    serveHeadline: "",
    ...over,
  };
}

describe("mode bar", () => {
  it("gives each role its own colour, icon and words", () => {
    const local = modeBarView(input({}));
    expect([local.tone, local.color, local.icon, local.text, local.link]).toEqual([
      "local",
      "indigo",
      "computer",
      "這台電腦處理",
      null,
    ]);
    const client = modeBarView(input({ mode: "remote", host: "10.0.0.2" }));
    expect([client.tone, client.color, client.icon]).toEqual(["client", "sky", "computers"]);
    const serve = modeBarView(input({ server_enabled: true }, { serveHeadline: "等別台電腦連進來" }));
    expect([serve.tone, serve.color, serve.icon, serve.text]).toEqual([
      "serve",
      "purple",
      "server",
      "運算主機模式：等別台電腦連進來",
    ]);
    const colours = new Set([local.color, client.color, serve.color]);
    const icons = new Set([local.icon, client.icon, serve.icon]);
    expect([colours.size, icons.size]).toEqual([3, 3]);
  });

  it("follows each serving event's sentence count (main.ts renders the bar on every one)", () => {
    const status = { enabled: true, listening: true, listen_host: "", port: 47100, error: null, peer: null };
    const models = { asr: { fast: "a", final: "b" }, translation: { fast: "", final: "" } };
    const event = (sentences: number): ServingEvent => ({
      peer: { name: "A-PC", fingerprint: "aaaa", state: "connected" },
      session: { started_at_ms: 0, sentences, mode: "media" },
      metrics: null,
    });
    const bar = (serving: ServingState): string =>
      modeBarView(
        input(
          { server_enabled: true },
          { serveHeadline: servePanelView({ serving, status, models, nowMs: 0 }).headline },
        ),
      ).text;
    let serving = applyServing(emptyServing(), event(3));
    expect(bar(serving)).toBe("運算主機模式：正在替 A-PC 處理・已處理 3 句");
    serving = applyServing(serving, event(4));
    expect(bar(serving)).toBe("運算主機模式：正在替 A-PC 處理・已處理 4 句");
  });

  it("names the compute host's card and the latency while connected", () => {
    const connected = modeBarView(
      input({ mode: "remote", host: "10.0.0.2" }, { host, rttMs: 3.4, sessionActive: true }),
    );
    expect(connected.text).toBe("使用運算主機：GTX 1080 Ti・11 GB・延遲 3 ms");
    expect([connected.link, connected.linkText]).toEqual(["connected", "已連線"]);
    // Before connecting: the card (or the address) without a latency.
    const idle = modeBarView(input({ mode: "remote", host: "10.0.0.2" }, { host, rttMs: 3 }));
    expect(idle.text).toBe("使用運算主機：GTX 1080 Ti・11 GB");
    expect(idle.linkText).toBe("未連線");
    expect(modeBarView(input({ mode: "remote", host: "10.0.0.2", port: 47200 })).text).toBe(
      "使用運算主機：10.0.0.2:47200",
    );
    expect(modeBarView(input({ mode: "remote" })).text).toBe("使用運算主機：還沒設定");
  });

  it("shows the link state", () => {
    expect(linkState(false, false)).toBe("offline");
    expect(linkState(false, true)).toBe("offline");
    expect(linkState(true, false)).toBe("connected");
    expect(linkState(true, true)).toBe("reconnecting");
    const reconnecting = modeBarView(input({ mode: "remote", host: "h" }, { sessionActive: true, reconnecting: true, rttMs: 5 }));
    expect([reconnecting.link, reconnecting.linkText, reconnecting.text]).toEqual([
      "reconnecting",
      "重新連線中",
      "使用運算主機：h",
    ]);
  });

  it("speaks every interface language", () => {
    setLocale("en");
    expect(modeBarView(input({})).text).toBe("This computer does the work");
    expect(modeBarView(input({ server_enabled: true }, { serveHeadline: "x" })).text).toBe("Compute host mode: x");
  });
});
