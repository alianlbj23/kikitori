import { describe, expect, it, vi } from "vitest";

import { DEFAULT_SETTINGS, type SettingsResult } from "./settings";
import { emptySubtitles, applySubtitle, lineDisplay } from "./subtitles";
import { setShowTranslation, translationSwitchMethod, type TranslationToggleDeps } from "./translation-toggle";
import type { OverlaySnapshot } from "./types";

function result(enabled: boolean): SettingsResult {
  return {
    settings: { ...DEFAULT_SETTINGS, translation: { ...DEFAULT_SETTINGS.translation, enabled } },
    path: null,
    warnings: [],
    isNew: false,
  };
}

const snapshot = (show: boolean): OverlaySnapshot => ({
  style: DEFAULT_SETTINGS.subtitle,
  click_through: false,
  visible: true,
  shortcut: "CommandOrControl+Alt+K",
  shortcut_registered: true,
  shortcuts: [],
  locale: null,
  show_translation: show,
});

function deps(overlayFails = false) {
  const d = {
    sessionSetTranslation: vi.fn(async (on: boolean) => result(on)),
    settingsSet: vi.fn(async (patch: Record<string, unknown>) =>
      result((patch.translation as { enabled: boolean }).enabled),
    ),
    overlaySetShowTranslation: vi.fn(async (on: boolean) => {
      if (overlayFails) throw new Error("no overlay");
      return snapshot(on);
    }),
  } satisfies TranslationToggleDeps;
  return d;
}

describe('"顯示翻譯" switch', () => {
  it("switches a loading or running session through the engine, otherwise saves the setting", () => {
    expect(translationSwitchMethod("running")).toBe("session.set_translation");
    expect(translationSwitchMethod("loading")).toBe("session.set_translation");
    expect(translationSwitchMethod("idle")).toBe("settings.set");
    expect(translationSwitchMethod("error")).toBe("settings.set");
  });

  it("uses session.set_translation while running and tells the overlay the saved value", async () => {
    const d = deps();
    const r = await setShowTranslation(false, "running", d);
    expect(d.sessionSetTranslation).toHaveBeenCalledWith(false);
    expect(d.settingsSet).not.toHaveBeenCalled();
    expect(d.overlaySetShowTranslation).toHaveBeenCalledWith(false);
    expect(r.settings.settings.translation.enabled).toBe(false);
    expect(r.overlay?.show_translation).toBe(false);
  });

  it("uses settings.set when no session runs", async () => {
    const d = deps();
    await setShowTranslation(true, "idle", d);
    expect(d.settingsSet).toHaveBeenCalledWith({ translation: { enabled: true } });
    expect(d.sessionSetTranslation).not.toHaveBeenCalled();
  });

  it("does not touch the overlay when the engine refuses", async () => {
    const d = deps();
    d.settingsSet.mockRejectedValueOnce({ kind: "engine", code: "settings_io_error", message: "disk" });
    await expect(setShowTranslation(false, "idle", d)).rejects.toMatchObject({ code: "settings_io_error" });
    expect(d.overlaySetShowTranslation).not.toHaveBeenCalled();
  });

  it("keeps the saved setting when only the overlay fails, and returns the error", async () => {
    const r = await setShowTranslation(false, "idle", deps(true));
    expect(r.settings.settings.translation.enabled).toBe(false);
    expect(r.overlay).toBeNull();
    expect(r.overlayError).toBeInstanceOf(Error);
  });
});

describe("original-only lines", () => {
  const state = applySubtitle(
    emptySubtitles(1),
    {
      utterance_id: 1,
      original: "次の駅で降りましょう。",
      translation: "我們在下一站下車吧。",
      source_language: "ja",
      target_language: "zh-TW",
      is_final: true,
      start_sample: 0,
      end_sample: 16000,
      session_id: 1,
    },
    3,
  );
  const line = state.lines[0]!;

  it("shows both lines when translations are on", () => {
    expect(lineDisplay(line, true)).toEqual({
      original: "次の駅で降りましょう。",
      translation: "我們在下一站下車吧。",
      originalOnly: false,
    });
  });

  it("promotes the original and leaves no empty translation line when off", () => {
    expect(lineDisplay(line, false)).toEqual({ original: "次の駅で降りましょう。", translation: "", originalOnly: true });
    // The translation stays in the state, so switching back shows it again.
    expect(line.translation).toBe("我們在下一站下車吧。");
  });
});
