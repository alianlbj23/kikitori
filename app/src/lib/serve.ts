/**
 * This computer as a compute host (docs/specs/compute-capacity.md 八): pure logic.
 *
 * Role: turns the `serving` events (and the last `remote.status` as a fallback)
 * into what the 運算主機 panel on the subtitle page shows — a status dot and a
 * headline in plain words, the elapsed time and sentence count of the other
 * computer's session (never its text), this computer's load (lib/load.ts, the
 * same view as the rail's), the models in use — and decides when 開始 is
 * blocked; it also words the `host_changed` notice. `main.ts` only renders.
 */

import { joinList, t, tp, type StringKey } from "./i18n";
import { emptyLoad, loadView, pushReading, type LoadState, type LoadView } from "./load";
import type { HostChangedEvent, RemoteServerStatus, ServerPeer, ServingEvent, ServingSession } from "./remote";

export interface ServingState {
  /** A `serving` event arrived since serving started; before that `remote.status` speaks. */
  readonly received: boolean;
  readonly peer: ServerPeer | null;
  readonly session: ServingSession | null;
  /** This computer's load during the other computer's session (history included). */
  readonly load: LoadState;
}

export function emptyServing(): ServingState {
  return { received: false, peer: null, session: null, load: emptyLoad() };
}

/**
 * Applies one `serving` event. The chart starts afresh with each new session
 * (another `started_at_ms`); a reading without a session is not drawn.
 */
export function applyServing(state: ServingState, ev: ServingEvent): ServingState {
  const sameSession = ev.session !== null && state.session?.started_at_ms === ev.session.started_at_ms;
  let load = sameSession ? state.load : emptyLoad();
  if (ev.session !== null && ev.metrics !== null) load = pushReading(load, ev.metrics);
  return { received: true, peer: ev.peer, session: ev.session, load };
}

/** The other computer using this one: the `serving` events once they arrive, else `remote.status`. */
export function servingPeer(state: ServingState, status: RemoteServerStatus | null): ServerPeer | null {
  return state.received ? state.peer : (status?.peer ?? null);
}

/** Another computer holds this one's GPU (or will come back to it): 開始 here must wait. */
export function startBlocked(state: ServingState, status: RemoteServerStatus | null): boolean {
  return servingPeer(state, status) !== null;
}

/** "12:34", or "1:02:03" from an hour on. */
export function formatElapsed(ms: number): string {
  const total = Math.max(0, Math.floor(ms / 1000));
  const s = total % 60;
  const m = Math.floor(total / 60) % 60;
  const h = Math.floor(total / 3600);
  const pad = (n: number): string => String(n).padStart(2, "0");
  return h > 0 ? `${h}:${pad(m)}:${pad(s)}` : `${pad(m)}:${pad(s)}`;
}

/** The status dot: waiting (calm), working for someone (live), a dropped link, or a problem. */
export type ServeTone = "waiting" | "busy" | "reconnect" | "error" | "off";

export interface ServeModels {
  asr: { fast: string; final: string };
  translation: { fast: string; final: string };
}

export interface ServePanelView {
  tone: ServeTone;
  /** The big line without the time: also the mode bar's text. */
  headline: string;
  /** "正在替 A 處理・已處理 3 句・12:34"; the headline otherwise. */
  title: string;
  /** The smaller line under it, or null. */
  detail: string | null;
  /** This computer's load while the other computer's session runs; null otherwise. */
  load: LoadView | null;
  /** "辨識：即時 X・完整 Y" and "翻譯：…". */
  asrModels: string;
  translationModels: string;
  /** 配對新電腦 is offered (listening and nobody connected). */
  canPair: boolean;
}

export interface ServePanelInput {
  serving: ServingState;
  status: RemoteServerStatus | null;
  models: ServeModels;
  nowMs: number;
}

function modelName(name: string): string {
  return name === "" ? t("serve.modelUnset") : name;
}

export function servePanelView(input: ServePanelInput): ServePanelView {
  const { serving, status, models, nowMs } = input;
  const peer = servingPeer(serving, status);
  const base = {
    load: null,
    asrModels: t("serve.asrModels", { fast: modelName(models.asr.fast), final: modelName(models.asr.final) }),
    translationModels: t("serve.translationModels", {
      fast: modelName(models.translation.fast),
      final: modelName(models.translation.final),
    }),
  };
  if (peer?.state === "waiting_resume") {
    const headline = t("serve.headReconnect", { name: peer.name });
    return { ...base, tone: "reconnect", headline, title: headline, detail: t("serve.detailReconnect"), canPair: false };
  }
  if (peer) {
    const session = serving.session;
    if (!session) {
      const headline = t("serve.headConnected", { name: peer.name });
      return { ...base, tone: "busy", headline, title: headline, detail: t("serve.detailPrivate"), canPair: false };
    }
    const headline = tp("serve.headBusy", session.sentences, { name: peer.name });
    return {
      ...base,
      tone: "busy",
      headline,
      title: t("serve.withTime", { text: headline, time: formatElapsed(nowMs - session.started_at_ms) }),
      detail: t("serve.detailPrivate"),
      load: loadView(serving.load),
      canPair: false,
    };
  }
  if (status?.error) {
    const headline = t("serve.headError");
    return { ...base, tone: "error", headline, title: headline, detail: status.error, canPair: false };
  }
  if (status && !status.listening) {
    const headline = t("compute.hintServeOff");
    return { ...base, tone: "off", headline, title: headline, detail: null, canPair: false };
  }
  const headline = t("compute.hintServeWaiting");
  return { ...base, tone: "waiting", headline, title: headline, detail: t("serve.detailWaiting"), canPair: true };
}

// ---------------------------------------------------------------- host_changed

export interface ModelSettings {
  asr: { fast_model: string; final_model: string };
  translation: { fast_model: string; final_model: string };
}

const WHAT: readonly { key: StringKey; before: (s: ModelSettings) => string; after: (e: HostChangedEvent) => string }[] = [
  { key: "serve.what.asrFinal", before: (s) => s.asr.final_model, after: (e) => e.asr.final },
  { key: "serve.what.asrFast", before: (s) => s.asr.fast_model, after: (e) => e.asr.fast },
  { key: "serve.what.translationFinal", before: (s) => s.translation.final_model, after: (e) => e.translation.final },
  { key: "serve.what.translationFast", before: (s) => s.translation.fast_model, after: (e) => e.translation.fast },
];

/**
 * The notice for a `host_changed` event: "A 把完整辨識模型改成 large-v3",
 * comparing with the settings shown before; every change is named when there
 * are several, and a general line when nothing differs (already re-read).
 */
export function hostChangedText(before: ModelSettings | null, ev: HostChangedEvent): string {
  const name = ev.by.trim() === "" ? t("pair.unknownPeer") : ev.by;
  const changes = before
    ? WHAT.filter((w) => w.after(ev) !== "" && w.after(ev) !== w.before(before)).map((w) => ({
        what: t(w.key),
        model: w.after(ev),
      }))
    : [];
  if (changes.length === 1) return t("serve.changedOne", { name, what: changes[0]!.what, model: changes[0]!.model });
  if (changes.length > 1) {
    const list = joinList(changes.map((c) => t("serve.changeItem", { what: c.what, model: c.model })));
    return t("serve.changedMany", { name, changes: list });
  }
  return t("serve.changedOther", { name, asr: ev.asr.final });
}
