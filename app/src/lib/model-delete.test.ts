import { describe, expect, it } from "vitest";

import { formatBytes } from "./format";
import { t } from "./i18n";
import { deleteBlockedText, deleteConfirmText, deleteOffer, reclaimableBytes, type DeleteContext } from "./model-delete";
import type { AsrModel } from "./types";

function model(id: string, over: Partial<AsrModel> = {}): AsrModel {
  return {
    id,
    size_bytes: 484_000_000,
    downloaded: true,
    license: "MIT",
    speed_tier: "medium",
    no_speech_reliable: true,
    cpu_ram_mb: null,
    vram_mb: null,
    vram_int8_mb: null,
    recommended_for: [],
    disk_bytes: 490_000_000,
    ...over,
  };
}

const IDLE: DeleteContext = {
  fastModel: "base",
  finalModel: "small",
  sessionActive: false,
  downloading: false,
  deleting: false,
  engineReady: true,
};

describe("deleteOffer", () => {
  it("offers nothing for a model that is not downloaded", () => {
    expect(deleteOffer(model("medium", { downloaded: false }), IDLE)).toBeNull();
  });

  it("allows deleting an unused model and a selected one while idle", () => {
    expect(deleteOffer(model("medium"), IDLE)).toEqual({ blocked: null, roles: [] });
    expect(deleteOffer(model("small"), IDLE)).toEqual({ blocked: null, roles: ["final"] });
    expect(deleteOffer(model("x"), { ...IDLE, fastModel: "x", finalModel: "x" })?.roles).toEqual(["fast", "final"]);
  });

  it("blocks a model the running session uses, but not the others", () => {
    const running = { ...IDLE, sessionActive: true };
    expect(deleteOffer(model("small"), running)?.blocked).toBe("in_use");
    expect(deleteOffer(model("medium"), running)?.blocked).toBeNull();
  });

  it("blocks while downloading, deleting or without an engine", () => {
    expect(deleteOffer(model("medium"), { ...IDLE, downloading: true })?.blocked).toBe("downloading");
    expect(deleteOffer(model("medium"), { ...IDLE, deleting: true })?.blocked).toBe("deleting");
    expect(deleteOffer(model("medium"), { ...IDLE, engineReady: false })?.blocked).toBe("engine_not_ready");
  });
});

describe("delete texts", () => {
  it("names the space freed, falling back to the download size", () => {
    expect(reclaimableBytes(model("m"))).toBe(490_000_000);
    expect(reclaimableBytes(model("m", { disk_bytes: null }))).toBe(484_000_000);
    expect(deleteConfirmText(model("medium"), { blocked: null, roles: [] })).toBe(
      t("asr.deleteConfirm", { model: "medium", size: formatBytes(490_000_000) }),
    );
  });

  it("warns that a selected model is downloaded again at the next start", () => {
    const text = deleteConfirmText(model("small"), { blocked: null, roles: ["final"] });
    expect(text).toContain(t("asr.deleteSelected", { roles: t("role.final") }));
  });

  it("explains every blocked state", () => {
    for (const b of ["in_use", "downloading", "deleting", "engine_not_ready"] as const) {
      expect(deleteBlockedText(b).trim()).not.toBe("");
    }
  });
});
