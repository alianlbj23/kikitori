/**
 * Downloading missing models when the user presses "開始", then starting.
 *
 * Role: choosing languages never downloads anything (languages-i18n.md); the
 * first real start does. When the chosen combination lacks models — known
 * before starting from the model list, or reported by the engine afterwards
 * (`model_not_downloaded`, `translation_model_missing` with `data.models`) —
 * this state machine downloads each one (ASR through `models.download`,
 * translation through `models.pull_translation`), follows the
 * `download_progress` events, and starts the session again once all are done.
 * A failure stops with a retry; a translation server that cannot pull for the
 * user (not Ollama, unreachable, rejecting the API key) stops with instructions.
 * Pure logic with injected engine calls, so `main.ts` only renders the state.
 */

import { formatBytes, percent } from "./format";
import { joinList, t } from "./i18n";
import type { DownloadProgressEvent } from "./types";

export type StartItemKind = "asr" | "translation";

export interface StartItemRequest {
  kind: StartItemKind;
  model: string;
  /** Known size (ASR model list); null when unknown. */
  sizeBytes: number | null;
}

export type StartItemState = "pending" | "downloading" | "done" | "error";

export interface StartItem extends StartItemRequest {
  state: StartItemState;
  downloadedBytes: number;
  totalBytes: number;
  /** Why it failed, already in the interface language (or the engine's text). */
  error: string | null;
}

/** Why translation models cannot be pulled for the user. */
export type Blocker = "pull_unsupported" | "server_unreachable" | "auth_failed";

export type StartPhase =
  /** Nothing to do (or cancelled). */
  | "idle"
  | "downloading"
  /** At least one download failed; retry offered. */
  | "failed"
  /** The translation server cannot pull for us; instructions shown. */
  | "blocked"
  /** Everything is downloaded; the session is being started. */
  | "starting";

export interface StartDownloadsState {
  phase: StartPhase;
  items: StartItem[];
  blocker: Blocker | null;
}

export interface StartDownloadsDeps {
  downloadAsr(model: string): Promise<void>;
  pullTranslation(model: string): Promise<void>;
  /** Starts the session again; its own failures are reported by the caller. */
  start(): Promise<void>;
  /** The engine error code of a failed call, if it is one. */
  errorCode(e: unknown): string | null;
  /** A failure as a sentence in the interface language. */
  describe(e: unknown): string;
  /** The state changed: render it. */
  onChange(state: StartDownloadsState): void;
}

const BLOCKERS: Readonly<Record<string, Blocker>> = {
  pull_unsupported: "pull_unsupported",
  translation_server_unreachable: "server_unreachable",
  translation_server_auth_failed: "auth_failed",
};

function key(kind: StartItemKind, model: string): string {
  return `${kind}:${model}`;
}

export class StartDownloads {
  state: StartDownloadsState = { phase: "idle", items: [], blocker: null };
  /** Models finished during this start; asked for again = the engine still misses them. */
  private completed = new Set<string>();

  constructor(private readonly deps: StartDownloadsDeps) {}

  /** Downloads or the automatic start are under way: the start button must wait. */
  get active(): boolean {
    return this.state.phase === "downloading" || this.state.phase === "starting";
  }

  /** This start is waiting for `model` (its progress is shown in the panel, not as notices). */
  tracks(kind: StartItemKind, model: string): boolean {
    return this.state.phase !== "idle" && this.state.items.some((i) => i.kind === kind && i.model === model);
  }

  /** The user pressed "開始" anew: forget what earlier attempts downloaded. */
  reset(): void {
    this.completed.clear();
    this.set({ phase: "idle", items: [], blocker: null });
  }

  /**
   * Downloads `requests` (duplicates dropped), then starts the session. A model
   * already downloaded during this start is not fetched again: the engine
   * asking for it twice means something else is wrong, so it is reported.
   */
  begin(requests: readonly StartItemRequest[]): void {
    const unique = new Map<string, StartItemRequest>();
    for (const r of requests) unique.set(key(r.kind, r.model), r);
    const items: StartItem[] = [...unique.values()].map((r) => {
      const repeated = this.completed.has(key(r.kind, r.model));
      return {
        ...r,
        state: repeated ? "error" : "pending",
        downloadedBytes: 0,
        totalBytes: r.sizeBytes ?? 0,
        error: repeated ? t("startdl.stillMissing", { model: r.model }) : null,
      };
    });
    this.set({ phase: "downloading", items, blocker: null });
    for (const item of items) if (item.state === "pending") void this.request(item);
    this.settle();
  }

  /** Retries what failed (or was blocked), keeping finished items. */
  retry(): void {
    if (this.state.phase !== "failed" && this.state.phase !== "blocked") return;
    const items = this.state.items.map((i) =>
      i.state === "error" || i.state === "pending" ? { ...i, state: "pending" as const, error: null } : i,
    );
    // A model the engine asked for twice is retried too: the user decided to.
    for (const i of items) this.completed.delete(key(i.kind, i.model));
    this.set({ phase: "downloading", items, blocker: null });
    for (const item of items) if (item.state === "pending") void this.request(item);
    this.settle();
  }

  /** Stops waiting (downloads already sent keep running in the engine). */
  cancel(): void {
    this.set({ phase: "idle", items: [], blocker: null });
  }

  /** A `download_progress` event; events for other downloads are ignored. */
  onProgress(ev: DownloadProgressEvent): void {
    if (this.state.phase !== "downloading" && this.state.phase !== "failed" && this.state.phase !== "blocked") return;
    // Older engines send no kind; then only ASR items can match (they had no pulls).
    const kind: StartItemKind | null =
      ev.kind === "translation" ? "translation" : ev.kind === "asr" || ev.kind === null ? "asr" : null;
    if (kind === null) return;
    const found = this.state.items.find((i) => i.kind === kind && i.model === ev.model);
    if (!found || found.state === "done") return;
    const next: StartItem = {
      ...found,
      downloadedBytes: ev.downloaded_bytes,
      // Ollama reports layer by layer: the total may grow during the pull.
      totalBytes: ev.total_bytes || found.totalBytes,
      state: ev.state === "done" ? "done" : ev.state === "error" ? "error" : "downloading",
      error: ev.state === "error" ? (ev.error ?? t("common.unknownReason")) : null,
    };
    if (next.state === "done") this.completed.add(key(next.kind, next.model));
    this.replaceItem(next);
    this.settle();
  }

  private async request(item: StartItem): Promise<void> {
    this.replaceItem({ ...item, state: "downloading" });
    try {
      if (item.kind === "asr") await this.deps.downloadAsr(item.model);
      else await this.deps.pullTranslation(item.model);
    } catch (e) {
      const code = this.deps.errorCode(e);
      if (code === "download_in_progress") return; // started earlier; its progress events follow
      const current = this.state.items.find((i) => i.kind === item.kind && i.model === item.model);
      if (!current || current.state === "done") return;
      const blocker = code ? BLOCKERS[code] : undefined;
      if (blocker && item.kind === "translation") this.state = { ...this.state, blocker };
      this.replaceItem({ ...current, state: "error", error: this.deps.describe(e) });
      this.settle();
    }
  }

  private replaceItem(item: StartItem): void {
    const items = this.state.items.map((i) => (i.kind === item.kind && i.model === item.model ? item : i));
    this.set({ ...this.state, items });
  }

  /** Moves to the next phase once no download is pending any more. */
  private settle(): void {
    const { items, blocker, phase } = this.state;
    if (phase === "idle" || phase === "starting") return;
    if (items.some((i) => i.state === "pending" || i.state === "downloading")) {
      if (phase !== "downloading") this.set({ ...this.state, phase: "downloading" });
      return;
    }
    if (items.some((i) => i.state === "error")) {
      this.set({ ...this.state, phase: blocker ? "blocked" : "failed" });
      return;
    }
    this.set({ ...this.state, phase: "starting" });
    void this.startSession();
  }

  private async startSession(): Promise<void> {
    try {
      await this.deps.start();
    } catch (e) {
      // `start` reports its own failures to the user; this only keeps the
      // rejection from going unhandled.
      console.error("automatic start after the downloads failed:", e);
    } finally {
      // Whatever the start reports next (running, or another missing model that
      // calls begin() again) is handled from there; this round is over.
      if (this.state.phase === "starting") this.set({ phase: "idle", items: [], blocker: null });
    }
  }

  private set(state: StartDownloadsState): void {
    this.state = state;
    this.deps.onChange(state);
  }
}

// ---------------------------------------------------------------- display

export interface StartItemView {
  label: string;
  /** 0–100 while downloading; null when there is no bar to show. */
  percent: number | null;
  text: string;
  tone: "busy" | "ok" | "bad" | "quiet";
}

export interface StartDownloadsView {
  items: StartItemView[];
  /** What happens next, or what the user has to do. */
  message: string;
  tone: "busy" | "bad";
  canRetry: boolean;
  canCancel: boolean;
}

function itemView(i: StartItem): StartItemView {
  const label = i.kind === "asr" ? t("startdl.itemAsr", { model: i.model }) : t("startdl.itemTranslation", { model: i.model });
  switch (i.state) {
    case "pending":
      return { label, percent: null, text: t("startdl.pending"), tone: "quiet" };
    case "downloading":
      return {
        label,
        percent: percent(i.downloadedBytes, i.totalBytes),
        text:
          i.totalBytes > 0
            ? t("startdl.progress", {
                percent: percent(i.downloadedBytes, i.totalBytes),
                done: formatBytes(i.downloadedBytes),
                total: formatBytes(i.totalBytes),
              })
            : t("startdl.preparing"),
        tone: "busy",
      };
    case "done":
      return { label, percent: 100, text: t("startdl.done"), tone: "ok" };
    case "error":
      return { label, percent: null, text: t("startdl.failed", { error: i.error ?? t("common.unknownReason") }), tone: "bad" };
  }
}

/** What the download panel shows; `serverUrl` is the translation service in the settings. */
export function startDownloadsView(state: StartDownloadsState, serverUrl: string): StartDownloadsView | null {
  if (state.phase === "idle") return null;
  const items = state.items.map(itemView);
  const translation = state.items.filter((i) => i.kind === "translation" && i.state === "error").map((i) => i.model);
  switch (state.phase) {
    case "downloading":
      return { items, message: t("startdl.downloading"), tone: "busy", canRetry: false, canCancel: true };
    case "starting":
      return { items, message: t("startdl.starting"), tone: "busy", canRetry: false, canCancel: false };
    case "failed":
      return { items, message: t("startdl.failedHelp"), tone: "bad", canRetry: true, canCancel: true };
    case "blocked": {
      const models = joinList(translation);
      const message =
        state.blocker === "pull_unsupported"
          ? t("startdl.pullUnsupported", { url: serverUrl, models })
          : state.blocker === "auth_failed"
            ? t("startdl.authFailed", { url: serverUrl })
            : t("startdl.serverUnreachable", { url: serverUrl });
      return { items, message, tone: "bad", canRetry: true, canCancel: true };
    }
  }
}
