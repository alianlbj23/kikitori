/**
 * Deleting a downloaded speech recognition model from the models page.
 *
 * Role: pure decisions behind the "Delete" button (competitive-features.md
 * F7): whether it can be pressed now, and what the confirmation says. The
 * engine is authoritative (`models.delete` refuses with `model_in_use`,
 * `model_not_downloaded`, `unknown_model`); this keeps the button honest so
 * those errors are the exception. Translation models live on the user's
 * Ollama and are never deleted from here.
 */

import { formatBytes } from "./format";
import { joinList, t } from "./i18n";
import type { AsrModel, ModelRole } from "./types";

export type DeleteBlocked = "in_use" | "downloading" | "deleting" | "engine_not_ready";

export interface DeleteContext {
  /** The saved combination's ASR models. */
  fastModel: string | null;
  finalModel: string | null;
  /** A session is loading or running (it holds its models). */
  sessionActive: boolean;
  /** A download of this model is in progress. */
  downloading: boolean;
  /** A delete of this model is in flight. */
  deleting: boolean;
  engineReady: boolean;
}

export interface DeleteOffer {
  /** Null when the button can be pressed. */
  blocked: DeleteBlocked | null;
  /** Roles the saved settings use this model for (the confirmation mentions them). */
  roles: ModelRole[];
}

/** Null when there is nothing to delete (not downloaded). */
export function deleteOffer(model: AsrModel, ctx: DeleteContext): DeleteOffer | null {
  if (!model.downloaded) return null;
  const roles: ModelRole[] = [];
  if (ctx.fastModel === model.id) roles.push("fast");
  if (ctx.finalModel === model.id) roles.push("final");
  const blocked: DeleteBlocked | null = !ctx.engineReady
    ? "engine_not_ready"
    : ctx.deleting
      ? "deleting"
      : ctx.downloading
        ? "downloading"
        : ctx.sessionActive && roles.length > 0
          ? "in_use"
          : null;
  return { blocked, roles };
}

/** What deleting frees: the measured disk use, or the download size when the engine did not report it. */
export function reclaimableBytes(model: AsrModel): number {
  return model.disk_bytes ?? model.size_bytes;
}

/** The confirmation question shown next to the button. */
export function deleteConfirmText(model: AsrModel, offer: DeleteOffer): string {
  const question = t("asr.deleteConfirm", { model: model.id, size: formatBytes(reclaimableBytes(model)) });
  if (offer.roles.length === 0) return question;
  const roles = joinList(offer.roles.map((r) => (r === "fast" ? t("role.fast") : t("role.final"))));
  return `${question} ${t("asr.deleteSelected", { roles })}`;
}

/** Why the button is disabled, for its tooltip. */
export function deleteBlockedText(blocked: DeleteBlocked): string {
  switch (blocked) {
    case "in_use":
      return t("asr.deleteInUse");
    case "downloading":
      return t("asr.deleteDownloading");
    case "deleting":
      return t("asr.deleting");
    case "engine_not_ready":
      return t("check.engineNotReady");
  }
}
