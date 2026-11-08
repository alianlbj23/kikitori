/**
 * Keeps late settings results from overwriting newer ones on screen.
 *
 * Several requests answer with the whole settings (settings.set, session.set_translation,
 * session.set_furigana…), and a caller may write its answer back only after more awaits
 * (the translation switch waits for the overlay). Without an order, an answer computed
 * before a newer change can land last and put the old value back on screen — the
 * furigana switch then shows off while the engine has it on. The engine numbers every
 * settings change (`revision`); the UI keeps only answers that are not older.
 */
export class SettingsRevision {
  private latest: number | null = null;

  /**
   * True when a result with this revision may replace the settings on screen, and then
   * remembers it as the latest. `null` (an engine that does not report it) is always
   * accepted, as before, and leaves the latest unchanged.
   */
  acceptIfNotOlder(revision: number | null): boolean {
    if (revision === null) return true;
    if (this.latest !== null && revision < this.latest) return false;
    this.latest = revision;
    return true;
  }

  /** The engine (re)started: its revisions count from the beginning again. */
  reset(): void {
    this.latest = null;
  }
}
