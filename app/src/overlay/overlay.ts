/**
 * Overlay (subtitle) window controller.
 *
 * Role: renders the newest utterances on a transparent, always-on-top window.
 * It only listens — subtitles and status from the engine, style and
 * click-through from the main window via Rust — and can move, resize and hide
 * itself. It cannot control the engine (see capabilities/overlay.json).
 */

import { getCurrentWindow } from "@tauri-apps/api/window";

import { api, events } from "../lib/api";
import { byId, h, replace } from "../lib/dom";
import { htmlLang } from "../lib/format";
import { DEFAULT_OVERLAY_STYLE } from "../lib/settings";
import { applySubtitle, emptySubtitles, trimSubtitles, type SubtitleState } from "../lib/subtitles";
import type { OverlaySnapshot, OverlayStyle } from "../lib/types";

const frame = byId("frame");
const lines = byId("lines");
const hint = byId("hint");
const hideButton = byId<HTMLButtonElement>("hide");
const resizeGrip = byId("resize");
const appWindow = getCurrentWindow();

let subtitles: SubtitleState = emptySubtitles();
let style: OverlayStyle = DEFAULT_OVERLAY_STYLE;
let clickThrough = false;

function applyStyle(next: OverlayStyle): void {
  style = next;
  frame.style.setProperty("--fs", `${next.font_size}px`);
  frame.style.setProperty("--plate", String(next.background_opacity));
  frame.dataset.position = next.position;
  subtitles = trimSubtitles(subtitles, next.max_lines);
  render();
}

function applySnapshot(s: OverlaySnapshot): void {
  clickThrough = s.click_through;
  frame.classList.toggle("is-interactive", !clickThrough);
  applyStyle(s.style);
}

function render(): void {
  replace(
    lines,
    ...subtitles.lines.map((line) =>
      h(
        "div",
        { class: line.isFinal ? "line" : "line is-partial" },
        h("span", { class: "orig", lang: htmlLang(line.sourceLanguage) }, line.original),
        h("span", { class: "tr", lang: htmlLang(line.targetLanguage) }, line.translation),
      ),
    ),
  );
  // The hint helps place an empty window; with subtitles or click-through on
  // it would only cover the video.
  hint.hidden = subtitles.lines.length > 0 || clickThrough;
}

function isControl(target: EventTarget | null): boolean {
  return target instanceof Element && target.closest("button, .resize") !== null;
}

function wire(): void {
  // Undecorated window: dragging anywhere (except controls) moves it.
  frame.addEventListener("mousedown", (e) => {
    if (e.button !== 0 || clickThrough || isControl(e.target)) return;
    appWindow.startDragging().catch((err: unknown) => console.error("startDragging failed:", err));
  });
  resizeGrip.addEventListener("mousedown", (e) => {
    if (e.button !== 0 || clickThrough) return;
    e.preventDefault();
    appWindow
      .startResizeDragging("SouthEast")
      .catch((err: unknown) => console.error("startResizeDragging failed:", err));
  });
  hideButton.addEventListener("click", () => {
    api.overlaySetVisible(false).catch((err: unknown) => console.error("hide overlay failed:", err));
  });
}

async function init(): Promise<void> {
  wire();
  render();
  await Promise.all([
    events.subtitle((ev) => {
      subtitles = applySubtitle(subtitles, ev, style.max_lines);
      render();
    }),
    events.status((s) => {
      // A new session starts with a clean slate.
      if (s.state === "loading" && s.session_id !== null && s.session_id !== subtitles.sessionId) {
        subtitles = emptySubtitles(s.session_id);
        render();
      }
    }),
    events.overlay(applySnapshot),
  ]);
  try {
    applySnapshot(await api.overlayState());
  } catch (e) {
    console.error("cannot read overlay state; using defaults", e);
  }
}

init().catch((e: unknown) => console.error("overlay failed to initialise", e));
