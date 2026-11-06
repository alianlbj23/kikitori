import { describe, expect, it } from "vitest";

import {
  FURIGANA_DICTIONARY_ID,
  afterFuriganaDownload,
  furiganaBlockView,
  furiganaEnableAction,
  furiganaSwitchView,
  isFuriganaDownload,
  type FuriganaSwitchInput,
} from "./furigana";
import type { DownloadProgressEvent, FuriganaDictionary } from "./types";

const dictionary = (over: Partial<FuriganaDictionary> = {}): FuriganaDictionary => ({
  id: FURIGANA_DICTIONARY_ID,
  installed: false,
  edition: "core",
  version: "20260428",
  download_bytes: 72_238_136,
  installed_bytes: 217_391_698,
  disk_bytes: null,
  path: null,
  license: "Apache-2.0",
  license_url: "https://example.invalid/LEGAL",
  in_use: false,
  ...over,
});

const progress = (over: Partial<DownloadProgressEvent> = {}): DownloadProgressEvent => ({
  model: FURIGANA_DICTIONARY_ID,
  kind: "furigana",
  downloaded_bytes: 36_119_068,
  total_bytes: 72_238_136,
  state: "downloading",
  error: null,
  ...over,
});

const input = (over: Partial<FuriganaSwitchInput> = {}): FuriganaSwitchInput => ({
  enabled: false,
  dictionary: dictionary(),
  modelsLoaded: true,
  download: null,
  pendingEnable: false,
  busy: false,
  engineReady: true,
  ...over,
});

describe("furiganaSwitchView", () => {
  it("is off and usable by default", () => {
    expect(furiganaSwitchView(input())).toEqual({ checked: false, disabled: false, percent: null, status: null });
  });

  it("shows the download the switch started, checked and locked", () => {
    const view = furiganaSwitchView(input({ download: progress(), pendingEnable: true }));
    expect(view.checked).toBe(true);
    expect(view.disabled).toBe(true);
    expect(view.percent).toBe(50);
    expect(view.status?.key).toBe("furigana.downloading");
  });

  it("reports a failed download as an error and lets the user retry", () => {
    const view = furiganaSwitchView(input({ download: progress({ state: "error", error: "Boom" }) }));
    expect(view).toMatchObject({ checked: false, disabled: false, status: { key: "furigana.downloadFailed", error: true } });
    expect(view.status?.params).toEqual({ error: "Boom" });
  });

  it("shows saved-on as off when the dictionary is gone, so turning it on downloads again", () => {
    expect(furiganaSwitchView(input({ enabled: true })).checked).toBe(false);
    expect(furiganaSwitchView(input({ enabled: true, dictionary: dictionary({ installed: true }) })).checked).toBe(true);
  });

  it("is disabled while a request is in flight or the engine is away", () => {
    expect(furiganaSwitchView(input({ busy: true })).disabled).toBe(true);
    expect(furiganaSwitchView(input({ engineReady: false })).disabled).toBe(true);
  });

  it("says so when the engine has no furigana, but not before the list arrived", () => {
    expect(furiganaSwitchView(input({ dictionary: null })).status?.key).toBe("furigana.unsupported");
    const loading = furiganaSwitchView(input({ dictionary: null, modelsLoaded: false, enabled: true }));
    expect(loading).toMatchObject({ checked: true, disabled: true, status: null });
  });
});

describe("turning furigana on", () => {
  it("enables at once with the dictionary, downloads first without it", () => {
    expect(furiganaEnableAction(dictionary({ installed: true }))).toBe("enable");
    expect(furiganaEnableAction(dictionary())).toBe("download");
    expect(furiganaEnableAction(null)).toBe("unsupported");
  });

  it("enables after the download only when the user was waiting for it", () => {
    expect(afterFuriganaDownload(progress({ state: "done" }), true)).toBe("enable");
    expect(afterFuriganaDownload(progress({ state: "done" }), false)).toBe("none");
    expect(afterFuriganaDownload(progress({ state: "error", error: "x" }), true)).toBe("failed");
    expect(afterFuriganaDownload(progress(), true)).toBe("none");
    expect(afterFuriganaDownload(progress({ model: "base", state: "done" }), true)).toBe("none");
  });

  it("recognises the dictionary's progress events", () => {
    expect(isFuriganaDownload(progress())).toBe(true);
    expect(isFuriganaDownload(progress({ kind: null }))).toBe(true);
    expect(isFuriganaDownload(progress({ kind: "asr", model: "base" }))).toBe(false);
    expect(isFuriganaDownload(progress({ kind: null, model: "base" }))).toBe(false);
  });
});

describe("furiganaBlockView", () => {
  const ctx = { downloading: false, deleting: false, engineReady: true };

  it("offers nothing to delete before the download", () => {
    const view = furiganaBlockView(dictionary(), ctx);
    expect(view.deletable).toBeNull();
    expect(view.lines.map((l) => l.key)).toEqual(["furigana.lede", "furigana.notInstalled", "furigana.license"]);
  });

  it("shows the disk use and allows deleting an unused dictionary", () => {
    const view = furiganaBlockView(dictionary({ installed: true, disk_bytes: 217_392_119 }), ctx);
    expect(view.lines[1]!.key).toBe("furigana.installed");
    expect(view.deletable).toEqual({ enabled: true, reason: null });
  });

  it("blocks deleting while furigana uses it, a download runs or the engine is away", () => {
    const inUse = furiganaBlockView(dictionary({ installed: true, in_use: true }), ctx);
    expect(inUse.deletable).toEqual({ enabled: false, reason: { key: "furigana.inUse", params: {}, error: false } });
    const installed = dictionary({ installed: true });
    expect(furiganaBlockView(installed, { ...ctx, deleting: true }).deletable?.enabled).toBe(false);
    expect(furiganaBlockView(installed, { ...ctx, downloading: true }).deletable?.enabled).toBe(false);
    expect(furiganaBlockView(installed, { ...ctx, engineReady: false }).deletable?.enabled).toBe(false);
  });
});
