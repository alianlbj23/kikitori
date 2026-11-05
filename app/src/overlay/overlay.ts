/**
 * Overlay (subtitle) window controller.
 *
 * Role: renders the newest utterances on a transparent, always-on-top window.
 * It only listens — subtitles and status from the engine, style and
 * click-through from the main window via Rust — and can move, resize and hide
 * itself. It cannot control the engine (see capabilities/overlay.json). Its
 * interface language is the one the main window pushes through Rust
 * (`OverlaySnapshot.locale`), or the OS language until then. With "顯示翻譯"
 * off (`OverlaySnapshot.show_translation`) each line is the original alone, in
 * the primary subtitle style. Which lines are on screen follows the reading
 * rhythm (`lib/rhythm.ts`): a minimum time per line and an idle fade-out. The
 * "clear subtitles" shortcut (`overlay://clear`) empties the screen.
 */

import { getCurrentWindow } from "@tauri-apps/api/window";

import { api, events } from "../lib/api";
import { byId, h, replace } from "../lib/dom";
import { htmlLang } from "../lib/format";
import {
  applyStatic,
  isUiLocale,
  onLocaleChange,
  resolveLocale,
  setLocale,
  systemLanguages,
  t,
  type UiLocale,
} from "../lib/i18n";
import { bufferLines, emptyRhythm, stepRhythm, type RhythmState } from "../lib/rhythm";
import { DEFAULT_OVERLAY_STYLE } from "../lib/settings";
import {
  applySubtitle,
  emptySubtitles,
  lineDisplay,
  trimSubtitles,
  type SubtitleLine,
  type SubtitleState,
} from "../lib/subtitles";
import type { OverlaySnapshot, OverlayStyle } from "../lib/types";

const frame = byId("frame");
const lines = byId("lines");
const hint = byId("hint");
const hideButton = byId<HTMLButtonElement>("hide");
const resizeGrip = byId("resize");
const appWindow = getCurrentWindow();
/** Matches the CSS fade-out of `.lines.is-fading`. */
const FADE_MS = 400;

/** Every line the engine sent, a few beyond the screen (waiting or faded). */
let subtitles: SubtitleState = emptySubtitles();
let style: OverlayStyle = DEFAULT_OVERLAY_STYLE;
let clickThrough = false;
let showTranslation = true;
let rhythm: RhythmState = emptyRhythm();
/** Lines on screen (decided by the reading rhythm). */
let shown: SubtitleLine[] = [];
let wakeTimer: number | undefined;
let fadeTimer: number | undefined;

function applyStyle(next: OverlayStyle): void {
  style = next;
  frame.style.setProperty("--fs", `${next.font_size}px`);
  frame.style.setProperty("--plate", String(next.background_opacity));
  frame.dataset.position = next.position;
  subtitles = trimSubtitles(subtitles, bufferLines(next.max_lines));
  refresh();
}

/** Re-decides what is on screen now and when to look again. */
function refresh(): void {
  window.clearTimeout(wakeTimer);
  const result = stepRhythm(rhythm, subtitles.lines, Date.now(), {
    maxLines: style.max_lines,
    minDisplayMs: style.min_display_ms,
    idleClearMs: style.idle_clear_secs * 1000,
  });
  rhythm = result.state;
  if (result.wakeAt !== null) {
    wakeTimer = window.setTimeout(refresh, Math.max(0, result.wakeAt - Date.now()));
  }
  if (result.fadedNow) {
    // Keep the old lines while they fade, then draw the empty screen.
    lines.classList.add("is-fading");
    window.clearTimeout(fadeTimer);
    fadeTimer = window.setTimeout(() => {
      shown = result.lines;
      render();
    }, FADE_MS);
    return;
  }
  if (lines.classList.contains("is-fading") && result.lines.length === 0) return;
  window.clearTimeout(fadeTimer);
  shown = result.lines;
  render();
}

/** Starts over (new session, or the "clear subtitles" shortcut). */
function resetScreen(next: SubtitleState): void {
  subtitles = next;
  rhythm = emptyRhythm(Date.now());
  window.clearTimeout(fadeTimer);
  shown = [];
  refresh();
}

/** Re-labels the overlay and its window title in the current locale. */
function relabel(): void {
  applyStatic(document);
  const title = t("overlay.title");
  document.title = title;
  // 字幕視窗沒有標題列，但標題會出現在工作切換、螢幕閱讀器等地方。
  appWindow.setTitle(title).catch((err: unknown) => console.error("setTitle failed:", err));
}

let appliedLocale: UiLocale | null = null;

function applyLocale(locale: string | null): void {
  // Before the main window has sent a locale (or for one this build does not know), follow the OS.
  const next = isUiLocale(locale) ? locale : resolveLocale(systemLanguages());
  // Every style or click-through change re-sends the snapshot; relabel only on a real change.
  if (next === appliedLocale) return;
  appliedLocale = next;
  setLocale(next);
}

function applySnapshot(s: OverlaySnapshot): void {
  applyLocale(s.locale);
  clickThrough = s.click_through;
  showTranslation = s.show_translation;
  frame.classList.toggle("is-interactive", !clickThrough);
  applyStyle(s.style);
}

function render(): void {
  lines.classList.remove("is-fading");
  replace(
    lines,
    ...shown.map((line) => {
      const display = lineDisplay(line, showTranslation);
      const classes = ["line", line.isFinal ? "" : "is-partial", display.originalOnly ? "is-original-only" : ""];
      // dir="auto": Arabic lines run right to left, each span on its own.
      return h(
        "div",
        { class: classes.filter(Boolean).join(" ") },
        h("span", { class: "orig", lang: htmlLang(line.sourceLanguage), dir: "auto" }, display.original),
        display.originalOnly
          ? null
          : h("span", { class: "tr", lang: htmlLang(line.targetLanguage), dir: "auto" }, display.translation),
      );
    }),
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
  onLocaleChange(relabel);
  applyLocale(null);
  wire();
  render();
  await Promise.all([
    events.subtitle((ev) => {
      const before = subtitles.sessionId;
      subtitles = applySubtitle(subtitles, ev, bufferLines(style.max_lines));
      if (subtitles.sessionId !== before) rhythm = emptyRhythm(Date.now());
      refresh();
    }),
    events.status((s) => {
      // A new session starts with a clean slate.
      if (s.state === "loading" && s.session_id !== null && s.session_id !== subtitles.sessionId) {
        resetScreen(emptySubtitles(s.session_id));
      }
    }),
    events.overlay(applySnapshot),
    // Keeps lastFinalId, so late partials of cleared lines stay away.
    events.overlayClear(() => resetScreen({ ...subtitles, lines: [] })),
  ]);
  try {
    applySnapshot(await api.overlayState());
  } catch (e) {
    console.error("cannot read overlay state; using defaults", e);
  }
}

init().catch((e: unknown) => console.error("overlay failed to initialise", e));
