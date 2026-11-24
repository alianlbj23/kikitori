import { afterEach, describe, expect, it } from "vitest";

import { describeStatusError } from "./format";
import {
  applyHostDownload,
  comboFit,
  comboWarning,
  downloadPercent,
  downloadText,
  hostDownloadRows,
  hostManageErrorText,
  hostSlotOptions,
  needsOverConfirm,
  overConfirmText,
  setModelArgs,
  slotFit,
  tightHint,
  translationOptions,
} from "./host-models";
import { setLocale, t } from "./i18n";
import type { HostDownloadProgress, HostInfo } from "./remote";

afterEach(() => setLocale("zh-TW"));

/**
 * A compute host's report: each model labelled by the compute host itself as
 * the final model next to its real-time large-v3-turbo (`fit`) and as the
 * real-time model next to its final large-v3 (`fit_as_fast`). The two labels
 * differ on purpose (kotoba, big) so each menu is seen reading its own.
 */
function host(over: Partial<HostInfo> = {}): HostInfo {
  return {
    gpu: { name: "NVIDIA GeForce GTX 1080 Ti", vram_total_mb: 11264 },
    device: "cuda",
    compute_type: "int8",
    current: {
      fast: "large-v3-turbo",
      final: "large-v3",
      translation: { fast: "qwen3.5:4b", final: "qwen3.5:9b" },
    },
    recommended: { fast: "large-v3-turbo", final: "large-v3" },
    models: [
      { id: "large-v3-turbo", downloaded: true, need_mb: 1600, fit: "ok", fit_as_fast: "ok" },
      { id: "large-v3", downloaded: true, need_mb: 3000, fit: "ok", fit_as_fast: "ok" },
      { id: "kotoba", downloaded: true, need_mb: 1500, fit: "ok", fit_as_fast: "tight" },
      { id: "big", downloaded: true, need_mb: 4900, fit: "tight", fit_as_fast: null },
      { id: "huge", downloaded: true, need_mb: 9000, fit: "over", fit_as_fast: "over" },
      { id: "small", downloaded: false, need_mb: 600, fit: "ok", fit_as_fast: "ok" },
      { id: "heavy", downloaded: false, need_mb: 7000, fit: "over", fit_as_fast: "over" },
    ],
    translation_model: "qwen3.5:9b",
    translation_models: ["qwen3.5:4b", "qwen3.5:9b"],
    ...over,
  };
}

/** A compute host older than `fit_as_fast`: it labels only the final pass. */
function olderHost(): HostInfo {
  return host({ models: host().models.map((m) => ({ ...m, fit_as_fast: null })) });
}

function withFinal(final: string): HostInfo {
  const h = host();
  return { ...h, current: { ...h.current, final } };
}

function withFast(fast: string): HostInfo {
  const h = host();
  return { ...h, current: { ...h.current, fast } };
}

describe("how well a model fits the compute host", () => {
  it("uses the compute host's own label for the final model", () => {
    expect(slotFit(host(), "final", "large-v3")).toBe("ok");
    expect(slotFit(host(), "final", "big")).toBe("tight");
    expect(slotFit(host(), "final", "huge")).toBe("over");
    expect(slotFit(host(), "final", "not-listed")).toBeNull();
    expect(comboFit(host())).toBe("ok");
    expect(comboFit(withFinal("huge"))).toBe("over");
  });

  it("uses the compute host's own label for the real-time model, not an estimate", () => {
    expect(slotFit(host(), "fast", "large-v3-turbo")).toBe("ok");
    // kotoba fits as the final model but is tight as the real-time one: each slot reads its own label.
    expect(slotFit(host(), "fast", "kotoba")).toBe("tight");
    expect(slotFit(host(), "final", "kotoba")).toBe("ok");
    expect(slotFit(host(), "fast", "huge")).toBe("over");
    expect(slotFit(host(), "fast", "big")).toBeNull();
    expect(slotFit(host(), "fast", "not-listed")).toBeNull();
  });

  it("cannot tell the real-time load from an older compute host", () => {
    for (const m of olderHost().models) expect(slotFit(olderHost(), "fast", m.id), m.id).toBeNull();
    // The final pass is still labelled.
    expect(slotFit(olderHost(), "final", "huge")).toBe("over");
  });
});

describe("recognition menus", () => {
  it("lists the downloaded models labelled 建議 and fit, 超出負荷 marked but selectable", () => {
    const options = hostSlotOptions(host(), "final");
    expect(options.map((o) => o.value)).toEqual(["large-v3-turbo", "large-v3", "kotoba", "big", "huge"]);
    expect(options.map((o) => o.label)).toEqual([
      "large-v3-turbo（跑得動）",
      "large-v3（建議・跑得動）",
      "kotoba（跑得動）",
      "big（可能吃緊）",
      "⚠ huge（超出負荷）",
    ]);
    expect(options.map((o) => o.fit)).toEqual(["ok", "ok", "ok", "tight", "over"]);
    const fast = hostSlotOptions(host(), "fast");
    expect(fast.map((o) => o.label)).toEqual([
      "large-v3-turbo（建議・跑得動）",
      "large-v3（跑得動）",
      "kotoba（可能吃緊）",
      "big（無法判斷負荷）",
      "⚠ huge（超出負荷）",
    ]);
    expect(fast.map((o) => o.fit)).toEqual(["ok", "ok", "tight", null, "over"]);
    expect(hostSlotOptions(olderHost(), "fast").every((o) => o.fit === null)).toBe(true);
  });

  it("keeps a current model the compute host no longer lists as downloaded", () => {
    const options = hostSlotOptions(withFinal("small"), "final");
    expect(options[0]).toEqual({ value: "small", label: "small（運算主機上沒有）", fit: null });
  });

  it("asks again only before switching to a 超出負荷 model", () => {
    expect(needsOverConfirm(host(), "final", "huge")).toBe(true);
    expect(needsOverConfirm(host(), "final", "big")).toBe(false);
    expect(needsOverConfirm(host(), "final", "large-v3")).toBe(false);
    // Already the current model: nothing to confirm.
    expect(needsOverConfirm(withFinal("huge"), "final", "huge")).toBe(false);
    expect(needsOverConfirm(host(), "fast", "huge")).toBe(true);
    expect(needsOverConfirm(host(), "fast", "kotoba")).toBe(false);
    // Unknown is not "over": no question, the warning after applying tells.
    expect(needsOverConfirm(host(), "fast", "big")).toBe(false);
    expect(needsOverConfirm(olderHost(), "fast", "huge")).toBe(false);
  });

  it("says what may happen in plain words, naming GPU or system memory", () => {
    expect(overConfirmText(host(), "huge")).toBe(
      "運算主機的顯示卡記憶體可能不夠，字幕可能變慢、跟不上或無法開始。仍要使用 huge 嗎？",
    );
    expect(overConfirmText(host({ device: "cpu", gpu: null }), "huge")).toContain("運算主機的記憶體可能不夠");
    setLocale("en");
    expect(overConfirmText(host(), "huge")).toContain("Use huge anyway?");
  });

  it("notes a tight current model under its menu, without asking", () => {
    expect(tightHint(withFinal("big"), "final")).toBe(t("hostModels.tight", { model: "big" }));
    expect(tightHint(host(), "final")).toBeNull();
    expect(tightHint(withFinal("huge"), "final")).toBeNull();
    expect(tightHint(withFast("kotoba"), "fast")).toBe(t("hostModels.tight", { model: "kotoba" }));
    expect(tightHint(host(), "fast")).toBeNull();
    expect(tightHint(withFast("big"), "fast")).toBeNull();
  });

  it("keeps warning while the current combination is too heavy", () => {
    expect(comboWarning(withFinal("huge"))).toEqual({ tone: "bad", text: t("hostModels.comboOver") });
    expect(comboWarning(withFinal("big"))).toEqual({ tone: "info", text: t("hostModels.comboTight") });
    expect(comboWarning(host())).toBeNull();
    expect(comboWarning(withFinal("not-listed"))).toBeNull();
  });

  it("builds the remote_host_set_models arguments", () => {
    expect(setModelArgs("asr", "final", "large-v3")).toEqual({ asr: { final_model: "large-v3" } });
    expect(setModelArgs("asr", "fast", "kotoba")).toEqual({ asr: { fast_model: "kotoba" } });
    expect(setModelArgs("translation", "final", "qwen3.5:9b")).toEqual({ translation: { final_model: "qwen3.5:9b" } });
  });
});

describe("translation menus", () => {
  it("lists what the compute host's translation server has installed", () => {
    expect(translationOptions(host(), "final")?.map((o) => o.value)).toEqual(["qwen3.5:4b", "qwen3.5:9b"]);
  });

  it("keeps a current model that is not installed, and an unset one", () => {
    const h = host();
    const missing = { ...h, current: { ...h.current, translation: { fast: "gone:1b", final: "" } } };
    expect(translationOptions(missing, "fast")?.[0]).toEqual({
      value: "gone:1b",
      label: "gone:1b（運算主機上沒有安裝）",
      fit: null,
    });
    expect(translationOptions(missing, "final")?.[0]).toEqual({ value: "", label: "（還沒設定）", fit: null });
  });

  it("offers nothing when the translation server did not answer", () => {
    expect(translationOptions(host({ translation_models: null }), "fast")).toBeNull();
  });
});

describe("downloads on the compute host", () => {
  const progress = (over: Partial<HostDownloadProgress> = {}): HostDownloadProgress => ({
    model: "heavy",
    downloaded_bytes: 50,
    total_bytes: 200,
    state: "downloading",
    error: null,
    ...over,
  });

  it("lists the models not downloaded yet, a heavy one flagged but still offered", () => {
    const rows = hostDownloadRows(host(), new Map([["heavy", progress()]]));
    expect(rows.map((r) => [r.id, r.need, r.fitText, r.heavy])).toEqual([
      ["small", "需要 0.6 GB", "跑得動", false],
      ["heavy", "需要 6.8 GB", "超出負荷", true],
    ]);
    expect(rows[1]?.progress?.downloaded_bytes).toBe(50);
  });

  it("follows the progress events and forgets a finished download", () => {
    let map = applyHostDownload(new Map(), progress());
    expect(downloadPercent(map.get("heavy")!)).toBe(25);
    expect(downloadText(map.get("heavy")!)).toBe("運算主機下載中 25%");
    expect(downloadText(progress({ total_bytes: 0 }))).toBe("運算主機下載中…");
    expect(downloadPercent(progress({ total_bytes: 0 }))).toBeNull();
    map = applyHostDownload(map, progress({ state: "error", error: "disk full" }));
    expect(downloadText(map.get("heavy")!)).toBe("運算主機下載 heavy 失敗：disk full");
    map = applyHostDownload(map, progress({ state: "done" }));
    expect(map.has("heavy")).toBe(false);
  });
});

describe("errors from the compute host", () => {
  it("explains the management codes and the connection codes in plain words", () => {
    expect(hostManageErrorText("remote_model_not_downloaded", "x")).toBe(t("hostModels.error.notDownloaded"));
    expect(hostManageErrorText("remote_translation_model_missing", "x")).toBe(t("hostModels.error.translationMissing"));
    expect(hostManageErrorText("remote_download_in_progress", "x")).toBe(t("hostModels.error.downloadInProgress"));
    expect(hostManageErrorText("remote_unknown_model", "x")).toBe(t("hostModels.error.unknownModel"));
    expect(hostManageErrorText("remote_error", "x")).toBe(t("hostModels.error.other"));
    expect(hostManageErrorText("remote_unreachable", "x")).toBe(describeStatusError("remote_unreachable", null));
    expect(hostManageErrorText("internal_error", "fallback")).toBe("fallback");
    expect(hostManageErrorText(null, "fallback")).toBe("fallback");
  });
});
