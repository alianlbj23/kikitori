/**
 * Auto-dismiss for confirmation notices.
 *
 * The notice bar sticks to the top of the scrolling content, so a notice that
 * only confirms something ("dictionary downloaded") would otherwise cover the
 * top of every page until the user closes it. Notices that ask for a decision,
 * report an error or carry actions stay until closed; main.ts opts in per call.
 */

/** How long a confirmation stays up; long enough to read two lines twice. */
export const AUTO_DISMISS_MS = 8000;

export interface AutoDismissTarget {
  /** True while the pointer is over the notice or focus is inside it: reading it wins. */
  isBeingRead(): boolean;
  dismiss(): void;
}

export interface AutoDismissTimer {
  /** Starts a full delay again; call it when the reader lets go (pointer leaves, focus moves out). */
  restart(): void;
  /** Stops for good; call it when the user closes the notice. */
  cancel(): void;
}

/**
 * Dismisses `target` after `ms`; while it is being read, waits another `ms` and checks again.
 * The checks alone would leave anywhere from 0 to `ms` after the reader lets go, so the caller
 * calls `restart()` on release to give a full `ms` from that moment.
 */
export function autoDismiss(target: AutoDismissTarget, ms: number = AUTO_DISMISS_MS): AutoDismissTimer {
  if (!Number.isFinite(ms) || ms <= 0) throw new RangeError(`auto-dismiss delay must be a positive number of ms, got ${ms}`);
  let timer: ReturnType<typeof setTimeout> | undefined;
  let cancelled = false;
  const stop = (): void => {
    if (timer !== undefined) clearTimeout(timer);
    timer = undefined;
  };
  const arm = (): void => {
    stop();
    timer = setTimeout(() => {
      timer = undefined;
      if (target.isBeingRead()) arm();
      else target.dismiss();
    }, ms);
  };
  arm();
  return {
    restart: () => {
      if (!cancelled) arm();
    },
    cancel: () => {
      cancelled = true;
      stop();
    },
  };
}
