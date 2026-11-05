/**
 * In-app updates (tauri-plugin-updater, driven from Rust).
 *
 * Role: checks GitHub Releases' `latest.json` (endpoint and public key are in
 * tauri.conf.json), then lets the user download, install and restart. Check and
 * install go through the app's `update_check`／`update_install` commands
 * (`src-tauri/src/updater.rs`), which shut the engine down before the Windows
 * installer replaces its files. Copy comes from the i18n dictionary; the
 * caller decides where messages appear.
 */

import { relaunch } from "@tauri-apps/plugin-process";

import { api } from "../lib/api";
import { t } from "../lib/i18n";
import type { UpdateInfo } from "../lib/types";

const CHECK_TIMEOUT_MS = 15_000;
const DOWNLOAD_TIMEOUT_MS = 10 * 60_000;

export interface UpdateUi {
  /** Offer an available update; `install` downloads and installs it. */
  offer(version: string, notes: string | null, install: () => Promise<void>): void;
  progress(percent: number | null): void;
  /** Installed; `restart` relaunches the app. */
  installed(version: string, restart: () => Promise<void>): void;
  failed(message: string): void;
}

function describe(e: unknown): string {
  return e instanceof Error ? e.message : String(e);
}

/**
 * Checks for an update. Returns the available version or null; throws only for
 * unexpected programming errors — network failures are reported via `ui.failed`
 * when `reportFailures` is set (manual checks) and logged otherwise.
 */
export async function checkForUpdate(ui: UpdateUi, reportFailures: boolean): Promise<string | null> {
  let update: UpdateInfo | null;
  try {
    update = await api.updateCheck(CHECK_TIMEOUT_MS);
  } catch (e) {
    console.error("update check failed:", e);
    if (reportFailures) ui.failed(t("update.checkFailed", { reason: describe(e) }));
    return null;
  }
  if (!update) return null;
  const found = update;
  ui.offer(found.version, found.notes, async () => {
    let total: number | null = null;
    let received = 0;
    try {
      await api.updateInstall((event) => {
        if (event.event === "Started") {
          total = event.contentLength;
          ui.progress(total ? 0 : null);
        } else if (event.event === "Progress") {
          received += event.chunkLength;
          ui.progress(total ? Math.min(100, Math.floor((received / total) * 100)) : null);
        }
      }, DOWNLOAD_TIMEOUT_MS);
    } catch (e) {
      console.error("update install failed:", e);
      ui.failed(t("update.failed", { reason: describe(e) }));
      return;
    }
    // On Windows the app has exited before this point (engine stopped first,
    // then the installer runs); macOS and Linux get here and offer a restart.
    ui.installed(found.version, async () => {
      try {
        await relaunch();
      } catch (e) {
        console.error("relaunch failed:", e);
        ui.failed(t("update.relaunchFailed", { reason: describe(e) }));
      }
    });
  });
  return found.version;
}
