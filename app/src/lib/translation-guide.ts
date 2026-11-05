/**
 * First-use guide when the translation server cannot be reached.
 *
 * Role: decides whether the settings page and the notice area offer the two
 * ways forward (docs/specs/competitive-features.md F8): install Ollama (its
 * official download page, the only extra URL the opener permission allows),
 * or show originals only (`translation.enabled = false`). Pure, so the rule is
 * tested without a window.
 */

/** Ollama's official download page; also listed in `capabilities/main.json`. */
export const OLLAMA_DOWNLOAD_URL = "https://ollama.com/download";

/** What the engine reported about the translation server (`models.list` or `hardware.detect`). */
export interface ServerProbe {
  base_url: string;
  is_local: boolean;
  reachable: boolean;
  error: string | null;
}

export interface GuideInput {
  /** Settings `translation.enabled`: nothing to guide when originals only. */
  translationEnabled: boolean;
  /** Latest probe; null when none has run yet. */
  server: ServerProbe | null;
  /** The start flow stopped on `translation_server_unreachable`. */
  startBlockedUnreachable: boolean;
}

export interface GuideView {
  /** The server address as configured, for the message. */
  url: string;
  /**
   * Offer "Install Ollama" only for a server on this computer: a remote
   * address is someone else's service, and installing locally would not fix it.
   */
  offerInstall: boolean;
}

/** The guide to show, or null when translation works or is switched off. */
export function translationGuide(input: GuideInput, fallbackUrl: string): GuideView | null {
  if (!input.translationEnabled) return null;
  const unreachable = input.startBlockedUnreachable || (input.server !== null && !input.server.reachable);
  if (!unreachable) return null;
  const url = input.server?.base_url ?? fallbackUrl;
  // Without a probe (start blocked first) the default local address is the usual case.
  const local = input.server?.is_local ?? isLocalUrl(url);
  return { url, offerInstall: local };
}

/** `localhost`, `127.x.x.x` or `[::1]`: the server would run on this computer. */
export function isLocalUrl(url: string): boolean {
  try {
    const host = new URL(url).hostname;
    return host === "localhost" || host === "[::1]" || /^127\.\d+\.\d+\.\d+$/.test(host);
  } catch (e) {
    console.warn("not a URL:", url, e);
    return false;
  }
}
