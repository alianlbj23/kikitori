import { describe, expect, it } from "vitest";

import {
  SPEAKER_CHOICES,
  SPEAKER_MODEL_ID,
  afterSpeakerDownload,
  choiceOf,
  isSpeakerDownload,
  parseChoice,
  speakerBlockView,
  speakerChoiceAction,
  speakerSelectView,
  speakersPatch,
  type SpeakerSelectInput,
} from "./speaker-model";
import type { DownloadProgressEvent, SpeakerModel } from "./types";

const MODEL: SpeakerModel = {
  id: SPEAKER_MODEL_ID,
  name: "3D-Speaker CAM++",
  installed: true,
  download_bytes: 28_281_164,
  installed_bytes: 28_281_164,
  disk_bytes: 28_281_164,
  path: "/x/model.onnx",
  license: "Apache-2.0",
  license_url: "https://example.com",
};
const MISSING: SpeakerModel = { ...MODEL, installed: false, disk_bytes: null, path: null };

function progress(state: DownloadProgressEvent["state"], done = 0): DownloadProgressEvent {
  return { model: SPEAKER_MODEL_ID, kind: "speaker", downloaded_bytes: done, total_bytes: 1000, state, error: state === "error" ? "boom" : null };
}

function input(over: Partial<SpeakerSelectInput> = {}): SpeakerSelectInput {
  return {
    speakers: { enabled: true, count: 3 },
    model: MODEL,
    modelsLoaded: true,
    download: null,
    pending: null,
    busy: false,
    engineReady: true,
    ...over,
  };
}

describe("choices", () => {
  it("offers off, auto and 2 to 6", () => {
    expect(SPEAKER_CHOICES).toEqual(["off", "auto", "2", "3", "4", "5", "6"]);
    expect(parseChoice("4")).toBe("4");
    expect(parseChoice("7")).toBeNull();
    expect(parseChoice("")).toBeNull();
  });

  it("maps settings to a choice and back", () => {
    expect(choiceOf({ enabled: false, count: 3 })).toBe("off");
    expect(choiceOf({ enabled: true, count: null })).toBe("auto");
    expect(choiceOf({ enabled: true, count: 4 })).toBe("4");
    expect(speakersPatch("off")).toEqual({ speakers: { enabled: false } });
    expect(speakersPatch("auto")).toEqual({ speakers: { enabled: true, count: null } });
    expect(speakersPatch("5")).toEqual({ speakers: { enabled: true, count: 5 } });
  });
});

describe("speakerSelectView", () => {
  it("shows the saved choice when the model is there", () => {
    expect(speakerSelectView(input())).toEqual({ value: "3", disabled: false, percent: null, status: null });
  });

  it("shows off when the model was deleted", () => {
    expect(speakerSelectView(input({ model: MISSING })).value).toBe("off");
  });

  it("is disabled for an engine without speaker labels", () => {
    const view = speakerSelectView(input({ model: null }));
    expect(view.disabled).toBe(true);
    expect(view.status?.key).toBe("speakers.unsupported");
  });

  it("shows the pending choice and progress while downloading", () => {
    const view = speakerSelectView(input({ model: MISSING, speakers: { enabled: false, count: null }, pending: "auto", download: progress("downloading", 250) }));
    expect(view).toMatchObject({ value: "auto", disabled: true, percent: 25 });
    expect(view.status?.key).toBe("speakers.downloading");
  });

  it("reports a failed download as an error", () => {
    const view = speakerSelectView(input({ model: MISSING, download: progress("error") }));
    expect(view.status).toEqual({ key: "speakers.downloadFailed", params: { error: "boom" }, error: true });
  });

  it("is disabled while busy or before the engine is ready", () => {
    expect(speakerSelectView(input({ busy: true })).disabled).toBe(true);
    expect(speakerSelectView(input({ engineReady: false })).disabled).toBe(true);
  });
});

describe("actions and downloads", () => {
  it("downloads first only when the model is missing", () => {
    expect(speakerChoiceAction("auto", MODEL)).toBe("save");
    expect(speakerChoiceAction("3", MISSING)).toBe("download");
    expect(speakerChoiceAction("off", MISSING)).toBe("save");
    expect(speakerChoiceAction("2", null)).toBe("unsupported");
  });

  it("saves the waiting choice when the download is done", () => {
    expect(afterSpeakerDownload(progress("done"), "4")).toBe("save");
    expect(afterSpeakerDownload(progress("done"), null)).toBe("none");
    expect(afterSpeakerDownload(progress("error"), "4")).toBe("failed");
    expect(afterSpeakerDownload(progress("downloading"), "4")).toBe("none");
    expect(afterSpeakerDownload({ ...progress("done"), model: "other" }, "4")).toBe("none");
  });

  it("recognises its own downloads", () => {
    expect(isSpeakerDownload(progress("done"))).toBe(true);
    expect(isSpeakerDownload({ ...progress("done"), kind: null })).toBe(true);
    expect(isSpeakerDownload({ ...progress("done"), kind: "asr", model: "small" })).toBe(false);
  });
});

describe("speakerBlockView", () => {
  const ctx = { downloading: false, deleting: false, engineReady: true };

  it("lists name, disk use and license, and allows deleting", () => {
    const view = speakerBlockView(MODEL, ctx);
    expect(view.lines.map((l) => l.key)).toEqual(["speakers.lede", "speakers.installed", "speakers.license"]);
    expect(view.deletable).toEqual({ enabled: true });
  });

  it("has nothing to delete when not downloaded", () => {
    const view = speakerBlockView(MISSING, ctx);
    expect(view.lines[1]!.key).toBe("speakers.notInstalled");
    expect(view.deletable).toBeNull();
  });

  it("offers deleting a leftover partial download", () => {
    const view = speakerBlockView({ ...MISSING, disk_bytes: 1000 }, ctx);
    expect(view.lines[1]!.key).toBe("speakers.notInstalled");
    expect(view.deletable).toEqual({ enabled: true });
  });

  it("blocks deleting while downloading or deleting", () => {
    expect(speakerBlockView(MODEL, { ...ctx, downloading: true }).deletable).toEqual({ enabled: false });
    expect(speakerBlockView(MODEL, { ...ctx, deleting: true }).deletable).toEqual({ enabled: false });
  });
});
