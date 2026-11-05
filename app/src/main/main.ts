/**
 * Main window controller.
 *
 * Role: wires the static markup in index.html to the engine (through `lib/api`)
 * and keeps it in sync with engine/bridge/overlay events. Pure decisions
 * (subtitle ordering, validation, wording, combination-check rules) live in
 * `lib/`; this file only holds UI state and renders it. Every text comes from
 * the i18n dictionary, and every render function can run again after the
 * interface language changes (`onLocaleChanged`).
 */

import { getVersion } from "@tauri-apps/api/app";

import { api, describeError, events } from "../lib/api";
import {
  Latest,
  afterGpuRuntimeInstalled,
  canOpenLicenseUrl,
  canSave,
  type CheckView,
} from "../lib/combination";
import { byId, h, replace } from "../lib/dom";
import {
  computeTypeLabel,
  describeStatus,
  describeStatusError,
  deviceLabel,
  formatBytes,
  formatMiB,
  formatTimecode,
  htmlLang,
  languageShort,
  osName,
  percent,
  regionNames,
  speedTierLabel,
  threadsLabel,
  tierLabel,
} from "../lib/format";
import {
  NATIVE_NAMES,
  UI_LOCALES,
  applyStatic,
  getLocale,
  htmlLangForLocale,
  isUiLocale,
  joinList,
  languageName,
  localeForPreference,
  onLocaleChange,
  setLocale,
  systemLanguages,
  t,
  tp,
  type StringKey,
  type UiLocalePreference,
} from "../lib/i18n";
import { formatEngineMessage, type EngineMessage } from "../lib/messages";
import {
  FirstRunGate,
  blockingDownloads,
  dismissedWith,
  missingModels,
  missingModelsView,
  offerText,
  runFirstRun,
  shouldOfferRecommendation,
  type MissingModel,
} from "../lib/recommend";
import {
  COMPUTE_TYPES,
  DEFAULT_SETTINGS,
  combinationFieldForEnginePath,
  combinationFormFromSettings,
  combinationPatch,
  devicePatch,
  formFieldForEnginePath,
  formFromSettings,
  settingsPatchFromForm,
  uiLocalePatch,
  validateCombinationForm,
  validateSettingsForm,
  type CombinationErrors,
  type CombinationForm,
  type FormErrors,
  type SettingsForm,
} from "../lib/settings";
import { applySubtitle, emptySubtitles, type SubtitleLine, type SubtitleState } from "../lib/subtitles";
import type {
  AboutInfo,
  AppliedRecommendation,
  AsrChoice,
  BridgeStatus,
  DeviceInfo,
  DownloadProgressEvent,
  EngineState,
  HardwareReport,
  LicenseFolder,
  Mode,
  ModelsList,
  OverlaySnapshot,
  RecommendationPreview,
  Settings,
} from "../lib/types";
import { asCommandError } from "../lib/validate";
import { checkForUpdate, type UpdateUi } from "./updater";

const TRANSCRIPT_MAX_LINES = 300;
/** Pause after the last keystroke before asking the engine to check the combination. */
const CHECK_DEBOUNCE_MS = 350;
const GPU_RUNTIME_ID = "gpu-runtime";

type Page = "transcript" | "models" | "settings";

interface State {
  bridge: BridgeStatus | null;
  sessionState: EngineState;
  sessionId: number | null;
  /** A start/stop request is in flight. */
  busy: boolean;
  mode: Mode;
  settings: Settings | null;
  settingsPath: string | null;
  devices: DeviceInfo[];
  subtitles: SubtitleState;
  overlay: OverlaySnapshot | null;
  models: ModelsList | null;
  hardware: HardwareReport | null;
  downloads: Map<string, DownloadProgressEvent>;
  page: Page;
  /** Live `models.validate` result for the combination form. */
  check: CheckView;
  /** The combination form differs from the saved settings; reloads keep the edits. */
  comboDirty: boolean;
  /** The user ticked "I agree to the NVIDIA CUDA EULA" (per download, never stored). */
  gpuLicenseAccepted: boolean;
  about: AboutInfo | null;
  /** The user saved a model combination in this window: first run must not override it. */
  userChoseModels: boolean;
  /** The first-run auto-apply is in progress; the offer waits for it. */
  firstRunActive: boolean;
  /** Latest `settings.preview_recommendation` answer. */
  preview: RecommendationPreview | null;
  /** Offers closed with 關閉 in this window (not stored; "不要再提醒" is). */
  closedOffers: Set<string>;
  /** Models the missing-models notice is about (null when it is not shown). */
  missing: MissingModel[] | null;
  /** Interface language preference shown in the settings page menu. */
  uiLocale: UiLocalePreference;
  /** A language chosen while the engine was not ready; saved once it is. */
  uiLocaleUnsaved: boolean;
}

const state: State = {
  bridge: null,
  sessionState: "idle",
  sessionId: null,
  busy: false,
  mode: "media",
  settings: null,
  settingsPath: null,
  devices: [],
  subtitles: emptySubtitles(),
  overlay: null,
  models: null,
  hardware: null,
  downloads: new Map(),
  page: "transcript",
  check: { state: "unavailable", message: t("check.engineNotReady") },
  comboDirty: false,
  gpuLicenseAccepted: false,
  about: null,
  userChoseModels: false,
  firstRunActive: false,
  preview: null,
  closedOffers: new Set(),
  missing: null,
  uiLocale: "system",
  uiLocaleUnsaved: false,
};

const firstRunGate = new FirstRunGate();

const el = {
  modes: Array.from(document.querySelectorAll<HTMLButtonElement>(".mode")),
  modeNotes: {
    media: document.querySelector<HTMLElement>('[data-mode-note="media"]'),
    interpret: document.querySelector<HTMLElement>('[data-mode-note="interpret"]'),
  },
  device: byId<HTMLSelectElement>("device"),
  deviceHint: byId("device-hint"),
  start: byId<HTMLButtonElement>("start"),
  navItems: Array.from(document.querySelectorAll<HTMLButtonElement>(".nav-item")),
  statusDot: byId("status-dot"),
  statusLabel: byId("status-label"),
  statusDetail: byId("status-detail"),
  restart: byId<HTMLButtonElement>("restart-engine"),
  notices: byId("notices"),
  pages: {
    transcript: byId("page-transcript"),
    models: byId("page-models"),
    settings: byId("page-settings"),
  } as Record<Page, HTMLElement>,
  transcript: byId<HTMLOListElement>("transcript"),
  transcriptEmpty: byId("transcript-empty"),
  overlayVisible: byId<HTMLInputElement>("overlay-visible"),
  overlayClick: byId<HTMLInputElement>("overlay-click-through"),
  shortcutNote: byId("shortcut-note"),
  clear: byId<HTMLButtonElement>("clear-transcript"),
  hardware: byId("hardware"),
  hardwareErrors: byId("hardware-errors"),
  recommendation: byId("recommendation"),
  gpuRuntime: byId("gpu-runtime"),
  asrModels: byId("asr-models"),
  modelsRefresh: byId<HTMLButtonElement>("models-refresh"),
  trSource: byId("tr-source"),
  trPresets: byId("tr-presets"),
  trNames: byId<HTMLDataListElement>("tr-names"),
  comboForm: byId<HTMLFormElement>("combo-form"),
  comboCheck: byId("combo-check"),
  comboSave: byId<HTMLButtonElement>("combo-save"),
  comboReset: byId<HTMLButtonElement>("combo-reset"),
  comboStatus: byId("combo-status"),
  settingsForm: byId<HTMLFormElement>("settings-form"),
  settingsPath: byId("settings-path"),
  uiLocale: byId<HTMLSelectElement>("f-ui-locale"),
  settingsStatus: byId("settings-status"),
  preview: byId("style-preview"),
  oFontSize: byId<HTMLOutputElement>("o-font-size"),
  oOpacity: byId<HTMLOutputElement>("o-opacity"),
  aboutVersion: byId("about-version"),
  aboutEngine: byId("about-engine"),
  aboutLicense: byId("about-license"),
  aboutThirdParty: byId("about-third-party"),
  openAppLicenses: byId<HTMLButtonElement>("open-app-licenses"),
  openEngineLicenses: byId<HTMLButtonElement>("open-engine-licenses"),
  checkUpdate: byId<HTMLButtonElement>("check-update"),
  updateStatus: byId("update-status"),
};

// ---------------------------------------------------------------- notices

interface NoticeAction {
  label: string;
  run: () => void;
}

interface NoticeOptions {
  /** A keyed notice replaces the previous one with the same key (see `noticeFor`). */
  key?: string;
  /** Runs when the user closes the notice with 關閉. */
  onClose?: () => void;
}

/** Shows a dismissible message at the top of the content area; returns it. */
function notify(
  text: string,
  tone: "info" | "bad" | "update" = "info",
  actions: NoticeAction[] = [],
  options: NoticeOptions = {},
): HTMLElement {
  for (const existing of Array.from(el.notices.children)) {
    if (existing.getAttribute("data-text") === text) existing.remove();
  }
  if (options.key) noticeFor(options.key)?.remove();
  const notice = h("div", { class: "notice", "data-tone": tone, "data-text": text, role: tone === "bad" ? "alert" : "status" });
  if (options.key) notice.dataset.key = options.key;
  const body = h("p", {}, text);
  const buttons = h("div", { class: "notice-actions" });
  for (const action of actions) {
    const b = h("button", { type: "button", class: "ghost" }, action.label);
    b.addEventListener("click", action.run);
    buttons.appendChild(b);
  }
  const close = h("button", { type: "button", class: "ghost", "aria-label": t("notice.closeLabel") }, t("notice.close"));
  close.addEventListener("click", () => {
    notice.remove();
    options.onClose?.();
  });
  buttons.appendChild(close);
  replace(notice, body, buttons);
  el.notices.prepend(notice);
  while (el.notices.children.length > 3) el.notices.lastElementChild?.remove();
  return notice;
}

/** The keyed notice currently shown, if any. */
function noticeFor(key: string): HTMLElement | null {
  return Array.from(el.notices.children).find((n): n is HTMLElement => n instanceof HTMLElement && n.dataset.key === key) ?? null;
}

/** Shows "<context>: <reason>"; `context` is a dictionary key such as `error.start`. */
function reportError(context: StringKey, e: unknown, params?: Record<string, string>): void {
  console.error(context, e);
  notify(t("common.withReason", { context: t(context, params), reason: describeError(e) }), "bad");
}

function engineCode(e: unknown): string | null {
  const ce = asCommandError(e);
  return ce?.kind === "engine" ? ce.code : null;
}

// ---------------------------------------------------------------- status & controls

function sessionActive(): boolean {
  return state.sessionState === "running" || state.sessionState === "loading";
}

function engineReady(): boolean {
  return state.bridge?.phase === "ready";
}

function renderStatus(): void {
  const view = describeStatus(state.bridge, state.sessionState);
  el.statusDot.dataset.tone = view.tone;
  el.statusLabel.textContent = view.label;
  const phase = state.bridge?.phase;
  const showDetail = phase === "failed" || phase === "restarting" || phase === "stopped";
  el.statusDetail.hidden = !(showDetail && state.bridge?.detail);
  el.statusDetail.textContent = showDetail ? (state.bridge?.detail ?? "") : "";
  el.restart.hidden = !(phase === "failed" || phase === "stopped");

  const ready = engineReady();
  el.start.disabled = !ready || state.busy;
  el.start.dataset.state = state.busy ? "busy" : sessionActive() ? "running" : "idle";
  el.start.textContent = sessionActive() ? t("start.stop") : t("start.start");
  for (const button of el.modes) {
    const mode = button.dataset.mode as Mode;
    button.setAttribute("aria-checked", String(mode === state.mode));
    button.disabled = sessionActive() || state.busy;
  }
  el.device.disabled = sessionActive() || state.busy || !ready;

  const info = state.bridge?.info;
  el.aboutEngine.textContent = info
    ? t(info.fake ? "about.engineFake" : "about.engine", { version: info.version, protocol: info.protocol_version })
    : t("about.engineOffline");
}

function renderModeNotes(): void {
  const partner = state.settings?.partner_language ?? "ja";
  if (el.modeNotes.interpret) {
    el.modeNotes.interpret.textContent = t("mode.interpret.note", {
      own: languageName("zh"),
      partner: languageName(partner),
    });
  }
}

function renderDevices(): void {
  const kind = state.mode === "media" ? "loopback" : "microphone";
  const list = state.devices.filter((d) => d.kind === kind);
  const selected = state.mode === "media" ? state.settings?.media_device_id : state.settings?.interpret_device_id;
  const fallback = list.find((d) => d.is_default);
  const options = [
    h("option", { value: "" }, fallback ? t("device.defaultNamed", { name: fallback.name }) : t("device.systemDefault")),
    ...list.map((d) => h("option", { value: d.id }, d.name)),
  ];
  replace(el.device, ...options);
  const known = list.some((d) => d.id === selected);
  el.device.value = selected && known ? selected : "";
  el.deviceHint.textContent =
    selected && !known
      ? t("device.hint.missing")
      : state.mode === "media"
        ? t("device.hint.media")
        : t("device.hint.interpret");
}

async function setMode(mode: Mode): Promise<void> {
  if (mode === state.mode || sessionActive()) return;
  state.mode = mode;
  renderStatus();
  renderDevices();
  const deviceId = mode === "media" ? state.settings?.media_device_id : state.settings?.interpret_device_id;
  try {
    const { settings } = await api.settingsSet(devicePatch(mode, deviceId ?? null));
    state.settings = settings;
  } catch (e) {
    reportError("error.saveMode", e);
  }
}

async function setDevice(deviceId: string | null): Promise<void> {
  try {
    const { settings } = await api.settingsSet(devicePatch(state.mode, deviceId));
    state.settings = settings;
    renderDevices();
  } catch (e) {
    reportError("error.saveDevice", e);
  }
}

async function toggleSession(): Promise<void> {
  if (state.busy) return;
  state.busy = true;
  renderStatus();
  try {
    if (sessionActive()) {
      await api.sessionStop();
      state.sessionState = "idle";
    } else {
      const waiting = state.settings ? blockingDownloads(state.settings.asr, state.downloads) : [];
      if (waiting.length) {
        // Starting now would only fail with model_not_downloaded.
        notify(t("notice.waitDownloads", { models: joinList(waiting) }));
        return;
      }
      const deviceId = el.device.value === "" ? null : el.device.value;
      const sessionId = await api.sessionStart(state.mode, deviceId);
      state.sessionId = sessionId;
      state.subtitles = emptySubtitles(sessionId);
      state.sessionState = "loading";
      renderTranscript();
      if (state.overlay && !state.overlay.visible) {
        await setOverlayVisible(true);
      }
    }
  } catch (e) {
    reportError(sessionActive() ? "error.stop" : "error.start", e);
  } finally {
    state.busy = false;
    renderStatus();
  }
}

// ---------------------------------------------------------------- transcript

const lineElements = new Map<number, HTMLLIElement>();

function updateLineElement(li: HTMLLIElement, line: SubtitleLine): void {
  li.classList.toggle("is-partial", !line.isFinal);
  const [time, lang, text] = li.children as unknown as [HTMLElement, HTMLElement, HTMLElement];
  time.textContent = formatTimecode(line.startSample);
  lang.textContent = languageShort(line.sourceLanguage);
  lang.title = t("transcript.langTitle", {
    source: languageName(line.sourceLanguage),
    target: languageName(line.targetLanguage),
  });
  const [orig, tr] = text.children as unknown as [HTMLElement, HTMLElement];
  orig.textContent = line.original;
  orig.lang = htmlLang(line.sourceLanguage);
  tr.textContent = line.translation;
  tr.lang = htmlLang(line.targetLanguage);
}

function newLineElement(): HTMLLIElement {
  return h(
    "li",
    { class: "utt" },
    h("span", { class: "utt-time" }),
    h("span", { class: "utt-lang" }),
    h("div", { class: "utt-text" }, h("p", { class: "utt-orig" }), h("p", { class: "utt-tr" })),
  );
}

function renderTranscript(): void {
  const scroller = el.transcript.closest(".content");
  const nearBottom = scroller ? scroller.scrollHeight - scroller.scrollTop - scroller.clientHeight < 80 : false;
  const keep = new Set<number>();
  let previous: HTMLLIElement | null = null;
  for (const line of state.subtitles.lines) {
    keep.add(line.utteranceId);
    let li = lineElements.get(line.utteranceId);
    if (!li) {
      li = newLineElement();
      lineElements.set(line.utteranceId, li);
    }
    updateLineElement(li, line);
    const expectedNext: ChildNode | null = previous ? previous.nextSibling : el.transcript.firstChild;
    if (expectedNext !== li) el.transcript.insertBefore(li, expectedNext);
    previous = li;
  }
  for (const [id, li] of lineElements) {
    if (!keep.has(id)) {
      li.remove();
      lineElements.delete(id);
    }
  }
  el.transcriptEmpty.hidden = state.subtitles.lines.length > 0;
  if (nearBottom && scroller) scroller.scrollTop = scroller.scrollHeight;
}

// ---------------------------------------------------------------- overlay controls

function renderOverlay(): void {
  const o = state.overlay;
  el.overlayVisible.checked = o?.visible ?? false;
  el.overlayClick.checked = o?.click_through ?? false;
  if (!o) {
    el.shortcutNote.textContent = "";
    return;
  }
  const keys = o.shortcut.replace("CommandOrControl", t("transcript.shortcutCtrl"));
  el.shortcutNote.textContent = o.shortcut_registered
    ? t("transcript.shortcutOn", { keys })
    : t("transcript.shortcutTaken", { keys });
}

async function setOverlayVisible(visible: boolean): Promise<void> {
  try {
    state.overlay = await api.overlaySetVisible(visible);
  } catch (e) {
    reportError(visible ? "error.showOverlay" : "error.hideOverlay", e);
  }
  renderOverlay();
}

// ---------------------------------------------------------------- models page: hardware

const CUBLAS_SOURCE: Readonly<Record<string, StringKey>> = {
  runtime: "hw.cublasSource.runtime",
  configured: "hw.cublasSource.configured",
  system: "hw.cublasSource.system",
};

/** "text（note）" in the interface language's punctuation. */
function withNote(text: string, note: string): string {
  return t("common.parenthetical", { text, note });
}

function renderHardware(): void {
  const hw = state.hardware;
  if (!hw) {
    replace(el.hardware, h("dt", {}, t("hw.status")), h("dd", {}, t("hw.detecting")));
    el.hardwareErrors.hidden = true;
    replace(el.recommendation, h("p", { class: "block-lede" }, t("rec.pending")));
    return;
  }
  const x = hw.hardware;
  const cores =
    x.cpu.physical_cores && x.cpu.logical_cores
      ? t("hw.cores", { physical: x.cpu.physical_cores, logical: x.cpu.logical_cores })
      : x.cpu_count
        ? tp("hw.threads", x.cpu_count)
        : t("hw.coresUnknown");
  const gpus = x.apple_silicon
    ? t("hw.appleSilicon")
    : x.gpus.length
      ? joinList(
          x.gpus.map((g) => {
            const extra = [
              t("hw.gpuVram", { size: formatMiB(g.memory_total_mb) }),
              g.driver_version ? t("hw.gpuDriver", { version: g.driver_version }) : null,
              g.compute_capability ? t("hw.gpuCompute", { capability: g.compute_capability }) : null,
            ].filter((s): s is string => s !== null);
            return withNote(g.name, extra.join(t("common.clauseSeparator")));
          }),
        )
      : t("hw.noNvidia");
  const cublasSource = CUBLAS_SOURCE[x.cublas.source ?? ""];
  const cublas = !x.cublas.supported_platform
    ? t("hw.cublasNotUsed")
    : x.cublas.available
      ? t("hw.cublasAvailable", {
          source: cublasSource ? t(cublasSource) : (x.cublas.source ?? t("hw.cublasSourceUnknown")),
        })
      : t("hw.cublasMissing");
  const ts = x.translation_server;
  const server = !ts
    ? t("hw.serverNotDetected")
    : ts.reachable
      ? t("hw.serverReachable", {
          url: ts.base_url,
          where: ts.is_local ? t("hw.serverLocal") : t("hw.serverRemote"),
          models: ts.models === null ? t("hw.serverModelsUnknown") : tp("hw.serverModelCount", ts.models.length),
        })
      : ts.error
        ? t("hw.serverUnreachableError", { url: ts.base_url, error: ts.error })
        : t("hw.serverUnreachable", { url: ts.base_url });
  const memory =
    x.memory.total_bytes === null
      ? t("common.unknown")
      : x.memory.available_bytes === null
        ? formatBytes(x.memory.total_bytes)
        : t("hw.memoryAvailable", {
            total: formatBytes(x.memory.total_bytes),
            available: formatBytes(x.memory.available_bytes),
          });
  replace(
    el.hardware,
    h("dt", {}, t("hw.os")),
    h("dd", {}, x.os_version.startsWith(osName(x.os)) ? x.os_version : `${osName(x.os)} ${x.os_version}`.trim()),
    h("dt", {}, t("hw.cpu")),
    h("dd", {}, t("hw.cpuValue", { model: x.cpu.model ?? x.machine ?? t("common.unknown"), cores })),
    h("dt", {}, t("hw.memory")),
    h("dd", {}, memory),
    h("dt", {}, t("hw.gpu")),
    h("dd", {}, gpus),
    h("dt", {}, t("hw.cublas")),
    h("dd", {}, cublas),
    h("dt", {}, t("hw.server")),
    h("dd", {}, server),
  );
  el.hardwareErrors.hidden = x.errors.length === 0;
  replace(el.hardwareErrors, ...x.errors.map((e) => h("li", { class: "warning" }, t("hw.incomplete", { error: e }))));
  renderRecommendation();
}

function describeChoice(c: AsrChoice): string {
  const parts = [deviceLabel(c.device), computeTypeLabel(c.compute_type)];
  if (c.device !== "cuda") parts.push(threadsLabel(c.cpu_threads));
  return withNote(c.model, parts.join(t("common.clauseSeparator")));
}

function renderRecommendation(): void {
  const rec = state.hardware?.recommendation;
  if (!rec) return;
  const notLocal = t("rec.notLocal");
  const dl = h(
    "dl",
    { class: "facts" },
    h("dt", {}, t("rec.tier")),
    h("dd", {}, tierLabel(rec.tier)),
    h("dt", {}, t("rec.fastAsr")),
    h("dd", {}, describeChoice(rec.fast_asr)),
    h("dt", {}, t("rec.finalAsr")),
    h("dd", {}, describeChoice(rec.final_asr)),
    h("dt", {}, t("rec.fastTranslation")),
    h("dd", {}, rec.translation_fast ?? notLocal),
    h("dt", {}, t("rec.finalTranslation")),
    h("dd", {}, rec.translation_final ?? notLocal),
    rec.translation_budget_gb === null ? null : h("dt", {}, t("rec.budget")),
    rec.translation_budget_gb === null
      ? null
      : h("dd", {}, t("common.approxGb", { gb: rec.translation_budget_gb.toFixed(1) })),
  );
  const notes = h(
    "ul",
    { class: "notes" },
    ...rec.reasons.map((r) => h("li", {}, formatEngineMessage(r))),
    ...rec.warnings.map((w) => h("li", { class: "warning" }, formatEngineMessage(w))),
    rec.gpu_runtime_missing ? h("li", {}, t("rec.gpuRuntimeMissing")) : null,
  );
  const apply = h("button", { type: "button", class: "primary", id: "apply-recommendation" }, t("rec.apply"));
  apply.disabled = !engineReady();
  apply.addEventListener("click", () => void applyRecommendation(apply));
  replace(
    el.recommendation,
    dl,
    notes,
    h(
      "div",
      { class: "form-actions" },
      apply,
      h("span", { class: "form-status" }, t("rec.applyNote")),
    ),
  );
}

async function applyRecommendation(button: HTMLButtonElement | null): Promise<void> {
  if (button) button.disabled = true;
  try {
    const result = await api.applyRecommendation();
    showAppliedSettings(result, true);
    const r = result.recommendation;
    notify(
      t(sessionActive() ? "rec.appliedWhileRunning" : "rec.applied", {
        fast: r.fast_asr.model,
        final: r.final_asr.model,
        device: deviceLabel(r.fast_asr.device),
      }),
    );
    await offerMissingModels();
  } catch (e) {
    reportError("error.applyRecommendation", e);
  } finally {
    if (button) button.disabled = !engineReady();
  }
}

/**
 * Puts the result of `settings.apply_recommendation` on screen. `force`
 * replaces unsaved edits in the combination form: right for the user's own
 * "套用", not for the automatic first-run apply.
 */
function showAppliedSettings(result: AppliedRecommendation, force: boolean): void {
  state.settings = result.settings;
  if (state.hardware) state.hardware = { ...state.hardware, recommendation: result.recommendation };
  if (result.warnings.length) console.warn("settings fields replaced by defaults:", result.warnings);
  // The saved settings now equal the recommendation: drop the offer, and any
  // preview still in flight (computed against the old settings).
  forgetPreview();
  fillCombo(result.settings, force);
  renderHardware();
  renderModels();
  scheduleCheck(0);
}

// ---------------------------------------------------------------- automatic recommendation

const FIRST_RUN_NOTICE = "first-run";
const OFFER_NOTICE = "recommendation-offer";
const MISSING_NOTICE = "missing-models";
const previews = new Latest();

/** Saved settings changed: hide the offer and ignore previews still in flight. */
function forgetPreview(): void {
  previews.invalidate();
  state.preview = null;
  noticeFor(OFFER_NOTICE)?.remove();
}

/**
 * First run (no settings file yet): wait for detection, apply the engine's
 * recommendation and say what was chosen. Never runs once the user has saved
 * a combination; failures keep the engine's safe defaults and say so.
 */
async function firstRunFlow(): Promise<void> {
  state.firstRunActive = true;
  const reviewAction = { label: t("firstRun.review"), run: () => showPage("models") };
  try {
    const outcome = await runFirstRun(
      {
        detect: () => api.hardwareDetect(),
        apply: () => api.applyRecommendation(true),
        userChoseModels: () => state.userChoseModels,
        describe: describeError,
      },
      state.settings ?? DEFAULT_SETTINGS,
    );
    switch (outcome.kind) {
      case "applied":
        state.hardware = outcome.report;
        showAppliedSettings(outcome.applied, false);
        notify(outcome.notice, "info", [reviewAction], { key: FIRST_RUN_NOTICE });
        await offerMissingModels();
        break;
      case "kept_defaults":
        if (outcome.report) state.hardware = outcome.report;
        renderHardware();
        notify(outcome.notice, "bad", [reviewAction], { key: FIRST_RUN_NOTICE });
        // A timed-out apply may still have been written: show what is saved.
        await reloadSettings();
        break;
      case "skipped":
        break;
    }
  } finally {
    state.firstRunActive = false;
  }
  await checkRecommendation();
}

/** Re-reads the saved settings into the UI (form edits are kept). */
async function reloadSettings(): Promise<void> {
  try {
    const { settings } = await api.settingsGet();
    state.settings = settings;
    fillCombo(settings);
    renderModels();
  } catch (e) {
    reportError("error.readSettings", e);
  }
}

/**
 * Asks the engine whether applying its recommendation would change the saved
 * settings, and shows or hides the offer. Background check: a failure is
 * logged, not shown (the models page reports detection problems itself).
 */
async function checkRecommendation(): Promise<void> {
  if (!engineReady() || state.firstRunActive) return;
  const token = previews.next();
  let preview: RecommendationPreview;
  try {
    preview = await api.previewRecommendation();
  } catch (e) {
    console.error("settings.preview_recommendation failed:", e);
    return;
  }
  if (!previews.isCurrent(token)) return;
  state.preview = preview;
  renderOffer();
}

function renderOffer(): void {
  const preview = state.preview;
  const dismissed = state.settings?.ui.dismissed_recommendations ?? [];
  if (!preview || !shouldOfferRecommendation(preview, dismissed) || state.closedOffers.has(preview.fingerprint)) {
    noticeFor(OFFER_NOTICE)?.remove();
    return;
  }
  // Already showing this one: leave it where it is.
  if (noticeFor(OFFER_NOTICE)?.dataset.fingerprint === preview.fingerprint) return;
  const notice = notify(
    offerText(preview),
    "update",
    [
      { label: t("offer.apply"), run: () => void applyRecommendation(null) },
      { label: t("offer.dismiss"), run: () => void dismissOffer(preview.fingerprint) },
    ],
    { key: OFFER_NOTICE, onClose: () => state.closedOffers.add(preview.fingerprint) },
  );
  notice.dataset.fingerprint = preview.fingerprint;
}

/** "不要再提醒": remembered per recommendation in the settings' opaque `ui` object. */
async function dismissOffer(fingerprint: string): Promise<void> {
  noticeFor(OFFER_NOTICE)?.remove();
  state.closedOffers.add(fingerprint);
  const current = state.settings?.ui.dismissed_recommendations ?? [];
  try {
    const { settings } = await api.settingsSet({ ui: { dismissed_recommendations: dismissedWith(current, fingerprint) } });
    state.settings = settings;
  } catch (e) {
    reportError("error.saveDismissal", e);
  }
}

/**
 * Refreshes the model list and, if a chosen ASR model is not downloaded yet,
 * offers to download exactly those (never automatically: they are GB-sized).
 * Returns whether anything is missing.
 */
async function offerMissingModels(): Promise<boolean> {
  if (!state.settings || !engineReady()) return false;
  try {
    state.models = await api.modelsList();
  } catch (e) {
    reportError("error.readModels", e);
    return false;
  }
  renderModels();
  const missing = missingModels(state.settings.asr, state.models.asr);
  state.missing = missing.length ? missing : null;
  // An earlier prompt may be about models no longer chosen.
  if (!state.missing) noticeFor(MISSING_NOTICE)?.remove();
  renderMissingNotice();
  return missing.length > 0;
}

function renderMissingNotice(): void {
  if (!state.missing) return;
  const view = missingModelsView(state.missing, state.models?.asr ?? null, state.downloads);
  const shown = noticeFor(MISSING_NOTICE);
  if (shown?.dataset.state === view.state) {
    // Progress updates only change the text; keep the buttons (and focus) in place.
    const p = shown.querySelector("p");
    if (p) p.textContent = view.text;
    // notify() matches duplicates by data-text; keep it in step with the text.
    shown.dataset.text = view.text;
    return;
  }
  const actions: NoticeAction[] = [];
  if (view.state === "offer") actions.push({ label: t("missing.download"), run: downloadMissingModels });
  if (view.state === "failed") actions.push({ label: t("common.redownload"), run: downloadMissingModels });
  const notice = notify(view.text, view.state === "failed" ? "bad" : "update", actions, {
    key: MISSING_NOTICE,
    onClose: () => {
      state.missing = null;
    },
  });
  notice.dataset.state = view.state;
  if (view.state === "done") state.missing = null;
}

function downloadMissingModels(): void {
  for (const m of state.missing ?? []) {
    const known = state.models?.asr.find((x) => x.id === m.id);
    if (known?.downloaded || state.downloads.get(m.id)?.state === "downloading") continue;
    void downloadModel(m.id);
  }
  renderMissingNotice();
}

// ---------------------------------------------------------------- models page: GPU runtime

function renderGpuRuntime(): void {
  const rt = state.models?.gpu_runtime;
  if (!rt) {
    replace(el.gpuRuntime, h("p", { class: "gpu-state rail-hint" }, t("common.loading")));
    return;
  }
  if (!rt.supported) {
    replace(
      el.gpuRuntime,
      h("p", { class: "gpu-state" }, t("gpu.unsupported")),
    );
    return;
  }
  const progress = state.downloads.get(rt.id);
  if (progress?.state === "downloading") {
    const pct = percent(progress.downloaded_bytes, progress.total_bytes);
    const finishing = progress.total_bytes > 0 && progress.downloaded_bytes >= progress.total_bytes;
    replace(
      el.gpuRuntime,
      h(
        "div",
        { class: "progress" },
        h("progress", { max: 100, value: pct, "aria-label": t("gpu.progressLabel") }),
        h(
          "span",
          {},
          finishing
            ? t("gpu.finishing")
            : t("gpu.progress", {
                percent: pct,
                done: formatBytes(progress.downloaded_bytes),
                total: formatBytes(progress.total_bytes),
              }),
        ),
      ),
    );
    return;
  }
  if (rt.installed) {
    const what = [rt.package ?? "cuBLAS", rt.version].filter(Boolean).join(" ");
    replace(
      el.gpuRuntime,
      h("p", { class: "gpu-state" }, rt.path ? t("gpu.installedAt", { what, path: rt.path }) : t("gpu.installed", { what })),
      rt.cublas_available ? null : h("p", { class: "gpu-state warning" }, t("gpu.noCublas")),
    );
    return;
  }

  const hw = state.hardware;
  const size = rt.download_bytes ? t("gpu.sizeApprox", { size: formatBytes(rt.download_bytes) }) : t("gpu.sizeUnknown");
  const pkg = [rt.package ?? "nvidia-cublas-cu12", rt.version].filter(Boolean).join(" ");
  const situation = hw?.recommendation.gpu_runtime_missing
    ? t("gpu.situationMissing")
    : hw && hw.hardware.gpus.length === 0
      ? t("gpu.situationNoGpu")
      : null;
  const licenseName = rt.license ?? "NVIDIA CUDA EULA";
  const read = h("button", { type: "button", class: "ghost" }, t("gpu.readLicense"));
  read.addEventListener("click", () => void openEula());
  const consent = h("input", { type: "checkbox", id: "gpu-eula-consent" });
  consent.checked = state.gpuLicenseAccepted;
  const download = h("button", { type: "button", class: "primary" }, t("gpu.download"));
  download.disabled = !state.gpuLicenseAccepted || !engineReady();
  consent.addEventListener("change", () => {
    state.gpuLicenseAccepted = consent.checked;
    download.disabled = !consent.checked || !engineReady();
  });
  download.addEventListener("click", () => void downloadGpuRuntime());
  replace(
    el.gpuRuntime,
    situation ? h("p", { class: "gpu-state" }, situation) : null,
    h("p", { class: "gpu-state" }, t("gpu.consentNotice", { package: pkg, size, license: licenseName })),
    rt.license_url ? h("p", { class: "gpu-state eula-url url" }, rt.license_url) : null,
    progress?.state === "error"
      ? h("p", { class: "field-error" }, t("gpu.lastFailed", { error: progress.error ?? t("common.unknownReason") }))
      : null,
    h("label", { class: "consent", for: "gpu-eula-consent" }, consent, h("span", {}, t("gpu.agree"))),
    h("div", { class: "gpu-actions" }, download, canOpenLicenseUrl(rt.license_url) ? read : null),
  );
}

async function openEula(): Promise<void> {
  try {
    await api.openNvidiaEula();
  } catch (e) {
    reportError("error.openEula", e);
  }
}

async function downloadGpuRuntime(): Promise<void> {
  // The engine refuses without the flag too; this keeps the button honest.
  if (!state.gpuLicenseAccepted) return;
  const id = state.models?.gpu_runtime.id ?? GPU_RUNTIME_ID;
  state.downloads.set(id, { model: id, downloaded_bytes: 0, total_bytes: 0, state: "downloading", error: null });
  // Agreement is per download: a later retry asks again.
  state.gpuLicenseAccepted = false;
  renderGpuRuntime();
  try {
    await api.modelsDownload(id, true);
  } catch (e) {
    const code = engineCode(e);
    if (code === "download_in_progress") {
      // Already running (e.g. started before a page reload); progress events follow.
      notify(describeError(e));
      return;
    }
    state.downloads.delete(id);
    reportError("error.downloadGpuRuntime", e);
    if (code === "gpu_runtime_unsupported") void loadModels();
    renderGpuRuntime();
  }
}

/** The ASR layer will not retry CUDA in this engine process: restart it when safe. */
function onGpuRuntimeInstalled(): void {
  const others = Array.from(state.downloads.values()).filter(
    (d) => d.state === "downloading" && d.model !== GPU_RUNTIME_ID,
  ).length;
  if (afterGpuRuntimeInstalled(sessionActive(), others) === "restart") {
    notify(t("gpu.installedRestarting"));
    void restartEngine();
    return;
  }
  notify(t("gpu.installedBusy"), "update", [{ label: t("engine.restart"), run: () => void restartEngine() }]);
  void loadModels();
}

// ---------------------------------------------------------------- models page: lists

function downloadCell(model: { id: string; downloaded: boolean }): HTMLElement {
  const progress = state.downloads.get(model.id);
  if (progress && progress.state === "downloading") {
    const pct = percent(progress.downloaded_bytes, progress.total_bytes);
    return h(
      "div",
      { class: "progress" },
      h("progress", { max: 100, value: pct, "aria-label": t("asr.progressLabel", { model: model.id }) }),
      h("span", {}, t("asr.progress", { percent: pct, done: formatBytes(progress.downloaded_bytes) })),
    );
  }
  if (model.downloaded) return h("span", { class: "ok" }, t("asr.downloaded"));
  const wrap = h("div", {});
  if (progress?.state === "error") {
    wrap.append(
      h("p", { class: "field-error" }, t("asr.downloadFailed", { error: progress.error ?? t("common.unknownReason") })),
    );
  }
  const button = h(
    "button",
    { type: "button", class: "ghost" },
    progress?.state === "error" ? t("common.redownload") : t("asr.download"),
  );
  button.addEventListener("click", () => void downloadModel(model.id));
  wrap.append(button);
  return wrap;
}

function roleNames(roles: readonly string[]): string {
  return joinList(roles.map((r) => (r === "fast" ? t("role.fast") : t("role.final"))));
}

function memoryCell(cpu: number | null, gpu: number | null): string {
  const parts = [
    cpu === null ? null : t("asr.memoryCpu", { size: formatMiB(cpu) }),
    gpu === null ? null : t("asr.memoryGpu", { size: formatMiB(gpu) }),
  ];
  const known = parts.filter((p): p is string => p !== null);
  return known.length ? known.join(t("common.altSeparator")) : t("common.unknown");
}

function renderModels(): void {
  const m = state.models;
  const s = state.settings;
  renderGpuRuntime();
  if (!m) {
    replace(el.asrModels, h("tr", {}, h("td", { colspan: 6 }, t("common.loading"))));
    return;
  }
  replace(
    el.asrModels,
    ...m.asr.map((model) => {
      const uses: string[] = [];
      if (s?.asr.fast_model === model.id) uses.push(t("role.fast"));
      if (s?.asr.final_model === model.id) uses.push(t("role.final"));
      return h(
        "tr",
        {},
        h(
          "td",
          {},
          h("span", { class: "model-id" }, model.id),
          uses.length ? h("span", { class: "tag" }, t("asr.inUse", { roles: joinList(uses) })) : null,
          model.recommended_for.length
            ? h("span", { class: "tag tag-quiet" }, t("common.recommendedFor", { roles: roleNames(model.recommended_for) }))
            : null,
          model.no_speech_reliable === false
            ? h("span", { class: "tag tag-quiet", title: t("asr.noSpeechHelp") }, t("asr.noSpeech"))
            : null,
        ),
        h("td", {}, formatBytes(model.size_bytes)),
        h("td", {}, speedTierLabel(model.speed_tier)),
        h("td", {}, memoryCell(model.cpu_ram_mb, model.vram_mb)),
        h("td", {}, model.license),
        h("td", {}, downloadCell(model)),
      );
    }),
  );

  // `tr`, not `t`: `t` is the translation function.
  const tr = m.translation;
  const installed = tr.models.filter((x) => x.installed === true).length;
  el.trSource.textContent = !tr.server.reachable
    ? tr.server.error
      ? t("tr.unreachableError", { url: tr.server.base_url, error: tr.server.error })
      : t("tr.unreachable", { url: tr.server.base_url })
    : tp(tr.server.is_local ? "tr.installedCountLocal" : "tr.installedCount", installed, { url: tr.server.base_url });
  replace(
    el.trPresets,
    ...tr.models.map((p) =>
      h(
        "li",
        {},
        h("span", { class: "preset-name" }, p.label),
        p.label !== p.name ? h("span", { class: "preset-meta" }, p.name) : null,
        p.recommended_for.length
          ? h("span", { class: "tag tag-quiet" }, t("common.recommendedFor", { roles: roleNames(p.recommended_for) }))
          : null,
        p.commercial_use === false ? h("span", { class: "tag tag-warn" }, t("tr.nonCommercial")) : null,
        p.excluded_regions.length
          ? h("span", { class: "tag tag-warn" }, t("tr.regionExcluded", { regions: regionNames(p.excluded_regions) }))
          : null,
        h(
          "span",
          { class: "preset-meta" },
          p.known ? t("tr.license", { license: p.license ?? t("common.unknown") }) : t("tr.unlisted"),
        ),
        p.approx_vram_gb === null
          ? null
          : h("span", { class: "preset-meta" }, t("common.approxGb", { gb: p.approx_vram_gb.toFixed(1) })),
        h(
          "span",
          { class: "preset-meta" },
          p.installed === null ? t("tr.stateUnknown") : p.installed ? t("tr.stateInstalled") : t("tr.stateMissing"),
        ),
        p.license_url ? h("span", { class: "preset-meta url" }, p.license_url) : null,
      ),
    ),
  );
  replace(el.trNames, ...tr.models.map((x) => h("option", { value: x.name })));
  renderComboOptions();
}

async function loadModels(): Promise<void> {
  if (!engineReady()) return;
  el.modelsRefresh.disabled = true;
  // Both are answered by the engine's background worker in order; matching is
  // by id, so neither waits on the other's position in the reply stream.
  const [hardware, models] = await Promise.allSettled([api.hardwareDetect(), api.modelsList()]);
  if (hardware.status === "fulfilled") state.hardware = hardware.value;
  else reportError("error.detectHardware", hardware.reason);
  if (models.status === "fulfilled") state.models = models.value;
  else reportError("error.readModels", models.reason);
  el.modelsRefresh.disabled = false;
  renderHardware();
  renderModels();
  renderMissingNotice();
  scheduleCheck(0);
}

async function downloadModel(id: string): Promise<void> {
  state.downloads.set(id, { model: id, downloaded_bytes: 0, total_bytes: 0, state: "downloading", error: null });
  renderModels();
  try {
    await api.modelsDownload(id);
  } catch (e) {
    if (engineCode(e) === "download_in_progress") {
      notify(describeError(e));
      return;
    }
    state.downloads.delete(id);
    renderModels();
    renderMissingNotice();
    reportError("error.downloadModel", e, { model: id });
  }
}

// ---------------------------------------------------------------- models page: combination form

const COMBO_FIELDS: (keyof CombinationForm)[] = [
  "fast_model",
  "final_model",
  "device",
  "translation_fast",
  "translation_final",
  "fast_compute_type",
  "final_compute_type",
  "fast_cpu_threads",
  "final_cpu_threads",
  "cuda_library_dirs",
];

type FieldElement = HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement;

function namedField(form: HTMLFormElement, name: string): FieldElement {
  const found = form.elements.namedItem(name);
  if (
    !(found instanceof HTMLInputElement || found instanceof HTMLSelectElement || found instanceof HTMLTextAreaElement)
  ) {
    throw new Error(`form #${form.id} has no field ${name}`);
  }
  return found;
}

function readCombo(): CombinationForm {
  const form = {} as CombinationForm;
  for (const name of COMBO_FIELDS) form[name] = namedField(el.comboForm, name).value;
  return form;
}

/** Select options; values not offered by the engine are kept so nothing is silently changed. */
function setOptions(select: HTMLSelectElement, options: { value: string; label: string }[], value: string): void {
  // An empty value only happens before settings are loaded; nothing to preserve then.
  const keep = value === "" || options.some((o) => o.value === value);
  const all = keep ? options : [{ value, label: t("combo.notInList", { value }) }, ...options];
  replace(select, ...all.map((o) => h("option", { value: o.value }, o.label)));
  select.value = value;
}

function renderComboOptions(): void {
  const current = readCombo();
  const asr = (state.models?.asr ?? []).map((x) => ({
    value: x.id,
    label: x.downloaded ? x.id : t("combo.notDownloaded", { id: x.id }),
  }));
  for (const key of ["fast_model", "final_model"] as const) {
    setOptions(namedField(el.comboForm, key) as HTMLSelectElement, asr, current[key]);
  }
  const precisions = [{ value: "", label: t("common.auto") }, ...COMPUTE_TYPES.map((c) => ({ value: c, label: c }))];
  for (const key of ["fast_compute_type", "final_compute_type"] as const) {
    setOptions(namedField(el.comboForm, key) as HTMLSelectElement, precisions, current[key]);
  }
}

/** Puts saved settings into the form unless the user has unsaved edits (or `force`). */
function fillCombo(settings: Settings, force = false): void {
  if (state.comboDirty && !force) return;
  const form = combinationFormFromSettings(settings);
  // Options first, so the select can hold the saved value.
  for (const key of ["fast_model", "final_model", "fast_compute_type", "final_compute_type"] as const) {
    const select = namedField(el.comboForm, key) as HTMLSelectElement;
    if (!Array.from(select.options).some((o) => o.value === form[key])) {
      select.append(h("option", { value: form[key] }, form[key] || t("common.auto")));
    }
  }
  for (const name of COMBO_FIELDS) namedField(el.comboForm, name).value = form[name];
  state.comboDirty = false;
  renderComboOptions();
  showComboErrors({});
  el.comboStatus.textContent = "";
}

function showFieldErrors<K extends string>(form: HTMLFormElement, fields: readonly K[], errors: Partial<Record<K, string>>): void {
  for (const name of fields) {
    const message = errors[name] ?? "";
    const slot = form.querySelector<HTMLElement>(`[data-error-for="${name}"]`);
    if (slot) slot.textContent = message;
    const input = namedField(form, name);
    input.closest(".field")?.classList.toggle("has-error", message !== "");
    input.setAttribute("aria-invalid", String(message !== ""));
  }
}

function showComboErrors(errors: CombinationErrors): void {
  showFieldErrors(el.comboForm, COMBO_FIELDS, errors);
  // Advanced fields are hidden by default; open the section when one is wrong.
  const advanced = el.comboForm.querySelector("details");
  const advancedKeys: (keyof CombinationForm)[] = [
    "fast_compute_type",
    "final_compute_type",
    "fast_cpu_threads",
    "final_cpu_threads",
    "cuda_library_dirs",
  ];
  if (advanced && advancedKeys.some((k) => errors[k])) advanced.open = true;
}

function renderCheck(): void {
  const v = state.check;
  const list = (items: readonly EngineMessage[], cls: string | null) =>
    h("ul", {}, ...items.map((m) => h("li", cls ? { class: cls } : {}, formatEngineMessage(m))));
  switch (v.state) {
    case "local_errors":
      replace(el.comboCheck, h("p", { class: "is-error" }, t("check.fixFirst")));
      break;
    case "checking":
      replace(el.comboCheck, h("p", {}, t("check.checking")));
      break;
    case "checked": {
      const { ok, errors, warnings } = v.check;
      replace(
        el.comboCheck,
        h(
          "p",
          ok ? {} : { class: "is-error" },
          ok ? (warnings.length ? t("check.okWithWarnings") : t("check.ok")) : t("check.notOk"),
        ),
        errors.length ? list(errors, "is-error") : null,
        warnings.length ? list(warnings, null) : null,
      );
      break;
    }
    case "rejected":
      replace(el.comboCheck, h("p", { class: "is-error" }, t("check.rejected", { message: v.message })));
      break;
    case "unavailable":
      replace(el.comboCheck, h("p", {}, t("check.unavailable", { reason: v.message })));
      break;
  }
  el.comboSave.disabled = !canSave(v) || !engineReady();
}

const checks = new Latest();
let checkTimer: number | undefined;

/** Local checks now, then (debounced) `models.validate` with the form as a candidate. */
function scheduleCheck(delay = CHECK_DEBOUNCE_MS): void {
  window.clearTimeout(checkTimer);
  const form = readCombo();
  const errors = validateCombinationForm(form);
  showComboErrors(errors);
  if (Object.keys(errors).length > 0) {
    checks.invalidate();
    state.check = { state: "local_errors" };
  } else if (!engineReady()) {
    checks.invalidate();
    state.check = { state: "unavailable", message: t("check.engineNotReady") };
  } else {
    state.check = { state: "checking" };
    checkTimer = window.setTimeout(() => void runCheck(form), delay);
  }
  renderCheck();
}

async function runCheck(form: CombinationForm): Promise<void> {
  const token = checks.next();
  let next: CheckView;
  try {
    next = { state: "checked", check: await api.modelsValidate(combinationPatch(form)) };
  } catch (e) {
    const ce = asCommandError(e);
    if (ce?.kind === "engine" && ce.code === "invalid_settings") {
      const field = combinationFieldForEnginePath(ce.message);
      if (checks.isCurrent(token) && field) showComboErrors({ [field]: t("form.engineRejected", { message: ce.message }) });
      next = { state: "rejected", message: ce.message };
    } else {
      console.error("models.validate failed", e);
      next = { state: "unavailable", message: describeError(e) };
    }
  }
  // A slower answer for older input must not replace the answer for the current input.
  if (!checks.isCurrent(token)) return;
  state.check = next;
  renderCheck();
}

async function submitCombo(): Promise<void> {
  const form = readCombo();
  const errors = validateCombinationForm(form);
  showComboErrors(errors);
  if (Object.keys(errors).length > 0 || !canSave(state.check)) return;
  el.comboSave.disabled = true;
  el.comboStatus.textContent = t("common.saving");
  try {
    const { settings } = await api.settingsSet(combinationPatch(form));
    state.settings = settings;
    // A deliberate choice: never auto-applied over, and no offer right after saving
    // (it comes back on the next start or visit to this page if still different).
    state.userChoseModels = true;
    forgetPreview();
    fillCombo(settings, true);
    renderModels();
    el.comboStatus.textContent = sessionActive() ? t("combo.savedNextStart") : t("combo.saved");
    // The new combination may need other models (and an old prompt may be stale).
    void offerMissingModels();
  } catch (e) {
    const ce = asCommandError(e);
    const field =
      ce?.kind === "engine" && ce.code === "invalid_settings" ? combinationFieldForEnginePath(ce.message) : null;
    if (field && ce?.kind === "engine") {
      showComboErrors({ [field]: t("form.engineRejected", { message: ce.message }) });
      el.comboStatus.textContent = t("form.fixFields");
    } else {
      el.comboStatus.textContent = describeError(e);
    }
  } finally {
    renderCheck();
  }
}

// ---------------------------------------------------------------- settings page

const FORM_FIELDS: (keyof SettingsForm)[] = [
  "base_url",
  "api_key_env",
  "allow_restricted",
  "partner_language",
  "font_size",
  "background_opacity_percent",
  "position",
  "max_lines",
];

function readForm(): SettingsForm {
  const field = (name: keyof SettingsForm) => namedField(el.settingsForm, name);
  return {
    base_url: field("base_url").value,
    api_key_env: field("api_key_env").value,
    allow_restricted: (field("allow_restricted") as HTMLInputElement).checked,
    partner_language: field("partner_language").value,
    font_size: field("font_size").value,
    background_opacity_percent: field("background_opacity_percent").value,
    position: field("position").value,
    max_lines: field("max_lines").value,
  };
}

function fillForm(settings: Settings): void {
  const form = formFromSettings(settings);
  for (const name of FORM_FIELDS) {
    const input = namedField(el.settingsForm, name);
    const value = form[name];
    if (typeof value === "boolean") (input as HTMLInputElement).checked = value;
    else input.value = value;
  }
  showFormErrors({});
  renderPreview();
}

function showFormErrors(errors: FormErrors): void {
  showFieldErrors(el.settingsForm, FORM_FIELDS, errors);
}

function renderPreview(): void {
  const form = readForm();
  const size = Number.parseInt(form.font_size, 10);
  const opacity = Number.parseInt(form.background_opacity_percent, 10);
  el.oFontSize.textContent = Number.isFinite(size) ? `${size} px` : "";
  el.oOpacity.textContent = Number.isFinite(opacity) ? `${opacity}%` : "";
  if (Number.isFinite(size)) el.preview.style.setProperty("--fs", `${size}px`);
  if (Number.isFinite(opacity)) el.preview.style.setProperty("--plate", String(opacity / 100));
  el.preview.dataset.position = form.position;
}

function renderSettingsPath(): void {
  el.settingsPath.textContent = state.settingsPath ? t("settings.path", { path: state.settingsPath }) : "";
}

async function submitSettings(): Promise<void> {
  const form = readForm();
  const errors = validateSettingsForm(form);
  showFormErrors(errors);
  if (Object.keys(errors).length > 0) {
    el.settingsStatus.textContent = t("form.fixFields");
    return;
  }
  el.settingsStatus.textContent = t("common.saving");
  const restrictedChanged = state.settings?.translation.allow_restricted !== form.allow_restricted;
  try {
    const { settings } = await api.settingsSet(settingsPatchFromForm(form));
    state.settings = settings;
    await pushOverlayStyle();
    renderModeNotes();
    renderModels();
    el.settingsStatus.textContent = restrictedChanged ? t("settings.savedRegion") : t("settings.saved");
  } catch (e) {
    const ce = asCommandError(e);
    const field = ce?.kind === "engine" && ce.code === "invalid_settings" ? formFieldForEnginePath(ce.message) : null;
    if (field && ce?.kind === "engine") {
      showFormErrors({ [field]: t("form.engineRejected", { message: ce.message }) });
      el.settingsStatus.textContent = t("form.fixFields");
    } else {
      el.settingsStatus.textContent = describeError(e);
    }
  }
}

/** Sends the saved subtitle style to the overlay window. */
async function pushOverlayStyle(): Promise<void> {
  if (!state.settings) return;
  try {
    state.overlay = await api.overlaySetStyle(state.settings.subtitle);
    renderOverlay();
  } catch (e) {
    reportError("error.applyStyle", e);
  }
}

// ---------------------------------------------------------------- about

function renderAbout(): void {
  const a = state.about;
  el.aboutLicense.textContent = a ? t("about.license", { license: a.license }) : "";
  const app = a?.app_licenses ?? null;
  const engine = a?.engine_licenses ?? null;
  if (app || engine) {
    // Each folder is listed on its own line so its path stays selectable.
    replace(
      el.aboutThirdParty,
      t("about.thirdPartyHead"),
      app && h("br"),
      app && t("about.thirdPartyApp"),
      app && h("span", { class: "url" }, app),
      engine && h("br"),
      engine && t("about.thirdPartyEngine"),
      engine && h("span", { class: "url" }, engine),
    );
  } else {
    el.aboutThirdParty.textContent = t("about.thirdPartyDev");
  }
  el.openAppLicenses.hidden = !app;
  el.openEngineLicenses.hidden = !engine;
}

async function openLicenses(folder: LicenseFolder): Promise<void> {
  try {
    await api.openThirdPartyLicenses(folder);
  } catch (e) {
    reportError("error.openLicenses", e);
  }
}

// ---------------------------------------------------------------- interface language

/** Per-viewer cache of `ui.locale`, so the first paint already uses the chosen language. */
const UI_LOCALE_CACHE_KEY = "kikitori.uiLocale";

function readCachedUiLocale(): UiLocalePreference {
  try {
    const v = window.localStorage.getItem(UI_LOCALE_CACHE_KEY);
    if (v === "system" || isUiLocale(v)) return v;
  } catch (e) {
    // localStorage 可能被停用；只是少了快取，設定檔讀到後仍會套用正確的語系。
    console.warn("cannot read the cached interface language:", e);
  }
  return "system";
}

function cacheUiLocale(pref: UiLocalePreference): void {
  try {
    window.localStorage.setItem(UI_LOCALE_CACHE_KEY, pref);
  } catch (e) {
    console.warn("cannot cache the interface language:", e);
  }
}

/** Switches to `pref` (re-rendering everything through `onLocaleChanged`). */
function applyUiLocale(pref: UiLocalePreference): void {
  state.uiLocale = pref;
  cacheUiLocale(pref);
  setLocale(localeForPreference(pref, systemLanguages()));
}

/**
 * The saved `ui.locale` arrived (engine start): it wins over the cache, unless
 * the user picked a language while the engine was not ready — then that choice
 * is saved now instead.
 */
async function syncUiLocale(saved: UiLocalePreference): Promise<void> {
  if (state.uiLocaleUnsaved) {
    await saveUiLocale(state.uiLocale);
    return;
  }
  if (saved !== state.uiLocale) applyUiLocale(saved);
}

async function saveUiLocale(pref: UiLocalePreference): Promise<void> {
  if (!engineReady()) {
    state.uiLocaleUnsaved = true;
    return;
  }
  try {
    const { settings } = await api.settingsSet(uiLocalePatch(pref));
    state.settings = settings;
    state.uiLocaleUnsaved = false;
  } catch (e) {
    state.uiLocaleUnsaved = true;
    reportError("error.saveUiLocale", e);
  }
}

/** The user picked a language in the settings page: apply at once, then save. */
async function chooseUiLocale(pref: UiLocalePreference): Promise<void> {
  applyUiLocale(pref);
  await saveUiLocale(pref);
}

function renderUiLocaleOptions(): void {
  replace(
    el.uiLocale,
    h("option", { value: "system" }, t("settings.uiLocaleSystem")),
    ...UI_LOCALES.map((l) => h("option", { value: l, lang: htmlLangForLocale(l) }, NATIVE_NAMES[l])),
  );
  el.uiLocale.value = state.uiLocale;
}

/** The overlay window follows the main window's language (through Rust, see overlay.rs). */
async function pushOverlayLocale(): Promise<void> {
  try {
    state.overlay = await api.overlaySetLocale(getLocale());
  } catch (e) {
    reportError("error.setOverlayLocale", e);
  }
}

/** Redraws every text from state in the new language; notices already shown keep theirs. */
function onLocaleChanged(): void {
  applyStatic(document);
  renderUiLocaleOptions();
  if (state.check.state === "unavailable" && !engineReady()) {
    state.check = { state: "unavailable", message: t("check.engineNotReady") };
  }
  renderStatus();
  renderModeNotes();
  renderDevices();
  renderTranscript();
  renderOverlay();
  renderHardware();
  renderModels();
  renderCheck();
  renderSettingsPath();
  renderAbout();
  renderMissingNotice();
  void pushOverlayLocale();
}

// ---------------------------------------------------------------- navigation

function showPage(page: Page): void {
  state.page = page;
  for (const [name, section] of Object.entries(el.pages)) section.hidden = name !== page;
  for (const item of el.navItems) {
    if (item.dataset.page === page) item.setAttribute("aria-current", "page");
    else item.removeAttribute("aria-current");
  }
  if (page === "models") void loadModels().then(checkRecommendation);
}

// ---------------------------------------------------------------- engine data

async function loadEngineData(): Promise<void> {
  let isNew = false;
  try {
    const { settings, path, warnings, isNew: fresh } = await api.settingsGet();
    isNew = fresh;
    if (warnings.length) {
      notify(tp("notice.settingsFieldsUnreadable", warnings.length, { fields: joinList(warnings) }), "bad");
    }
    state.settings = settings;
    await syncUiLocale(settings.ui.locale);
    state.settingsPath = path;
    state.mode = settings.mode;
    fillForm(settings);
    fillCombo(settings);
    renderSettingsPath();
    await pushOverlayStyle();
  } catch (e) {
    reportError("error.readSettings", e);
  }
  try {
    state.devices = await api.devicesList();
  } catch (e) {
    reportError("error.listDevices", e);
  }
  renderModeNotes();
  renderDevices();
  renderStatus();
  if (state.page === "models") void loadModels();
  // Runs on every engine (re)start, e.g. after the GPU runtime install restarts it.
  if (firstRunGate.claim({ isNew, userChoseModels: state.userChoseModels })) void firstRunFlow();
  else void checkRecommendation();
}

function onBridge(status: BridgeStatus): void {
  const wasReady = engineReady();
  state.bridge = status;
  if (status.phase !== "ready") {
    // A restarted engine has no session and no downloads in flight.
    state.sessionState = "idle";
    for (const [id, d] of state.downloads) if (d.state === "downloading") state.downloads.delete(id);
  }
  renderStatus();
  renderCheck();
  if (status.phase === "ready" && !wasReady) void loadEngineData();
  if (status.phase === "failed") {
    notify(t("engine.failedNotice", { reason: status.detail ?? t("common.unknownCause") }), "bad", [
      { label: t("engine.restart"), run: () => void restartEngine() },
    ]);
  }
}

async function restartEngine(): Promise<void> {
  el.restart.disabled = true;
  try {
    onBridge(await api.restartEngine());
  } catch (e) {
    reportError("error.restartEngine", e);
  } finally {
    el.restart.disabled = false;
  }
}

// ---------------------------------------------------------------- updates

function updateUi(): UpdateUi {
  let notice: HTMLElement | null = null;
  return {
    offer(version, _notes, install) {
      notice = notify(t("update.available", { version }), "update", [
        {
          label: t("update.install"),
          run: () => {
            notice?.remove();
            notice = notify(t("update.downloading"), "update");
            void install();
          },
        },
      ]);
    },
    progress(pct) {
      const p = notice?.querySelector("p");
      if (p) p.textContent = pct === null ? t("update.downloading") : t("update.progress", { percent: pct });
    },
    installed(version, restart) {
      notice?.remove();
      notice = notify(t("update.installed", { version }), "update", [
        { label: t("update.restartNow"), run: () => void restart() },
      ]);
    },
    failed(message) {
      notice?.remove();
      notice = notify(message, "bad");
      el.updateStatus.textContent = message;
    },
  };
}

async function manualUpdateCheck(): Promise<void> {
  el.checkUpdate.disabled = true;
  const checking = t("update.checking");
  el.updateStatus.textContent = checking;
  try {
    const version = await checkForUpdate(updateUi(), true);
    if (version) el.updateStatus.textContent = t("update.found", { version });
    // A failure already replaced the text (UpdateUi.failed); keep that message.
    else if (el.updateStatus.textContent === checking) el.updateStatus.textContent = t("update.latest");
  } finally {
    el.checkUpdate.disabled = false;
  }
}

// ---------------------------------------------------------------- wiring

function wire(): void {
  for (const button of el.modes) {
    button.addEventListener("click", () => void setMode(button.dataset.mode as Mode));
  }
  el.device.addEventListener("change", () => void setDevice(el.device.value === "" ? null : el.device.value));
  el.start.addEventListener("click", () => void toggleSession());
  el.restart.addEventListener("click", () => void restartEngine());
  for (const item of el.navItems) {
    item.addEventListener("click", () => showPage(item.dataset.page as Page));
  }
  el.overlayVisible.addEventListener("change", () => void setOverlayVisible(el.overlayVisible.checked));
  el.overlayClick.addEventListener("change", async () => {
    try {
      state.overlay = await api.overlaySetClickThrough(el.overlayClick.checked);
    } catch (e) {
      reportError("error.toggleClickThrough", e);
    }
    renderOverlay();
  });
  el.clear.addEventListener("click", () => {
    state.subtitles = emptySubtitles(state.subtitles.sessionId);
    renderTranscript();
  });
  el.modelsRefresh.addEventListener("click", () => void loadModels().then(checkRecommendation));
  el.comboForm.addEventListener("input", () => {
    state.comboDirty = true;
    el.comboStatus.textContent = "";
    scheduleCheck();
  });
  el.comboForm.addEventListener("submit", (e) => {
    e.preventDefault();
    void submitCombo();
  });
  el.comboReset.addEventListener("click", () => {
    if (!state.settings) return;
    fillCombo(state.settings, true);
    scheduleCheck(0);
  });
  el.settingsForm.addEventListener("input", () => {
    el.settingsStatus.textContent = "";
    renderPreview();
  });
  el.settingsForm.addEventListener("submit", (e) => {
    e.preventDefault();
    void submitSettings();
  });
  el.openAppLicenses.addEventListener("click", () => void openLicenses("app"));
  el.openEngineLicenses.addEventListener("click", () => void openLicenses("engine"));
  el.checkUpdate.addEventListener("click", () => void manualUpdateCheck());
  el.uiLocale.addEventListener("change", () => {
    const v = el.uiLocale.value;
    if (v === "system" || isUiLocale(v)) void chooseUiLocale(v);
  });
}

async function subscribe(): Promise<void> {
  await Promise.all([
    events.bridge(onBridge),
    events.status((s) => {
      if (s.session_id !== null && state.sessionId !== null && s.session_id < state.sessionId) return;
      if (s.session_id !== null) state.sessionId = s.session_id;
      state.sessionState = s.state;
      if (s.state === "error" && s.code === "model_not_downloaded") {
        // Offer exactly the missing models; fall back to the generic message.
        void offerMissingModels().then((found) => {
          if (!found) notify(describeStatusError(s.code, s.detail), "bad");
        });
      } else if (s.state === "error") {
        notify(describeStatusError(s.code, s.detail), "bad");
      }
      renderStatus();
    }),
    events.subtitle((ev) => {
      state.subtitles = applySubtitle(state.subtitles, ev, TRANSCRIPT_MAX_LINES);
      renderTranscript();
    }),
    events.download((p) => {
      state.downloads.set(p.model, p);
      if (p.state === "done") {
        state.downloads.delete(p.model);
        if (p.model === GPU_RUNTIME_ID) {
          onGpuRuntimeInstalled();
        } else {
          // Mark it now so the missing-models notice does not flash back to
          // "still needed" while the list is being reloaded.
          const listed = state.models?.asr.find((m) => m.id === p.model);
          if (listed) listed.downloaded = true;
          notify(t("download.done", { model: p.model }));
          void loadModels();
        }
      } else if (p.state === "error") {
        const what = p.model === GPU_RUNTIME_ID ? t("gpu.name") : p.model;
        notify(t("download.failed", { what, error: p.error ?? t("common.unknownReason") }), "bad");
      }
      if (state.page === "models") renderModels();
      renderMissingNotice();
    }),
    events.log((l) => {
      if (l.level === "warning" || l.level === "error") {
        console.warn(`engine ${l.level}: ${l.message}`);
        notify(t("engine.log", { message: l.message }), l.level === "error" ? "bad" : "info");
      }
    }),
    events.overlay((o) => {
      state.overlay = o;
      renderOverlay();
    }),
  ]);
}

async function init(): Promise<void> {
  // Before anything is rendered: the last choice (cached) or the OS language,
  // so the window never shows the wrong language while the engine starts.
  onLocaleChange(onLocaleChanged);
  applyUiLocale(readCachedUiLocale());
  wire();
  renderStatus();
  renderTranscript();
  renderHardware();
  renderModels();
  renderCheck();
  try {
    el.aboutVersion.textContent = `kikitori ${await getVersion()}`;
  } catch (e) {
    console.error("getVersion failed:", e);
  }
  try {
    state.about = await api.about();
  } catch (e) {
    console.error("app_about failed:", e);
  }
  renderAbout();
  // Subscribe before asking for the current state so no transition is missed.
  await subscribe();
  try {
    state.overlay = await api.overlayState();
    renderOverlay();
  } catch (e) {
    reportError("error.readOverlayState", e);
  }
  try {
    onBridge(await api.bridgeStatus());
  } catch (e) {
    reportError("error.readBridge", e);
  }
  if (import.meta.env.PROD) {
    // Silent on failure: being offline at startup is normal.
    void checkForUpdate(updateUi(), false);
  }
}

init().catch((e: unknown) => {
  console.error("main window failed to initialise", e);
  notify(t("notice.initFailed", { reason: describeError(e) }), "bad");
});
