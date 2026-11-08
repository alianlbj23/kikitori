/**
 * The "顯示翻譯" switch (settings `translation.enabled`).
 *
 * Role: one rule for both switches (subtitle log toolbar and settings page):
 * while a session is loading or running the engine must switch its pipeline
 * at once, so `session.set_translation` is used (it also saves the setting);
 * otherwise a plain `settings.set` is enough. Either way the overlay is told,
 * so it can promote the original to the primary subtitle style.
 */

import type { SettingsResult } from "./settings";
import type { EngineState, OverlaySnapshot } from "./types";

export interface TranslationToggleDeps {
  sessionSetTranslation(enabled: boolean): Promise<SettingsResult>;
  settingsSet(patch: Record<string, unknown>): Promise<SettingsResult>;
  overlaySetShowTranslation(enabled: boolean): Promise<OverlaySnapshot>;
}

/** Which engine method applies the switch in this session state. */
export function translationSwitchMethod(session: EngineState): "session.set_translation" | "settings.set" {
  return session === "running" || session === "loading" ? "session.set_translation" : "settings.set";
}

export interface TranslationToggleResult {
  settings: SettingsResult;
  /** null when the overlay could not be told (reported separately; the setting is saved). */
  overlay: OverlaySnapshot | null;
  overlayError: unknown;
}

/**
 * Saves (and, while running, applies) the switch, then updates the overlay.
 * An engine failure rejects (nothing changed); an overlay failure does not undo
 * the saved setting and is returned for the caller to report.
 */
export async function setShowTranslation(
  enabled: boolean,
  session: EngineState,
  deps: TranslationToggleDeps,
): Promise<TranslationToggleResult> {
  const settings =
    translationSwitchMethod(session) === "session.set_translation"
      ? await deps.sessionSetTranslation(enabled)
      : await deps.settingsSet({ translation: { enabled } });
  try {
    return { settings, overlay: await deps.overlaySetShowTranslation(settings.settings.translation.enabled), overlayError: null };
  } catch (e) {
    return { settings, overlay: null, overlayError: e };
  }
}
