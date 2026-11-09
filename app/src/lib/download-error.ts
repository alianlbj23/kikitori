/**
 * Turns a failed download's error (`download_progress.error`) into what the user should do.
 *
 * Role: the engine passes upstream errors through as text (for diagnostics); a few of them
 * have one clear remedy that the raw English message hides. Today: Ollama refuses a model
 * that needs a newer Ollama (HTTP 412, "requires a newer version of Ollama", seen on a Linux
 * machine with an old Ollama pulling qwen3.5). Anything else is shown as it is.
 */

import { t } from "./i18n";

/** Ollama's wording for a model newer than the server (its `/api/pull` 412 error). */
const OLLAMA_TOO_OLD = /requires a newer version of ollama/i;

/** The text to show for a failed download; `null` = no reason given. */
export function downloadErrorText(error: string | null): string {
  if (error === null || error.trim() === "") return t("common.unknownReason");
  if (OLLAMA_TOO_OLD.test(error)) return t("download.ollamaOutdated");
  return error;
}
