/**
 * State rules for the model combination form and the GPU runtime install.
 *
 * Role: pure decisions the models page needs, kept out of `main.ts` so they can
 * be tested: whether "save" may be pressed given the live `models.validate`
 * result, which of several in-flight checks is still relevant, and what to do
 * with the engine once the GPU runtime has been installed.
 */

import type { CombinationCheck } from "./types";

/** What the combination form currently knows about its own validity. */
export type CheckView =
  /** The form has format errors; the engine was not asked. */
  | { state: "local_errors" }
  /** A `models.validate` request for the current input is in flight. */
  | { state: "checking" }
  | { state: "checked"; check: CombinationCheck }
  /** The engine rejected a field (`invalid_settings`), shown next to that field. */
  | { state: "rejected"; message: string }
  /** The check itself could not run (timeout, engine restarting...). */
  | { state: "unavailable"; message: string };

/**
 * Save is allowed only when the engine said the combination is usable. When
 * the check could not run at all, saving stays possible: `settings.set` still
 * validates the fields, and a stuck engine must not lock the user out.
 */
export function canSave(view: CheckView): boolean {
  switch (view.state) {
    case "checked":
      return view.check.ok;
    case "unavailable":
      return true;
    case "local_errors":
    case "checking":
    case "rejected":
      return false;
  }
}

/**
 * Hands out increasing tokens so only the newest of several overlapping
 * requests is applied. Responses are matched by id, but the user may type
 * faster than the engine answers, and a slow answer for old input must not
 * overwrite the answer for the current input.
 */
export class Latest {
  private current = 0;

  /** Starts a new request; earlier tokens become stale. */
  next(): number {
    this.current += 1;
    return this.current;
  }

  /** Makes every outstanding token stale (e.g. the form was reset). */
  invalidate(): void {
    this.current += 1;
  }

  isCurrent(token: number): boolean {
    return token === this.current;
  }
}

export type AfterGpuInstall = "restart" | "offer";

/**
 * After the GPU runtime is installed the engine must be restarted: the ASR
 * layer does not retry CUDA in a process where it already failed. Restarting
 * would cut a running session or another download short, so then only offer it.
 */
export function afterGpuRuntimeInstalled(sessionActive: boolean, otherDownloads: number): AfterGpuInstall {
  return sessionActive || otherDownloads > 0 ? "offer" : "restart";
}

/** The only URL the opener permission allows (capabilities/main.json). */
export const NVIDIA_EULA_URL = "https://docs.nvidia.com/cuda/eula/index.html";

/** Whether the engine-reported licence link is the one the UI may open. */
export function canOpenLicenseUrl(url: string | null): boolean {
  return url === NVIDIA_EULA_URL;
}
