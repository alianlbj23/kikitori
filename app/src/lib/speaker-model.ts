/**
 * The speaker-label choice (settings page) and the model block (models page).
 *
 * Role: pure decisions behind the UI of docs/specs/speaker-labels.md, the same
 * shape as lib/furigana.ts. Choosing labels needs the speaker embedding model:
 * when it is missing the choice first starts `models.download` (kind
 * `speaker`) and is saved once that download is done. The setting is read when
 * a session starts, so it is saved with `settings.set` (no session method).
 * Nothing here touches the DOM or the engine, so every state is testable.
 */

import { formatBytes } from "./format";
import type { StringKey } from "./i18n";
import { SPEAKER_COUNT_RANGE } from "./settings";
import type { DownloadProgressEvent, Settings, SpeakerModel } from "./types";

/** `models.download` / `models.delete` id of the model (the engine's `SPEAKER_MODEL_ID`). */
export const SPEAKER_MODEL_ID = "speaker-embedding";

/** What the settings select offers: off, guess how many, or a fixed count. */
export type SpeakerChoice = "off" | "auto" | `${number}`;

/** Every option of the select, in order. */
export const SPEAKER_CHOICES: readonly SpeakerChoice[] = [
  "off",
  "auto",
  ...Array.from(
    { length: SPEAKER_COUNT_RANGE[1] - SPEAKER_COUNT_RANGE[0] + 1 },
    (_, i) => `${SPEAKER_COUNT_RANGE[0] + i}` as SpeakerChoice,
  ),
];

export interface StatusLine {
  key: StringKey;
  params: Record<string, string | number>;
  /** Shown as an error rather than as help. */
  error: boolean;
}

/** The select's value for saved settings (ignoring whether the model is there). */
export function choiceOf(speakers: Settings["speakers"]): SpeakerChoice {
  if (!speakers.enabled) return "off";
  return speakers.count === null ? "auto" : (`${speakers.count}` as SpeakerChoice);
}

/** Parses a select value; null for anything the select does not offer. */
export function parseChoice(value: string): SpeakerChoice | null {
  return (SPEAKER_CHOICES as readonly string[]).includes(value) ? (value as SpeakerChoice) : null;
}

/** The `settings.set` patch for a choice; "off" keeps the count for next time. */
export function speakersPatch(choice: SpeakerChoice): { speakers: Partial<Settings["speakers"]> } {
  if (choice === "off") return { speakers: { enabled: false } };
  return { speakers: { enabled: true, count: choice === "auto" ? null : Number(choice) } };
}

export interface SpeakerSelectInput {
  speakers: Settings["speakers"];
  /** `models.list.speaker_model`; null before the list arrived or from an engine without it. */
  model: SpeakerModel | null;
  /** The models list has been read (null model then means "not supported"). */
  modelsLoaded: boolean;
  /** Latest `download_progress` of the model, if any. */
  download: DownloadProgressEvent | null;
  /** The choice waiting for the model to finish downloading. */
  pending: SpeakerChoice | null;
  /** A `settings.set` is in flight. */
  busy: boolean;
  engineReady: boolean;
}

export interface SpeakerSelectView {
  value: SpeakerChoice;
  disabled: boolean;
  /** Download percentage while the model downloads for this choice, else null. */
  percent: number | null;
  status: StatusLine | null;
}

function percentOf(done: number, total: number): number {
  return total > 0 ? Math.min(100, Math.floor((done / total) * 100)) : 0;
}

/** What the settings-page select shows. */
export function speakerSelectView(input: SpeakerSelectInput): SpeakerSelectView {
  const { model, download } = input;
  if (input.modelsLoaded && model === null) {
    return { value: "off", disabled: true, percent: null, status: { key: "speakers.unsupported", params: {}, error: false } };
  }
  const saved = choiceOf(input.speakers);
  if (download?.state === "downloading") {
    const percent = percentOf(download.downloaded_bytes, download.total_bytes);
    return {
      // The user's intent shows while the model is on its way.
      value: input.pending ?? saved,
      disabled: true,
      percent,
      status: {
        key: "speakers.downloading",
        params: { percent, done: formatBytes(download.downloaded_bytes), total: formatBytes(download.total_bytes) },
        error: false,
      },
    };
  }
  const failed: StatusLine | null =
    download?.state === "error"
      ? { key: "speakers.downloadFailed", params: { error: download.error ?? "" }, error: true }
      : null;
  return {
    // Saved as on but the model is gone (deleted elsewhere): shown off; choosing again downloads it.
    value: model === null || model.installed ? saved : "off",
    disabled: !input.engineReady || input.busy || model === null,
    percent: null,
    status: failed,
  };
}

/** What choosing an option does: save at once, or download the model first. */
export function speakerChoiceAction(choice: SpeakerChoice, model: SpeakerModel | null): "save" | "download" | "unsupported" {
  if (choice === "off") return "save";
  if (model === null) return "unsupported";
  return model.installed ? "save" : "download";
}

/** Whether a `download_progress` event is about the speaker model. */
export function isSpeakerDownload(progress: DownloadProgressEvent): boolean {
  // Older engines send no kind; the id is unique among downloads either way.
  return progress.kind === "speaker" || (progress.kind === null && progress.model === SPEAKER_MODEL_ID);
}

/** A finished model download: save the choice the user was waiting with. */
export function afterSpeakerDownload(progress: DownloadProgressEvent, pending: SpeakerChoice | null): "save" | "failed" | "none" {
  if (progress.model !== SPEAKER_MODEL_ID) return "none";
  if (progress.state === "done") return pending !== null && pending !== "off" ? "save" : "none";
  if (progress.state === "error") return "failed";
  return "none";
}

export interface SpeakerBlockView {
  lines: StatusLine[];
  /** Null when there is nothing to delete; otherwise whether the button can be pressed. */
  deletable: null | { enabled: boolean };
}

/** The models page block: name, use of disk, license and the Delete button. */
export function speakerBlockView(
  model: SpeakerModel,
  ctx: { downloading: boolean; deleting: boolean; engineReady: boolean },
): SpeakerBlockView {
  const lines: StatusLine[] = [
    { key: "speakers.lede", params: { name: model.name }, error: false },
    model.installed
      ? { key: "speakers.installed", params: { size: formatBytes(model.disk_bytes ?? model.installed_bytes) }, error: false }
      : { key: "speakers.notInstalled", params: { size: formatBytes(model.download_bytes) }, error: false },
    { key: "speakers.license", params: { license: model.license }, error: false },
  ];
  // Not downloaded and nothing on disk; a leftover partial download (disk_bytes) can still be deleted.
  if (!model.installed && model.disk_bytes === null) return { lines, deletable: null };
  // A running session read the model into memory when it started; deleting does not stop it.
  return { lines, deletable: { enabled: ctx.engineReady && !ctx.deleting && !ctx.downloading } };
}
