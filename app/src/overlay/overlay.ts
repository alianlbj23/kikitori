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
 * "clear subtitles" shortcut (`overlay://clear`) empties the screen. A line
 * still being heard fades only the part of the original and of the
 * translation that may change (`lib/stable.ts`); its newly appeared characters
 * fade in and the line never gets shorter until its final arrives
 * (`lib/fresh.ts`, docs/specs/v0.3-features.md G2).
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
import { freshParts, HeightLatch, type FreshText } from "../lib/fresh";
import { pushRecent } from "../lib/recent";
import { bufferLines, emptyRhythm, readingTimeMs, stepRhythm, type RhythmState } from "../lib/rhythm";
import { firstReadingsOnly, segmentNodes } from "../lib/ruby";
import { DEFAULT_OVERLAY_STYLE } from "../lib/settings";
import {
  applySubtitle,
  emptySubtitles,
  lineDisplay,
  trimSubtitles,
  type SubtitleLine,
  type SubtitleState,
} from "../lib/subtitles";
import { speakerLabels, voiceColor, voiceLabels } from "../lib/speaker";
import type { OverlayNotice, OverlaySnapshot, OverlayStyle, ShortcutAction, Speaker } from "../lib/types";

const frame = byId("frame");
const lines = byId("lines");
const hint = byId("hint");
const hideButton = byId<HTMLButtonElement>("hide");
const resizeGrip = byId("resize");
const pausedBadge = byId("paused");
const historyPanel = byId("history");
const historyLines = byId("history-lines");
const noticeBox = byId("notice");
const appWindow = getCurrentWindow();
/** Matches the CSS fade-out of `.lines.is-fading`. */
const FADE_MS = 400;
/** How long "say that again" stays up (G3). */
const NOTICE_MS = 5000;
/** How long the recent-subtitles panel stays open unless toggled off (G6). */
const HISTORY_MS = 8000;

/** Every line the engine sent, a few beyond the screen (waiting or faded). */
let subtitles: SubtitleState = emptySubtitles();
let style: OverlayStyle = DEFAULT_OVERLAY_STYLE;
let clickThrough = false;
let showTranslation = true;
/** Names the main window gave the speaker labels this session (speaker-labels.md). */
let speakerNames: readonly string[] = [];
let rhythm: RhythmState = emptyRhythm();
/** Lines on screen (decided by the reading rhythm). */
let shown: SubtitleLine[] = [];
/** Last drawn text of each line on screen, to fade in only what is new (G2). */
let lastFrame = new Map<number, { original: string; translation: string }>();
/** Tallest height each live line reached (G2). */
const heights = new HeightLatch();
let wakeTimer: number | undefined;
let fadeTimer: number | undefined;
/** G6: the pause shortcut froze the screen; subtitles keep arriving and are shown on resume. */
let paused = false;
/** G6: the last final lines, for the recent-subtitles panel. */
let recent: SubtitleLine[] = [];
let historyTimer: number | undefined;
let noticeTimer: number | undefined;
/** G7: which utterance first showed each word with furigana (this session). */
let rubyOwners = new Map<string, number>();

function applyStyle(next: OverlayStyle): void {
  style = next;
  frame.style.setProperty("--fs", `${next.font_size}px`);
  frame.style.setProperty("--plate", String(next.background_opacity));
  frame.dataset.position = next.position;
  // G7 reading layout: original size and place, and an optional maximum line width.
  frame.style.setProperty("--orig-scale", String(next.original_scale_percent / 100));
  frame.dataset.original = next.original_position;
  frame.classList.toggle("has-max-line", next.max_line_em > 0);
  // In translation-size em, so the smaller original wraps at the same width as the translation.
  if (next.max_line_em > 0) frame.style.setProperty("--max-line", `calc(var(--fs) * ${next.max_line_em})`);
  subtitles = trimSubtitles(subtitles, bufferLines(next.max_lines));
  refresh();
}

/** Re-decides what is on screen now and when to look again. */
function refresh(): void {
  window.clearTimeout(wakeTimer);
  // Paused: keep exactly what is on screen; resuming refreshes to the newest lines.
  if (paused) return;
  const result = stepRhythm(rhythm, subtitles.lines, Date.now(), {
    maxLines: style.max_lines,
    minDisplayMs: style.min_display_ms,
    idleClearMs: style.idle_clear_secs * 1000,
    // G7: a long line stays up for as long as it takes to read it.
    minDisplayFor: style.pace_by_length ? (line) => readingTimeMs(line, showTranslation) : undefined,
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
      if (paused) return; // frozen: the fade-out waits for the resume
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
/** Forgets everything tied to the lines of one session (G2 frames and heights, G7 furigana owners). */
function forgetLineState(): void {
  lastFrame = new Map();
  heights.clear();
  rubyOwners = new Map();
}

function resetScreen(next: SubtitleState): void {
  subtitles = next;
  rhythm = emptyRhythm(Date.now());
  window.clearTimeout(fadeTimer);
  forgetLineState();
  shown = [];
  // Clearing (or a new session) must reach the screen even while paused: a pause
  // that kept old text up after "clear subtitles" would defeat the clear.
  if (paused) {
    paused = false;
    pausedBadge.hidden = true;
    frame.classList.remove("is-paused");
  }
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
  const namesChanged = JSON.stringify(s.speaker_names) !== JSON.stringify(speakerNames);
  speakerNames = s.speaker_names;
  frame.classList.toggle("is-interactive", !clickThrough);
  applyStyle(s.style);
  if (namesChanged) render();
}

/** "You" / "Them" in front of a line whose speaker changed (G3); colour is not the only cue. */
function whoChip(speaker: Speaker): HTMLElement {
  return h("span", { class: `who is-${speaker}` }, t(speaker === "self" ? "speaker.self" : "speaker.partner"));
}

/** Media mode: the speaker's name in front of a line whose speaker changed; colour follows the name. */
function voiceChip(name: string): HTMLElement {
  return h("span", { class: `who voice c${voiceColor(name)}` }, name);
}

/** Shows a short message (interpret mode "say that again", G3) for a few seconds. */
function showNotice(notice: OverlayNotice): void {
  noticeBox.textContent = notice.text;
  noticeBox.lang = htmlLang(notice.lang);
  noticeBox.hidden = false;
  window.clearTimeout(noticeTimer);
  noticeTimer = window.setTimeout(() => {
    noticeBox.hidden = true;
  }, NOTICE_MS);
}

function setPaused(next: boolean): void {
  paused = next;
  pausedBadge.hidden = !paused;
  frame.classList.toggle("is-paused", paused);
  if (!paused) refresh();
}

function renderHistory(): void {
  replace(
    historyLines,
    ...recent.map((line) =>
      h(
        "div",
        { class: "history-line" },
        h("span", { class: "orig", lang: htmlLang(line.sourceLanguage), dir: "auto" }, line.original),
        showTranslation && line.translation
          ? h("span", { class: "tr", lang: htmlLang(line.targetLanguage), dir: "auto" }, line.translation)
          : null,
      ),
    ),
  );
}

function setHistoryOpen(open: boolean): void {
  window.clearTimeout(historyTimer);
  historyPanel.hidden = !open;
  frame.classList.toggle("is-history", open);
  if (open) {
    renderHistory();
    historyLines.scrollTop = historyLines.scrollHeight;
    historyTimer = window.setTimeout(() => setHistoryOpen(false), HISTORY_MS);
  }
}

/** The pause and recent-subtitles shortcuts (Rust `overlay://action`, G6). */
function onOverlayAction(action: ShortcutAction): void {
  if (action === "pause_subtitles") setPaused(!paused);
  else if (action === "show_history") setHistoryOpen(historyPanel.hidden === true);
}

/** Plain text with its newly appeared part in a `.fresh` span that fades in (G2). */
function freshNodes(text: FreshText): (string | HTMLElement)[] {
  return text.fresh ? [text.kept, h("span", { class: "fresh" }, text.fresh)] : [text.kept];
}

function render(): void {
  lines.classList.remove("is-fading");
  const frames = new Map<number, { original: string; translation: string }>();
  // Interpret mode: name the speaker when it changes (G3); media mode: the speaker labels.
  const labels = speakerLabels(shown);
  const voices = voiceLabels(shown, speakerNames, (n) => t("speaker.voice", { n }));
  const drawn = shown.map((line, index) => {
    const label = labels[index] ?? null;
    const voice = voices[index] ?? null;
    const shownLine = lineDisplay(line, showTranslation);
    // G7: furigana only where a word first appears in this session (an option).
    const display =
      style.furigana_first_only && shownLine.originalRuby
        ? { ...shownLine, originalRuby: firstReadingsOnly(shownLine.originalRuby, line.utteranceId, rubyOwners) }
        : shownLine;
    const before = lastFrame.get(line.utteranceId) ?? null;
    frames.set(line.utteranceId, {
      original: display.originalParts.stable + display.originalParts.unstable,
      translation: display.translationParts.stable + display.translationParts.unstable,
    });
    // New characters of a line still being heard fade in; a final appears as it is.
    const orig = freshParts(display.originalParts, line.isFinal ? null : (before?.original ?? null));
    const tr = freshParts(display.translationParts, line.isFinal ? null : (before?.translation ?? null));
    const classes = ["line", line.isFinal ? "" : "is-partial", display.originalOnly ? "is-original-only" : ""];
    // dir="auto": Arabic lines run right to left, each span on its own.
    const element = h(
      "div",
      { class: classes.filter(Boolean).join(" ") },
      // Settled prefix and the tail that may still change (F2); the inner spans take
      // their direction from the outer one.
      h(
        "span",
        { class: "orig", lang: htmlLang(line.sourceLanguage), dir: "auto" },
        label ? whoChip(label) : voice ? voiceChip(voice) : null,
        // Furigana (docs/specs/furigana.md): <ruby> segments in the same two spans (not faded in).
        display.originalRuby
          ? h("span", { class: "stable" }, ...segmentNodes(display.originalRuby.stable))
          : h("span", { class: "stable" }, ...freshNodes(orig.stable)),
        display.originalRuby
          ? display.originalRuby.unstable.length
            ? h("span", { class: "unstable" }, ...segmentNodes(display.originalRuby.unstable))
            : null
          : display.originalParts.unstable
            ? h("span", { class: "unstable" }, ...freshNodes(orig.unstable))
            : null,
      ),
      // The translation too: settled prefix, then the tail that may still change (G1).
      display.originalOnly || display.translation === ""
        ? null
        : h(
            "span",
            { class: "tr", lang: htmlLang(line.targetLanguage), dir: "auto" },
            h("span", { class: "stable" }, ...freshNodes(tr.stable)),
            display.translationParts.unstable ? h("span", { class: "unstable" }, ...freshNodes(tr.unstable)) : null,
          ),
    );
    // A live line is never drawn shorter than it has been, so a shrinking tail does not make the block jump (G2).
    const min = heights.minHeight(line.utteranceId, line.isFinal);
    if (min !== null) element.style.minHeight = `${min}px`;
    return { line, element };
  });
  replace(lines, ...drawn.map((d) => d.element));
  for (const { line, element } of drawn) heights.record(line.utteranceId, line.isFinal, element.offsetHeight);
  heights.keepOnly(frames.keys());
  lastFrame = frames;
  // The hint helps place an empty window; with subtitles or click-through on
  // it would only cover the video.
  hint.hidden = subtitles.lines.length > 0 || clickThrough;
}

function isControl(target: EventTarget | null): boolean {
  // The recent-subtitles panel scrolls: dragging its scrollbar must not move the window.
  return target instanceof Element && target.closest("button, .resize, .history") !== null;
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
      if (subtitles.sessionId !== before) {
        // Utterance ids start at 1 again: nothing of the old session may carry over.
        rhythm = emptyRhythm(Date.now());
        recent = [];
        forgetLineState();
      }
      if (ev.is_final) {
        const line = subtitles.lines.find((l) => l.utteranceId === ev.utterance_id);
        recent = line ? pushRecent(recent, line) : recent.filter((l) => l.utteranceId !== ev.utterance_id);
        if (!historyPanel.hidden) renderHistory();
      }
      refresh();
    }),
    events.status((s) => {
      // A new session starts with a clean slate.
      if (s.state === "loading" && s.session_id !== null && s.session_id !== subtitles.sessionId) {
        recent = [];
        resetScreen(emptySubtitles(s.session_id));
      }
    }),
    events.overlayAction(onOverlayAction),
    events.overlayNotice(showNotice),
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
