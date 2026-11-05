/**
 * In-app updates through tauri-plugin-updater.
 *
 * Role: checks GitHub Releases' `latest.json` (endpoint and public key are in
 * tauri.conf.json), then lets the user download, install and restart. All copy
 * is Traditional Chinese; the caller decides where messages appear.
 */

import { relaunch } from "@tauri-apps/plugin-process";
import { check, type Update } from "@tauri-apps/plugin-updater";

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
  let update: Update | null;
  try {
    update = await check({ timeout: CHECK_TIMEOUT_MS });
  } catch (e) {
    console.error("update check failed:", e);
    if (reportFailures) ui.failed(`無法檢查更新：${describe(e)}`);
    return null;
  }
  if (!update) return null;
  const found = update;
  ui.offer(found.version, found.body ?? null, async () => {
    let total: number | null = null;
    let received = 0;
    try {
      await found.downloadAndInstall(
        (event) => {
          if (event.event === "Started") {
            total = event.data.contentLength ?? null;
            ui.progress(total ? 0 : null);
          } else if (event.event === "Progress") {
            received += event.data.chunkLength;
            ui.progress(total ? Math.min(100, Math.floor((received / total) * 100)) : null);
          }
        },
        { timeout: DOWNLOAD_TIMEOUT_MS },
      );
    } catch (e) {
      console.error("update install failed:", e);
      ui.failed(`更新失敗：${describe(e)}`);
      return;
    }
    // On Windows the installer usually closes the app itself before this point.
    ui.installed(found.version, async () => {
      try {
        await relaunch();
      } catch (e) {
        console.error("relaunch failed:", e);
        ui.failed(`無法自動重新啟動，請手動關閉後再開啟 kikitori。（${describe(e)}）`);
      }
    });
  });
  return found.version;
}
