/**
 * When to auto-save the transcript after a session ends.
 *
 * Role: pure, timer-injected rule behind "save SRT and TXT at the end of every
 * session" (competitive-features.md F1 follow-up). The final pass may still
 * send the last sentence's complete subtitle shortly after Stop was answered,
 * so saving at once would leave it out. The save therefore waits until the
 * engine reports the session idle (its last final has been sent by then:
 * events arrive in order) or `SESSION_END_GRACE_MS` has passed, whichever
 * comes first. When the end is already known to be final (the engine said
 * idle or error, or the engine itself is gone) it saves at once, and a new
 * session starting during the wait saves the previous one first.
 */

/** Upper bound of the wait for the engine's "idle" after Stop. */
export const SESSION_END_GRACE_MS = 2000;

export interface Timers {
  set(run: () => void, ms: number): number;
  clear(handle: number): void;
}

export class SessionEndSave {
  private timer: number | null = null;

  constructor(
    private readonly save: () => void,
    private readonly timers: Timers,
    private readonly graceMs: number = SESSION_END_GRACE_MS,
  ) {}

  /** A save is waiting for the engine (or the grace period). */
  get waiting(): boolean {
    return this.timer !== null;
  }

  /**
   * The session ended. `final` = nothing more can arrive (the engine reported
   * idle/error, or it stopped running): save now; otherwise wait.
   */
  ended(final: boolean): void {
    if (final) {
      this.fire();
      return;
    }
    if (this.timer !== null) return;
    this.timer = this.timers.set(() => {
      this.timer = null;
      this.save();
    }, this.graceMs);
  }

  /** The engine reported the session idle (or failed): its last subtitle is in. */
  engineIdle(): void {
    if (this.timer !== null) this.fire();
  }

  /** A new session is starting: save the one that just ended before its log is reset. */
  flush(): void {
    if (this.timer !== null) this.fire();
  }

  private fire(): void {
    if (this.timer !== null) {
      this.timers.clear(this.timer);
      this.timer = null;
    }
    this.save();
  }
}
