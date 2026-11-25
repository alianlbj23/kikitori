/**
 * The mode bar (docs/specs/compute-capacity.md 九): which of the three roles
 * this computer is in, at a glance.
 *
 * Role: pure logic for the bar along the top of the window — the role's colour
 * name, line icon and plain-words text, plus the link dot of the client role.
 * Colour, icon and text always change together, so the role reads without
 * relying on colour alone. The same colour name tints the window's edge, the
 * rail's chosen role and the start button (main.css `[data-mode]`); the colours
 * avoid the load chart's green, yellow and red. `main.ts` only renders.
 */

import { t, type StringKey } from "./i18n";
import { computeRole, formatHostPort, hostSummary, type ComputeSettings, type HostInfo } from "./remote";

/** The role as the bar shows it: this computer only, using a compute host, or being one. */
export type ModeTone = "local" | "client" | "serve";
/** The mode's colour (CSS custom properties `--mode-*` in main.css). */
export type ModeColor = "indigo" | "sky" | "purple";
/** One computer, two computers with an arrow, a server. */
export type ModeIcon = "computer" | "computers" | "server";
/** The client's link to the compute host. */
export type LinkState = "connected" | "reconnecting" | "offline";

export interface ModeBarView {
  tone: ModeTone;
  color: ModeColor;
  icon: ModeIcon;
  text: string;
  /** Client only. */
  link: LinkState | null;
  linkText: string | null;
}

export interface ModeBarInput {
  compute: ComputeSettings;
  /** The compute host's last report (client). */
  host: HostInfo | null;
  /** Round trip of the running remote session, ms. */
  rttMs: number | null;
  /** A session is loading or running. */
  sessionActive: boolean;
  /** The running remote session lost its link and is reconnecting. */
  reconnecting: boolean;
  /** The compute host panel's headline (lib/serve.ts), for the serve role. */
  serveHeadline: string;
}

const LINK_TEXT: Readonly<Record<LinkState, StringKey>> = {
  connected: "modeBar.linkConnected",
  reconnecting: "modeBar.linkReconnecting",
  offline: "modeBar.linkOffline",
};

/** The client only connects while subtitles run, so idle reads 未連線. */
export function linkState(sessionActive: boolean, reconnecting: boolean): LinkState {
  if (!sessionActive) return "offline";
  return reconnecting ? "reconnecting" : "connected";
}

export function modeBarView(input: ModeBarInput): ModeBarView {
  const c = input.compute;
  switch (computeRole(c)) {
    case "local":
      return { tone: "local", color: "indigo", icon: "computer", text: t("modeBar.local"), link: null, linkText: null };
    case "serve":
      return {
        tone: "serve",
        color: "purple",
        icon: "server",
        text: t("modeBar.serve", { detail: input.serveHeadline }),
        link: null,
        linkText: null,
      };
    case "remote": {
      const link = linkState(input.sessionActive, input.reconnecting);
      let detail: string;
      if (c.host === "") detail = t("modeBar.clientNoHost");
      else {
        detail = input.host ? hostSummary(input.host) : formatHostPort(c.host, c.port);
        if (link === "connected" && input.rttMs !== null) {
          detail = t("modeBar.join", { a: detail, b: t("compute.rtt", { ms: Math.round(input.rttMs) }) });
        }
      }
      return {
        tone: "client",
        color: "sky",
        icon: "computers",
        text: t("modeBar.client", { detail }),
        link,
        linkText: t(LINK_TEXT[link]),
      };
    }
  }
}
