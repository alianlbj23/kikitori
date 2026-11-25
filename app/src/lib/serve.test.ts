import { afterEach, describe, expect, it } from "vitest";

import { setLocale, t } from "./i18n";
import type { RemoteServerStatus, ServingEvent } from "./remote";
import {
  applyServing,
  emptyServing,
  formatElapsed,
  hostChangedText,
  servePanelView,
  servingPeer,
  startBlocked,
  type ModelSettings,
  type ServeModels,
} from "./serve";

afterEach(() => setLocale("zh-TW"));

const STARTED = 1_795_000_000_000;
const peer = { name: "A-PC", fingerprint: "aaaa bbbb", state: "connected" as const };
const metrics = { final_busy: 0.5, rtf: 0.3, backlog: 0, gpu: null };

function serving(over: Partial<ServingEvent> = {}): ServingEvent {
  return { peer, session: { started_at_ms: STARTED, sentences: 12, mode: "media" }, metrics, ...over };
}

function status(over: Partial<RemoteServerStatus> = {}): RemoteServerStatus {
  return { enabled: true, listening: true, listen_host: "192.168.1.20", port: 47100, error: null, peer: null, ...over };
}

const models: ServeModels = {
  asr: { fast: "large-v3-turbo", final: "large-v3" },
  translation: { fast: "qwen3.5:4b", final: "" },
};

describe("serving state", () => {
  it("draws the load of the other computer's session and starts afresh with a new one", () => {
    let s = applyServing(emptyServing(), serving());
    s = applyServing(s, serving({ metrics: { ...metrics, final_busy: 0.95 } }));
    expect(s.load.history).toEqual([0.5, 0.95]);
    expect(s.load.last?.final_busy).toBe(0.95);
    s = applyServing(s, serving({ session: { started_at_ms: STARTED + 60_000, sentences: 0, mode: "media" } }));
    expect(s.load.history).toEqual([0.5]);
    // No session: nothing is drawn, and the next session starts empty.
    s = applyServing(s, serving({ session: null }));
    expect(s.load.history).toEqual([]);
  });

  it("takes the peer from the events once they come, from remote.status before that", () => {
    const st = status({ peer: { ...peer, name: "From-Status" } });
    expect(servingPeer(emptyServing(), st)?.name).toBe("From-Status");
    expect(startBlocked(emptyServing(), st)).toBe(true);
    const none = applyServing(emptyServing(), serving({ peer: null, session: null, metrics: null }));
    expect(servingPeer(none, st)).toBeNull();
    expect(startBlocked(none, st)).toBe(false);
    expect(startBlocked(emptyServing(), null)).toBe(false);
    // A dropped link still holds the GPU for the resume.
    const waiting = applyServing(emptyServing(), serving({ peer: { ...peer, state: "waiting_resume" } }));
    expect(startBlocked(waiting, null)).toBe(true);
  });

  it("formats the elapsed time", () => {
    expect(formatElapsed(0)).toBe("00:00");
    expect(formatElapsed(754_000)).toBe("12:34");
    expect(formatElapsed(3_723_000)).toBe("1:02:03");
    expect(formatElapsed(-5)).toBe("00:00");
  });
});

describe("the 運算主機 panel", () => {
  it("waits for another computer, offering to pair a new one", () => {
    const view = servePanelView({ serving: emptyServing(), status: status(), models, nowMs: STARTED });
    expect(view.tone).toBe("waiting");
    expect(view.title).toBe("等別台電腦連進來");
    expect(view.headline).toBe(view.title);
    expect(view.canPair).toBe(true);
    expect(view.load).toBeNull();
    expect(view.asrModels).toBe("辨識：即時 large-v3-turbo・完整 large-v3");
    expect(view.translationModels).toBe("翻譯：即時 qwen3.5:4b・完整 還沒設定");
  });

  it("shows whom it works for, how many sentences and for how long, but no text", () => {
    const s = applyServing(emptyServing(), serving());
    const view = servePanelView({ serving: s, status: status(), models, nowMs: STARTED + 754_000 });
    expect(view.tone).toBe("busy");
    expect(view.headline).toBe("正在替 A-PC 處理・已處理 12 句");
    expect(view.title).toBe("正在替 A-PC 處理・已處理 12 句・12:34");
    expect(view.detail).toBe(t("serve.detailPrivate"));
    expect(view.load?.percent).toBe(50);
    expect(view.canPair).toBe(false);
    setLocale("en");
    const one = applyServing(emptyServing(), serving({ session: { started_at_ms: STARTED, sentences: 1, mode: "media" } }));
    expect(servePanelView({ serving: one, status: status(), models, nowMs: STARTED }).headline).toBe(
      "Working for A-PC · 1 sentence done",
    );
  });

  it("says when a computer is connected without subtitles, or its link dropped", () => {
    const idle = applyServing(emptyServing(), serving({ session: null, metrics: null }));
    expect(servePanelView({ serving: idle, status: status(), models, nowMs: 0 }).headline).toBe(
      t("serve.headConnected", { name: "A-PC" }),
    );
    const dropped = applyServing(emptyServing(), serving({ peer: { ...peer, state: "waiting_resume" } }));
    const view = servePanelView({ serving: dropped, status: status(), models, nowMs: 0 });
    expect(view.tone).toBe("reconnect");
    expect(view.headline).toBe("A-PC 的連線中斷，等它重新連上");
  });

  it("explains a listening problem, and shows the preparation before listening", () => {
    const error = servePanelView({ serving: emptyServing(), status: status({ listening: false, error: "port in use" }), models, nowMs: 0 });
    expect([error.tone, error.headline, error.detail]).toEqual(["error", t("serve.headError"), "port in use"]);
    const off = servePanelView({ serving: emptyServing(), status: status({ listening: false }), models, nowMs: 0 });
    expect([off.tone, off.canPair]).toEqual(["off", false]);
  });
});

describe("host_changed notice", () => {
  const before: ModelSettings = {
    asr: { fast_model: "large-v3-turbo", final_model: "large-v3" },
    translation: { fast_model: "qwen3.5:4b", final_model: "qwen3.5:9b" },
  };
  const ev = {
    by: "A-PC",
    asr: { fast: "large-v3-turbo", final: "large-v3" },
    translation: { fast: "qwen3.5:4b", final: "qwen3.5:9b" },
  };

  it("names who changed which model", () => {
    expect(hostChangedText(before, { ...ev, asr: { ...ev.asr, final: "kotoba" } })).toBe(
      "A-PC 把完整辨識模型改成 kotoba",
    );
    expect(
      hostChangedText(before, { ...ev, asr: { fast: "small", final: "kotoba" }, translation: { ...ev.translation, final: "x:1b" } }),
    ).toBe("A-PC 改了這台的模型：完整辨識模型 → kotoba、即時辨識模型 → small、完整翻譯模型 → x:1b");
  });

  it("still says who changed something when nothing differs (settings already re-read)", () => {
    expect(hostChangedText(before, ev)).toBe("A-PC 改了這台的模型設定（完整辨識：large-v3）");
    expect(hostChangedText(null, ev)).toBe("A-PC 改了這台的模型設定（完整辨識：large-v3）");
    expect(hostChangedText(before, { ...ev, by: " ", asr: { ...ev.asr, final: "k" } })).toBe(
      `${t("pair.unknownPeer")} 把完整辨識模型改成 k`,
    );
  });
});
