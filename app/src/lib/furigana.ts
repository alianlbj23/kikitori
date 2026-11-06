/**
 * The furigana switch (settings page) and the dictionary block (models page).
 *
 * Role: pure decisions behind the UI of docs/specs/furigana.md. Turning
 * furigana on needs the dictionary: when it is missing the switch first starts
 * `models.download` (kind `furigana`) and turns furigana on once that download
 * is done; the engine is authoritative (`session.set_furigana` refuses with
 * `model_not_downloaded`, `models.delete` with `model_in_use`). Nothing here
 * touches the DOM or the engine, so every state is testable.
 */

import { formatBytes } from "./format";
import type { StringKey } from "./i18n";
import type { DownloadProgressEvent, FuriganaDictionary } from "./types";

/** `models.download` / `models.delete` id of the dictionary (the engine's `DICTIONARY_ID`). */
export const FURIGANA_DICTIONARY_ID = "furigana-dictionary";

export interface FuriganaSwitchInput {
  /** Saved `furigana.enabled`. */
  enabled: boolean;
  /** `models.list.furigana`; null before the list arrived or from an engine without furigana. */
  dictionary: FuriganaDictionary | null;
  /** The models list has been read (null dictionary then means "not supported"). */
  modelsLoaded: boolean;
  /** Latest `download_progress` of the dictionary, if any. */
  download: DownloadProgressEvent | null;
  /** The user asked to turn furigana on and the dictionary is still downloading. */
  pendingEnable: boolean;
  /** A `session.set_furigana` request is in flight. */
  busy: boolean;
  engineReady: boolean;
}

export interface StatusLine {
  key: StringKey;
  params: Record<string, string | number>;
  /** Shown as an error rather than as help. */
  error: boolean;
}

export interface FuriganaSwitchView {
  checked: boolean;
  disabled: boolean;
  /** Download percentage while the dictionary downloads for this switch, else null. */
  percent: number | null;
  status: StatusLine | null;
}

function percentOf(done: number, total: number): number {
  return total > 0 ? Math.min(100, Math.floor((done / total) * 100)) : 0;
}

/** What the settings-page switch shows. */
export function furiganaSwitchView(input: FuriganaSwitchInput): FuriganaSwitchView {
  const { dictionary, download } = input;
  if (input.modelsLoaded && dictionary === null) {
    return {
      checked: false,
      disabled: true,
      percent: null,
      status: { key: "furigana.unsupported", params: {}, error: false },
    };
  }
  const downloading = download?.state === "downloading";
  const blocked = !input.engineReady || input.busy || downloading;
  if (downloading && download) {
    return {
      // The user's intent shows while the dictionary is on its way.
      checked: input.enabled || input.pendingEnable,
      disabled: true,
      percent: percentOf(download.downloaded_bytes, download.total_bytes),
      status: {
        key: "furigana.downloading",
        params: {
          percent: percentOf(download.downloaded_bytes, download.total_bytes),
          done: formatBytes(download.downloaded_bytes),
          total: formatBytes(download.total_bytes),
        },
        error: false,
      },
    };
  }
  const failed: StatusLine | null =
    download?.state === "error"
      ? { key: "furigana.downloadFailed", params: { error: download.error ?? "" }, error: true }
      : null;
  return {
    // Saved as on but the dictionary is gone (deleted elsewhere): shown off, turning it on downloads again.
    checked: input.enabled && (dictionary === null || dictionary.installed),
    disabled: blocked || dictionary === null,
    percent: null,
    status: failed,
  };
}

/** What turning the switch on does: enable at once, or download first. */
export function furiganaEnableAction(dictionary: FuriganaDictionary | null): "enable" | "download" | "unsupported" {
  if (dictionary === null) return "unsupported";
  return dictionary.installed ? "enable" : "download";
}

/** A finished dictionary download: turn furigana on if the user was waiting for it. */
export function afterFuriganaDownload(
  progress: DownloadProgressEvent,
  pendingEnable: boolean,
): "enable" | "failed" | "none" {
  if (progress.model !== FURIGANA_DICTIONARY_ID) return "none";
  if (progress.state === "done") return pendingEnable ? "enable" : "none";
  if (progress.state === "error") return "failed";
  return "none";
}

/** Whether a `download_progress` event is about the furigana dictionary. */
export function isFuriganaDownload(progress: DownloadProgressEvent): boolean {
  // Older engines send no kind; the id is unique among downloads either way.
  return progress.kind === "furigana" || (progress.kind === null && progress.model === FURIGANA_DICTIONARY_ID);
}

export interface FuriganaBlockView {
  lines: StatusLine[];
  /** Null when there is nothing to delete; otherwise whether the button can be pressed. */
  deletable: null | { enabled: boolean; reason: StatusLine | null };
}

/** The models page block: version, use of disk, license and the Delete button. */
export function furiganaBlockView(
  dictionary: FuriganaDictionary,
  ctx: { downloading: boolean; deleting: boolean; engineReady: boolean },
): FuriganaBlockView {
  const lines: StatusLine[] = [
    { key: "furigana.lede", params: { version: dictionary.version }, error: false },
    dictionary.installed
      ? {
          key: "furigana.installed",
          params: { size: formatBytes(dictionary.disk_bytes ?? dictionary.installed_bytes) },
          error: false,
        }
      : { key: "furigana.notInstalled", params: { size: formatBytes(dictionary.download_bytes) }, error: false },
    { key: "furigana.license", params: { license: dictionary.license }, error: false },
  ];
  if (!dictionary.installed) return { lines, deletable: null };
  const reason: StatusLine | null = dictionary.in_use
    ? { key: "furigana.inUse", params: {}, error: false }
    : null;
  return {
    lines,
    deletable: {
      enabled: ctx.engineReady && !ctx.deleting && !ctx.downloading && !dictionary.in_use,
      reason,
    },
  };
}
