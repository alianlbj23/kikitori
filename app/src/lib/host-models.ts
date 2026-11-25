/**
 * Managing the compute host's models from this computer (docs/specs/compute-capacity.md 七).
 *
 * Role: the pure logic behind the models page's 運算主機的模型 and the rail's
 * model menu while recognition runs on another computer — the menus built from
 * the compute host's `HostInfo` (only downloaded models, each labelled 建議 and
 * how well it fits), when a choice needs a second "yes", the warnings for a
 * combination that may not keep up, the not-yet-downloaded list with its
 * `host_download` progress, the `remote_host_set_models` arguments and the
 * plain words for its errors. `main.ts` only renders these and calls the commands.
 *
 * The compute host never refuses a model for being too heavy (the user's
 * choice wins), so everything about load here is a warning, not a gate.
 */

import { describeStatusError } from "./format";
import { t, type StringKey } from "./i18n";
import { formatGb } from "./load";
import type { HostDownloadProgress, HostInfo, ModelFit } from "./remote";

export type ModelSlot = "fast" | "final";
/** The two kinds of models `remote_host_set_models` changes. */
export type ModelKind = "asr" | "translation";

/**
 * How well the compute host would run `id` in `slot`, next to its current
 * model in the other slot — always the compute host's own report (`fit` for
 * the final pass, `fit_as_fast` for the real-time pass), never estimated here:
 * only it knows its free memory and its translation models. null (無法判斷)
 * when it could not tell, does not list the model, or is too old to report it.
 */
export function slotFit(host: HostInfo, slot: ModelSlot, id: string): ModelFit | null {
  const model = host.models.find((m) => m.id === id);
  if (model === undefined) return null;
  return slot === "final" ? model.fit : model.fit_as_fast;
}

/** How well the compute host's current combination fits (its report for the current final model). */
export function comboFit(host: HostInfo): ModelFit | null {
  return host.models.find((m) => m.id === host.current.final)?.fit ?? null;
}

const FIT_TEXT: Readonly<Record<ModelFit, StringKey>> = {
  ok: "host.fit.ok",
  tight: "host.fit.tight",
  over: "host.fit.over",
};

export function fitText(fit: ModelFit | null): string {
  return t(fit ? FIT_TEXT[fit] : "host.fit.unknown");
}

/** Marks a too-heavy option in the menu text too: an `<option>` cannot be styled reliably. */
const OVER_MARK = "⚠ ";

export interface HostModelOption {
  value: string;
  label: string;
  fit: ModelFit | null;
}

/**
 * A recognition menu: the models the compute host has downloaded, in its
 * order, each labelled 建議 and how well it fits. 超出負荷 options stay listed
 * and selectable, only marked. The current model is always there, even when
 * the compute host no longer lists it as downloaded (marked so), so the menu
 * never silently shows another value.
 */
export function hostSlotOptions(host: HostInfo, slot: ModelSlot): HostModelOption[] {
  const recommended = host.recommended?.[slot] ?? null;
  const options = host.models
    .filter((m) => m.downloaded)
    .map((m): HostModelOption => {
      const fit = slotFit(host, slot, m.id);
      const label = t(recommended === m.id ? "host.optionRecommended" : "host.option", {
        model: m.id,
        fit: fitText(fit),
      });
      return { value: m.id, label: fit === "over" ? OVER_MARK + label : label, fit };
    });
  const current = host.current[slot];
  if (!options.some((o) => o.value === current)) {
    options.unshift({ value: current, label: t("host.optionMissing", { model: current }), fit: null });
  }
  return options;
}

/** A choice that needs a second "yes": the compute host would probably not keep up. */
export function needsOverConfirm(host: HostInfo, slot: ModelSlot, id: string): boolean {
  return id !== host.current[slot] && slotFit(host, slot, id) === "over";
}

/** The question before a 超出負荷 model is used, with what will happen in plain words. */
export function overConfirmText(host: HostInfo, model: string): string {
  return t(host.device === "cuda" ? "hostModels.overConfirm" : "hostModels.overConfirmCpu", { model });
}

/** The line under a menu whose current model may be tight (no question asked); null otherwise. */
export function tightHint(host: HostInfo, slot: ModelSlot): string | null {
  const model = host.current[slot];
  return slotFit(host, slot, model) === "tight" ? t("hostModels.tight", { model }) : null;
}

export interface ComboWarning {
  tone: "bad" | "info";
  text: string;
}

/** Shown on the model block for as long as the current combination is too heavy (or tight). */
export function comboWarning(host: HostInfo): ComboWarning | null {
  const fit = comboFit(host);
  if (fit === "over") return { tone: "bad", text: t("hostModels.comboOver") };
  if (fit === "tight") return { tone: "info", text: t("hostModels.comboTight") };
  return null;
}

/**
 * A translation menu: the models installed on the compute host's translation
 * server; null when it did not answer (nothing can be chosen then). The current
 * model stays listed when it is not installed, and "" when none is set.
 */
export function translationOptions(host: HostInfo, slot: ModelSlot): HostModelOption[] | null {
  const installed = host.translation_models;
  if (installed === null) return null;
  const options = installed.map((m): HostModelOption => ({ value: m, label: m, fit: null }));
  const current = host.current.translation?.[slot] ?? "";
  if (current === "") options.unshift({ value: "", label: t("hostModels.translationUnset"), fit: null });
  else if (!installed.includes(current)) {
    options.unshift({ value: current, label: t("hostModels.translationMissing", { model: current }), fit: null });
  }
  return options;
}

/** The current translation model of a slot ("" when none or an older compute host). */
export function currentTranslation(host: HostInfo, slot: ModelSlot): string {
  return host.current.translation?.[slot] ?? "";
}

/** `remote_host_set_models` arguments that change one model. */
export function setModelArgs(kind: ModelKind, slot: ModelSlot, model: string): Record<string, unknown> {
  return { [kind]: { [slot === "fast" ? "fast_model" : "final_model"]: model } };
}

// ---------------------------------------------------------------- downloads on the compute host

export interface HostDownloadRow {
  id: string;
  /** "需要 2.1 GB", or null when the compute host gave no size. */
  need: string | null;
  fit: ModelFit | null;
  fitText: string;
  /** Downloading it is allowed, but asks once more (it would be 超出負荷 as the final model). */
  heavy: boolean;
  progress: HostDownloadProgress | null;
}

/** The models the compute host has not downloaded yet, with any download in flight. */
export function hostDownloadRows(
  host: HostInfo,
  downloads: ReadonlyMap<string, HostDownloadProgress>,
): HostDownloadRow[] {
  return host.models
    .filter((m) => !m.downloaded)
    .map((m) => ({
      id: m.id,
      need: m.need_mb > 0 ? t("hostModels.need", { memory: formatGb(m.need_mb) }) : null,
      fit: m.fit,
      fitText: fitText(m.fit),
      heavy: m.fit === "over",
      progress: downloads.get(m.id) ?? null,
    }));
}

/** Applies a `host_download` event; a finished one is dropped (the next report lists the model). */
export function applyHostDownload(
  downloads: ReadonlyMap<string, HostDownloadProgress>,
  ev: HostDownloadProgress,
): Map<string, HostDownloadProgress> {
  const next = new Map(downloads);
  if (ev.state === "done") next.delete(ev.model);
  else next.set(ev.model, ev);
  return next;
}

/** 0–100, or null while the size is not known yet. */
export function downloadPercent(p: HostDownloadProgress): number | null {
  if (!(p.total_bytes > 0)) return null;
  return Math.min(100, Math.floor((p.downloaded_bytes / p.total_bytes) * 100));
}

/** The text next to a download's progress bar. */
export function downloadText(p: HostDownloadProgress): string {
  if (p.state === "error") {
    return t("hostModels.downloadFailed", { model: p.model, reason: p.error ?? t("common.unknownReason") });
  }
  const percent = downloadPercent(p);
  return percent === null ? t("hostModels.downloadingUnknown") : t("hostModels.downloading", { percent });
}

// ---------------------------------------------------------------- errors

const MANAGE_CODES: Readonly<Record<string, StringKey>> = {
  remote_unknown_model: "hostModels.error.unknownModel",
  remote_model_not_downloaded: "hostModels.error.notDownloaded",
  remote_translation_model_missing: "hostModels.error.translationMissing",
  remote_download_in_progress: "hostModels.error.downloadInProgress",
  remote_error: "hostModels.error.other",
};

/** Connection problems that already have plain words (the session's). */
const CONNECTION_CODES = [
  "remote_not_configured",
  "remote_unreachable",
  "remote_not_paired",
  "remote_incompatible",
  "remote_identity_error",
];

/**
 * Why a change or download on the compute host failed, in plain words;
 * `fallback` (lib/api describeError) for anything else.
 */
export function hostManageErrorText(code: string | null, fallback: string): string {
  if (code === null) return fallback;
  if (Object.hasOwn(MANAGE_CODES, code)) return t(MANAGE_CODES[code]!);
  if (CONNECTION_CODES.includes(code)) return describeStatusError(code, null);
  return fallback;
}
