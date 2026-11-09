/**
 * Main window controller.
 *
 * Role: wires the static markup in index.html to the engine (through `lib/api`)
 * and keeps it in sync with engine/bridge/overlay events. Pure decisions
 * (subtitle ordering, validation, wording, combination-check rules) live in
 * `lib/`; this file only holds UI state and renders it. Every text comes from
 * the i18n dictionary, and every render function can run again after the
 * interface language changes (`onLocaleChanged`). The language section, the
 * downloads before starting and the "顯示翻譯" switch follow the same split
 * (`lib/languages.ts`, `lib/start-downloads.ts`, `lib/translation-toggle.ts`),
 * as do transcript export, the translation-server guide and the diagnostics
 * report (`lib/export.ts`, `lib/translation-guide.ts`, `lib/diagnostics.ts`),
 * the glossary editor, the status-bar metrics, model deletion and the
 * end-of-session auto-save wait (`lib/glossary.ts`, `lib/metrics.ts`,
 * `lib/model-delete.ts`, `lib/session-end.ts`).
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
import { RecentLog, buildDiagnostics } from "../lib/diagnostics";
import { byId, h, replace, setAttributeIfChanged, setIfChanged } from "../lib/dom";
import {
  EXPORT_CONTENTS,
  EXPORT_FORMATS,
  EXPORT_MAX_LINES,
  exportTranscript,
  hasExportableLines,
  interpretNamer,
  transcriptFileName,
  type ExportContent,
  type ExportFormat,
  type LineNamer,
} from "../lib/export";
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
import {
  AUTO_LANGUAGE,
  filterLanguageOptions,
  languageFormFromSettings,
  languageOptions,
  languagesSave,
  previewMatches,
  previewNotes,
  previewRequest,
  previewRows,
  previewSummary,
  validateLanguageForm,
  type LanguageErrors,
  type LanguageField,
  type LanguageForm,
} from "../lib/languages";
import {
  GLOSSARY_MAX_CSV_BYTES,
  GLOSSARY_MAX_SOURCE_CHARS,
  glossaryErrorForEnginePath,
  glossaryFileName,
  glossaryPatch,
  glossaryToCsv,
  hasGlossaryErrors,
  mergeGlossary,
  parseGlossaryCsv,
  validateGlossary,
  type GlossaryErrorTarget,
  type GlossaryField,
  type GlossaryRowErrors,
} from "../lib/glossary";
import { formatEngineMessage, type EngineMessage } from "../lib/messages";
import {
  applyMetrics,
  dismissMetricsWarning,
  emptyMetrics,
  metricsSessionState,
  meterTransform,
  metricsView,
  noAudioDueAt,
  type MetricsState,
  type MetricsWarning,
} from "../lib/metrics";
import { deleteBlockedText, deleteConfirmText, deleteOffer } from "../lib/model-delete";
import {
  FURIGANA_DICTIONARY_ID,
  afterFuriganaDownload,
  furiganaBlockView,
  furiganaEnableAction,
  furiganaSwitchView,
  isFuriganaDownload,
  type StatusLine,
} from "../lib/furigana";
import { rubyKey, segmentNodes } from "../lib/ruby";
import {
  FirstRunGate,
  dismissedWith,
  missingModels,
  missingModelsView,
  missingNoticeMode,
  missingShownByStartPanel,
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
  obsOutputFromForm,
  settingsPatchFromForm,
  uiLocalePatch,
  validateCombinationForm,
  validateSettingsForm,
  type CombinationErrors,
  type CombinationForm,
  type FormErrors,
  type SettingsForm,
} from "../lib/settings";
import { SessionEndSave } from "../lib/session-end";
import { StartDownloads, startDownloadsView, type StartItemRequest } from "../lib/start-downloads";
import { correctionsToText, parseCorrectionsText } from "../lib/corrections";
import { sayAgainPhrase, voiceColor, voiceName } from "../lib/speaker";
import {
  SPEAKER_CHOICES,
  SPEAKER_MODEL_ID,
  afterSpeakerDownload,
  isSpeakerDownload,
  parseChoice,
  speakerBlockView,
  speakerChoiceAction,
  speakerSelectView,
  speakersPatch,
  type SpeakerChoice,
} from "../lib/speaker-model";
import { MAX_SPEAKER_NAME_CHARS } from "../lib/validate";
import { applySubtitle, emptySubtitles, lineDisplay, type SubtitleLine, type SubtitleState } from "../lib/subtitles";
import { isTranslationServerLog, translationGuide, type GuideView } from "../lib/translation-guide";
import { CUSTOM_TRANSLATION_MODEL, translationPicker } from "../lib/translation-picker";
import { setShowTranslation } from "../lib/translation-toggle";
import type {
  AboutInfo,
  AppliedRecommendation,
  AsrChoice,
  AsrModel,
  BridgeStatus,
  DeviceInfo,
  DownloadProgressEvent,
  EngineState,
  GlossaryEntry,
  HardwareReport,
  LanguageInfo,
  LanguagePreview,
  LicenseFolder,
  MetricsEvent,
  Mode,
  ModelsList,
  ObsStatus,
  OutputFolder,
  OutputInfo,
  OverlaySnapshot,
  RecommendationPreview,
  Settings,
  ShortcutAction,
} from "../lib/types";
import { autoDismiss } from "../lib/auto-dismiss";
import { SettingsRevision } from "../lib/settings-revision";
import { updateNotesBlocks, type NotesBlock } from "../lib/update-notes";
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
  /** `languages.list` (null until the engine answered). */
  languages: LanguageInfo[] | null;
  /** The language section's current choice (selects may be filtered, so it lives here). */
  langForm: LanguageForm | null;
  /** The language section differs from the saved settings; reloads keep the edits. */
  langDirty: boolean;
  /** Text typed into each language menu's filter. */
  langFilters: Record<LanguageField, string>;
  /** What the language section shows below the menus. */
  langPreview: LanguagePreviewView;
  /** The "顯示翻譯" switch is being applied. */
  translationBusy: boolean;
  /** Every line of the session for export (the visible log keeps only the last 300). */
  exportLog: SubtitleState;
  /** When the current (or last) session started, and in which mode: names exported files. */
  sessionStartedAt: Date | null;
  sessionMode: Mode;
  /** Session whose transcript was auto-saved (once per session). */
  autoSavedSession: string | null;
  /** `output_info`: where transcripts and the OBS text go. */
  output: OutputInfo | null;
  /** Last `obs_set_output` answer. */
  obs: ObsStatus | null;
  /** The translation-server guide is shown (its notice appears once per appearance). */
  guideShown: boolean;
  appVersion: string | null;
  /** Level meter, latency and warnings of the running session (lib/metrics.ts). */
  metrics: MetricsState;
  /** The glossary editor (settings page). */
  glossary: GlossaryEditor;
  /** ASR models with a `models.delete` in flight. */
  deleting: Set<string>;
  /** The model whose delete confirmation is open (one at a time). */
  confirmDelete: string | null;
  /** Furigana (lib/furigana.ts): a `session.set_furigana` is in flight. */
  furiganaBusy: boolean;
  /** The user turned furigana on and the dictionary is downloading for it. */
  furiganaPendingEnable: boolean;
  /** Last `download_progress` of the dictionary (cleared when it is done). */
  furiganaDownload: DownloadProgressEvent | null;
  furiganaDeleting: boolean;
  furiganaConfirmDelete: boolean;
  /** Speaker labels (lib/speaker-model.ts): a `settings.set` of `speakers` is in flight. */
  speakersBusy: boolean;
  /** The choice waiting for the speaker model to finish downloading. */
  speakersPending: SpeakerChoice | null;
  /** Last `download_progress` of the speaker model (cleared when it is done). */
  speakerDownload: DownloadProgressEvent | null;
  speakerDeleting: boolean;
  speakerConfirmDelete: boolean;
  /** Names the user gave the speaker labels this session; index i names voice i + 1. */
  speakerNames: string[];
}

/** One table row; `id` keeps the row's elements when others are added or removed. */
interface GlossaryRow {
  id: number;
  entry: GlossaryEntry;
}

interface GlossaryEditor {
  builtin: boolean;
  rows: GlossaryRow[];
  /** The table differs from the saved settings; reloads keep the edits. */
  dirty: boolean;
  /** The engine's rejection of the last save, until the user edits that place. */
  engineError: { target: GlossaryErrorTarget; message: string } | null;
  /** A `settings.set` is in flight (Save stays disabled). */
  saving: boolean;
  /** Counts edits, so a save does not overwrite rows changed while it was in flight. */
  revision: number;
  /** The ASR correction rules as typed in their text box (G4; lib/corrections.ts). */
  corrections: string;
}

/** State of the recommended-combination preview under the language menus. */
type LanguagePreviewView =
  | { state: "unavailable" }
  | { state: "local_errors" }
  | { state: "checking" }
  | { state: "ready"; preview: LanguagePreview }
  | { state: "error"; message: string };

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
  languages: null,
  langForm: null,
  langDirty: false,
  langFilters: { source: "", target: "", partner: "" },
  langPreview: { state: "unavailable" },
  translationBusy: false,
  exportLog: emptySubtitles(),
  sessionStartedAt: null,
  sessionMode: "media",
  autoSavedSession: null,
  output: null,
  obs: null,
  guideShown: false,
  appVersion: null,
  metrics: emptyMetrics(),
  glossary: { builtin: true, rows: [], dirty: false, engineError: null, saving: false, revision: 0, corrections: "" },
  deleting: new Set(),
  confirmDelete: null,
  furiganaBusy: false,
  furiganaPendingEnable: false,
  furiganaDownload: null,
  furiganaDeleting: false,
  furiganaConfirmDelete: false,
  speakersBusy: false,
  speakersPending: null,
  speakerDownload: null,
  speakerDeleting: false,
  speakerConfirmDelete: false,
  speakerNames: [],
};

/** Last engine messages for "複製診斷資訊". */
const recentLog = new RecentLog();
const GUIDE_NOTICE = "translation-guide";
/** An engine warning about the translation server; the guide replaces it when it appears. */
const TRANSLATION_LOG_NOTICE = "engine-translation-log";
const EXPORT_NOTICE = "export";

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
  showTranslation: byId<HTMLInputElement>("show-translation"),
  settingsShowTranslation: byId<HTMLInputElement>("f-show-translation"),
  furiganaSwitch: byId<HTMLInputElement>("f-furigana"),
  furiganaStatus: byId("furigana-status"),
  furiganaHelp: byId("furigana-help"),
  furiganaBlock: byId("furigana-dictionary"),
  speakersBlock: byId("speakers-block"),
  speakersSelect: byId<HTMLSelectElement>("f-speakers"),
  speakersHelp: byId("speakers-help"),
  speakersStatus: byId("speakers-status"),
  speakerBlock: byId("speaker-model"),
  voiceRename: byId<HTMLFormElement>("voice-rename"),
  voiceRenameInput: byId<HTMLInputElement>("voice-rename-input"),
  startDownloads: byId("start-downloads"),
  startDownloadsItems: byId("startdl-items"),
  startDownloadsMessage: byId("startdl-message"),
  startDownloadsRetry: byId<HTMLButtonElement>("startdl-retry"),
  startDownloadsCancel: byId<HTMLButtonElement>("startdl-cancel"),
  languagesForm: byId<HTMLFormElement>("languages-form"),
  languagesModeNote: byId("languages-mode-note"),
  langFields: {
    source: byId("field-source"),
    target: byId("field-target"),
    partner: byId("field-partner"),
  } as Record<LanguageField, HTMLElement>,
  langSelects: {
    source: byId<HTMLSelectElement>("f-source"),
    target: byId<HTMLSelectElement>("f-target"),
    partner: byId<HTMLSelectElement>("f-partner"),
  } as Record<LanguageField, HTMLSelectElement>,
  langFilters: {
    source: byId<HTMLInputElement>("f-source-filter"),
    target: byId<HTMLInputElement>("f-target-filter"),
    partner: byId<HTMLInputElement>("f-partner-filter"),
  } as Record<LanguageField, HTMLInputElement>,
  labelTarget: byId("label-target"),
  helpTarget: byId("help-target"),
  languagePreview: byId("language-preview"),
  applyRecommended: byId<HTMLInputElement>("f-apply-recommended"),
  languagesSave: byId<HTMLButtonElement>("languages-save"),
  languagesStatus: byId("languages-status"),
  shortcutNote: byId("shortcut-note"),
  resetOverlay: byId<HTMLButtonElement>("reset-overlay"),
  oOrigScale: byId("o-orig-scale"),
  sessionInfo: byId<HTMLTextAreaElement>("session-info"),
  sessionInfoBlock: byId<HTMLDetailsElement>("session-info-block"),
  sessionInfoStatus: byId("session-info-status"),
  clear: byId<HTMLButtonElement>("clear-transcript"),
  hardware: byId("hardware"),
  hardwareErrors: byId("hardware-errors"),
  recommendation: byId("recommendation"),
  gpuRuntime: byId("gpu-runtime"),
  asrModels: byId("asr-models"),
  modelsRefresh: byId<HTMLButtonElement>("models-refresh"),
  trSource: byId("tr-source"),
  trPresets: byId("tr-presets"),
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
  exportMenu: byId<HTMLDetailsElement>("export-menu"),
  exportContent: byId<HTMLSelectElement>("export-content"),
  exportFormats: byId("export-formats"),
  exportHelp: byId("export-help"),
  transcriptsPath: byId("transcripts-path"),
  openTranscripts: byId<HTMLButtonElement>("open-transcripts"),
  obsPath: byId("obs-path"),
  openObs: byId<HTMLButtonElement>("open-obs"),
  guide: byId("translation-guide"),
  guideText: byId("translation-guide-text"),
  guideInstall: byId<HTMLButtonElement>("guide-install"),
  guideOriginal: byId<HTMLButtonElement>("guide-original"),
  guideRecheck: byId<HTMLButtonElement>("guide-recheck"),
  copyDiagnostics: byId<HTMLButtonElement>("copy-diagnostics"),
  diagnosticsStatus: byId("diagnostics-status"),
  diagnosticsText: byId<HTMLTextAreaElement>("diagnostics-text"),
  metrics: byId("metrics"),
  levelMeter: byId("level-meter"),
  levelFill: byId("level-fill"),
  latency: byId("latency"),
  glossaryForm: byId<HTMLFormElement>("glossary-form"),
  glossaryBuiltin: byId<HTMLInputElement>("f-glossary-builtin"),
  glossaryRows: byId("glossary-rows"),
  glossaryEmpty: byId("glossary-empty"),
  glossaryListError: byId("glossary-list-error"),
  glossaryAdd: byId<HTMLButtonElement>("glossary-add"),
  glossaryImport: byId<HTMLButtonElement>("glossary-import"),
  glossaryFile: byId<HTMLInputElement>("glossary-file"),
  glossaryExport: byId<HTMLButtonElement>("glossary-export"),
  glossarySave: byId<HTMLButtonElement>("glossary-save"),
  glossaryReset: byId<HTMLButtonElement>("glossary-reset"),
  glossaryStatus: byId("glossary-status"),
  correctionsInput: byId<HTMLTextAreaElement>("f-corrections"),
  correctionsError: byId("corrections-error"),
  addToGlossary: byId<HTMLButtonElement>("add-to-glossary"),
};

// ---------------------------------------------------------------- settings answers

const settingsRevision = new SettingsRevision();

/**
 * Puts an engine answer's settings on screen unless a newer one is already there
 * (lib/settings-revision.ts). Returns the settings now on screen: forms filled after
 * a save use it, so a late answer does not put old values back into them either.
 */
function adoptSettings(settings: Settings, revision: number | null): Settings {
  if (settingsRevision.acceptIfNotOlder(revision)) {
    state.settings = settings;
    return settings;
  }
  console.info(`settings revision ${revision} arrived after a newer one; kept the newer settings`);
  return state.settings ?? settings;
}

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
  /** Extra content under the text (the update notes). */
  detail?: Node;
  /** A plain confirmation: closes itself after a few seconds (lib/auto-dismiss.ts). */
  autoDismiss?: boolean;
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
  // The text stays the notice's first <p>: progress updates find it with querySelector("p").
  const body = options.detail ? h("div", { class: "notice-body" }, h("p", {}, text), options.detail) : h("p", {}, text);
  const buttons = h("div", { class: "notice-actions" });
  for (const action of actions) {
    const b = h("button", { type: "button", class: "ghost" }, action.label);
    b.addEventListener("click", action.run);
    buttons.appendChild(b);
  }
  const close = h("button", { type: "button", class: "ghost", "aria-label": t("notice.closeLabel") }, t("notice.close"));
  const timer = options.autoDismiss
    ? autoDismiss({
        isBeingRead: () => notice.isConnected && (notice.matches(":hover") || notice.contains(document.activeElement)),
        dismiss: () => notice.remove(),
      })
    : undefined;
  if (timer) {
    // A full delay from the moment the reader lets go, not whatever is left of the current check.
    notice.addEventListener("pointerleave", () => timer.restart());
    notice.addEventListener("focusout", (e) => {
      if (!(e.relatedTarget instanceof Node && notice.contains(e.relatedTarget))) timer.restart();
    });
  }
  close.addEventListener("click", () => {
    timer?.cancel();
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

/** Auto-save waits for the engine's "idle" (or 2 s) so a final sent after Stop is included. */
const sessionEnd = new SessionEndSave(() => void autoSaveTranscript(), {
  set: (run, ms) => window.setTimeout(run, ms),
  clear: (handle) => window.clearTimeout(handle),
});

/**
 * Every change of the session state goes through here, so the end of a
 * session (stop, engine error, engine restart) is noticed in one place.
 * `fromEngine`: the engine itself reported the state (or is gone), so no
 * subtitle of the ended session can still arrive.
 */
function setSessionState(next: EngineState, fromEngine: boolean): void {
  const wasActive = sessionActive();
  state.sessionState = next;
  if (wasActive && !sessionActive()) sessionEnd.ended(fromEngine);
  else if (fromEngine && !sessionActive()) sessionEnd.engineIdle();
  state.metrics = metricsSessionState(state.metrics, next, state.sessionId);
  renderMetrics();
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
  // While models are downloaded for a start, the start happens by itself.
  const waiting = startFlow.active;
  el.start.disabled = !ready || state.busy || waiting;
  el.start.dataset.state = state.busy || waiting ? "busy" : sessionActive() ? "running" : "idle";
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

// ---------------------------------------------------------------- status bar metrics

const METRICS_NOTICES: Readonly<Record<MetricsWarning, string>> = {
  backlog: "metrics-backlog",
  no_audio: "metrics-no-audio",
  bgm: "metrics-bgm",
};
const METRICS_TEXT: Readonly<Record<MetricsWarning, StringKey>> = {
  backlog: "metrics.backlog",
  no_audio: "metrics.noAudio",
  bgm: "metrics.bgm",
};
/**
 * Warnings whose notice was shown for the current occurrence. A notice pushed
 * out by newer ones (the list keeps three) is not re-added every second.
 */
const shownMetricsWarnings = new Set<MetricsWarning>();
let noAudioTimer: number | undefined;

function onMetrics(ev: MetricsEvent): void {
  state.metrics = applyMetrics(state.metrics, ev, Date.now());
  renderMetrics();
}

/**
 * Meter, median latency and the two warnings (lib/metrics.ts). The no-audio
 * hint is re-checked by a timer too: metrics may stop arriving.
 */
function renderMetrics(): void {
  const now = Date.now();
  const view = metricsView(state.metrics, now);
  el.metrics.hidden = !view.visible;
  const level = view.level ?? 0;
  // Called every second while a session runs: write only what changed (see setIfChanged).
  setIfChanged(el.levelFill.style, "transform", meterTransform(view.level));
  setIfChanged(el.levelMeter.dataset, "quiet", String(view.level === null || level === 0));
  setAttributeIfChanged(el.levelMeter, "aria-valuenow", String(Math.round(view.levelDbfs ?? -120)));
  setAttributeIfChanged(
    el.levelMeter,
    "aria-valuetext",
    view.levelDbfs === null ? t("metrics.levelUnknown") : t("metrics.levelValue", { db: Math.round(view.levelDbfs) }),
  );
  setIfChanged(el.levelMeter, "title", t("metrics.levelLabel"));
  setIfChanged(
    el.latency,
    "textContent",
    view.latencyMs === null ? t("metrics.latencyUnknown") : t("metrics.latency", { seconds: (view.latencyMs / 1000).toFixed(1) }),
  );
  setIfChanged(el.latency, "title", t("metrics.latencyHelp"));

  for (const warning of ["backlog", "no_audio", "bgm"] as const) {
    const key = METRICS_NOTICES[warning];
    const show = view.warnings.includes(warning);
    if (!show) {
      noticeFor(key)?.remove();
      shownMetricsWarnings.delete(warning);
      continue;
    }
    if (shownMetricsWarnings.has(warning)) continue;
    shownMetricsWarnings.add(warning);
    const actions: NoticeAction[] =
      warning === "backlog" ? [{ label: t("metrics.openModels"), run: () => showPage("models") }] : [];
    notify(t(METRICS_TEXT[warning]), "info", actions, {
      key,
      onClose: () => {
        state.metrics = dismissMetricsWarning(state.metrics, warning);
      },
    });
  }

  window.clearTimeout(noAudioTimer);
  const due = noAudioDueAt(state.metrics, now);
  if (due !== null) noAudioTimer = window.setTimeout(renderMetrics, due - now + 50);
}

function renderModeNotes(): void {
  const s = state.settings ?? DEFAULT_SETTINGS;
  const original = !s.translation.enabled;
  const target = languageName(s.target_language);
  if (el.modeNotes.media) {
    const auto = s.source_language === AUTO_LANGUAGE;
    const source = auto ? t("language.auto") : languageName(s.source_language);
    el.modeNotes.media.textContent = original
      ? t("mode.media.noteOriginal", { source })
      : auto
        ? t("mode.media.noteAuto", { target })
        : t("mode.media.note", { source, target });
  }
  if (el.modeNotes.interpret) {
    const params = { own: target, partner: languageName(s.partner_language) };
    el.modeNotes.interpret.textContent = original
      ? t("mode.interpret.noteOriginal", params)
      : t("mode.interpret.note", params);
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
  renderSpeakerSelect();
  // The language section shows the languages of the selected mode.
  if (state.langForm) state.langForm = { ...state.langForm, mode };
  renderLanguages();
  scheduleLanguagePreview(0);
  const deviceId = mode === "media" ? state.settings?.media_device_id : state.settings?.interpret_device_id;
  try {
    const { settings, revision } = await api.settingsSet(devicePatch(mode, deviceId ?? null));
    adoptSettings(settings, revision);
  } catch (e) {
    reportError("error.saveMode", e);
  }
}

async function setDevice(deviceId: string | null): Promise<void> {
  try {
    const { settings, revision } = await api.settingsSet(devicePatch(state.mode, deviceId));
    adoptSettings(settings, revision);
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
      setSessionState("idle", false);
    } else {
      startFlow.reset();
      // Chosen speech models that are not downloaded yet (or still downloading):
      // fetch them first, then start by itself. Translation models are checked by
      // the engine (only it knows whether the server is Ollama) and reported as
      // translation_model_missing, which lands in the same flow.
      const missing = asrStartItems();
      if (missing.length) {
        startFlow.begin(missing);
        return;
      }
      await startSession();
    }
  } catch (e) {
    reportError(sessionActive() ? "error.stop" : "error.start", e);
  } finally {
    state.busy = false;
    renderStatus();
  }
}

// ---------------------------------------------------------------- stream info (G5)

/** Saves the stream info this long after the last keystroke. */
const SESSION_INFO_SAVE_MS = 800;
let sessionInfoTimer: number | undefined;
/** The text box differs from the saved settings. */
let sessionInfoDirty = false;

/** Shows the saved stream info, unless the user is typing in the box. */
function fillSessionInfo(settings: Settings): void {
  if (sessionInfoDirty) return;
  el.sessionInfo.value = settings.translation.session_info;
  // Open the block when there is something in it, so it is not forgotten between streams.
  if (settings.translation.session_info !== "") el.sessionInfoBlock.open = true;
}

function onSessionInfoInput(): void {
  sessionInfoDirty = true;
  el.sessionInfoStatus.textContent = "";
  window.clearTimeout(sessionInfoTimer);
  sessionInfoTimer = window.setTimeout(() => void saveSessionInfo(), SESSION_INFO_SAVE_MS);
}

/** Saves the box now if it has unsaved text (also before a session starts, so it takes effect). */
async function saveSessionInfo(): Promise<void> {
  window.clearTimeout(sessionInfoTimer);
  if (!sessionInfoDirty || !engineReady()) return;
  const value = el.sessionInfo.value;
  try {
    const { settings, revision } = await api.settingsSet({ translation: { session_info: value } });
    adoptSettings(settings, revision);
    // Typed more while saving: keep the newer text and save again later.
    if (el.sessionInfo.value === value) sessionInfoDirty = false;
    el.sessionInfoStatus.textContent = sessionActive() ? t("rail.sessionInfoSavedNext") : t("rail.sessionInfoSaved");
  } catch (e) {
    reportError("error.saveSessionInfo", e);
  }
}

/** `session.start` with the current mode and device; resets the transcript. */
async function startSession(): Promise<void> {
  await saveSessionInfo();
  const deviceId = el.device.value === "" ? null : el.device.value;
  // A previous session still waiting for its last final is saved before its log is reset.
  sessionEnd.flush();
  const sessionId = await api.sessionStart(state.mode, deviceId);
  state.sessionId = sessionId;
  state.subtitles = emptySubtitles(sessionId);
  state.exportLog = emptySubtitles(sessionId);
  state.sessionStartedAt = new Date();
  state.sessionMode = state.mode;
  // Speaker labels are numbered afresh each session, so last session's names would be wrong.
  if (state.speakerNames.length) void setSpeakerNames([]);
  setSessionState("loading", false);
  renderTranscript();
  if (state.overlay && !state.overlay.visible) {
    await setOverlayVisible(true);
  }
}

/** The chosen ASR models the latest model list says are not downloaded. */
function asrStartItems(): StartItemRequest[] {
  if (!state.settings || !state.models) return [];
  return missingModels(state.settings.asr, state.models.asr).map((m) => ({
    kind: "asr",
    model: m.id,
    sizeBytes: m.size_bytes,
  }));
}

// ---------------------------------------------------------------- downloads before starting

const startFlow = new StartDownloads({
  downloadAsr: (model) => api.modelsDownload(model),
  pullTranslation: (model) => api.modelsPullTranslation(model),
  start: async () => {
    // Reported here; the flow only needs to know the round is over.
    try {
      await startSession();
    } catch (e) {
      reportError("error.start", e);
    }
    renderStatus();
  },
  errorCode: engineCode,
  describe: describeError,
  onChange: () => {
    renderStartDownloads();
    renderStatus();
    renderTranslationGuide();
    renderMissingNotice();
  },
});

/** Approximate size of a translation preset (weights), for the progress before Ollama reports one. */
function translationSize(model: string): number | null {
  const gb = state.models?.translation.models.find((m) => m.name === model)?.approx_vram_gb;
  return gb === undefined || gb === null ? null : Math.round(gb * 1e9);
}

function renderStartDownloads(): void {
  const view = startDownloadsView(startFlow.state, state.settings?.translation.base_url ?? "");
  el.startDownloads.hidden = view === null;
  if (!view) return;
  el.startDownloads.dataset.tone = view.tone;
  replace(
    el.startDownloadsItems,
    ...view.items.map((item) =>
      h(
        "li",
        { "data-tone": item.tone },
        h("span", { class: "model-id" }, item.label),
        item.percent === null ? h("span") : h("progress", { max: 100, value: item.percent, "aria-label": item.label }),
        h("span", {}, item.text),
      ),
    ),
  );
  el.startDownloadsMessage.textContent = view.message;
  el.startDownloadsRetry.hidden = !view.canRetry;
  el.startDownloadsRetry.disabled = !engineReady();
  el.startDownloadsCancel.hidden = !view.canCancel;
}

// ---------------------------------------------------------------- transcript

const lineElements = new Map<number, HTMLLIElement>();

function showsTranslation(): boolean {
  return state.settings?.translation.enabled ?? true;
}

// Called for every row on every subtitle event (several per second): only values that
// changed are written, or each redraw would replace hundreds of text nodes (renderer memory
// grew ~8 MiB/min in the v0.2.0 GUI soak).
function updateLineElement(li: HTMLLIElement, line: SubtitleLine): void {
  const shown = lineDisplay(line, showsTranslation());
  li.classList.toggle("is-partial", !line.isFinal);
  li.classList.toggle("is-original-only", shown.originalOnly);
  const [time, lang, text] = li.children as unknown as [HTMLElement, HTMLElement, HTMLElement];
  setIfChanged(time, "textContent", formatTimecode(line.startSample));
  setIfChanged(lang, "textContent", languageShort(line.sourceLanguage));
  setIfChanged(
    lang,
    "title",
    t("transcript.langTitle", {
      source: languageName(line.sourceLanguage),
      target: languageName(line.targetLanguage),
    }),
  );
  const [voice, orig, tr] = text.children as unknown as [HTMLButtonElement, HTMLElement, HTMLElement];
  // Media mode speaker labels (speaker-labels.md): on every row, like the interpret chip; click to rename.
  const voiceText = line.voice === null ? "" : speakerName(line.voice);
  voice.hidden = line.voice === null;
  setIfChanged(voice, "textContent", voiceText);
  const voiceKey = line.voice === null ? "" : String(line.voice);
  if ((voice.dataset.voice ?? "") !== voiceKey) voice.dataset.voice = voiceKey;
  const voiceClass = line.voice === null ? "utt-voice" : `utt-voice c${voiceColor(voiceText)}`;
  setIfChanged(voice, "className", voiceClass);
  setIfChanged(voice, "title", t("speaker.rename"));
  // Settled prefix at normal emphasis, the tail that may still change faded (F2).
  const [stable, unstable] = orig.children as unknown as [HTMLElement, HTMLElement];
  if (shown.originalRuby) {
    // Furigana: rebuilt only when the words, readings or the cut changed (same reason as setIfChanged).
    const key = rubyKey(shown.originalRuby);
    if (stable.dataset.ruby !== key) {
      replace(stable, ...segmentNodes(shown.originalRuby.stable));
      replace(unstable, ...segmentNodes(shown.originalRuby.unstable));
      stable.dataset.ruby = key;
    }
  } else {
    if (stable.dataset.ruby !== undefined) {
      // Was drawn with furigana: textContent below replaces the <ruby> children.
      delete stable.dataset.ruby;
      stable.textContent = null;
      unstable.textContent = null;
    }
    setIfChanged(stable, "textContent", shown.originalParts.stable);
    setIfChanged(unstable, "textContent", shown.originalParts.unstable);
  }
  setIfChanged(orig, "lang", htmlLang(line.sourceLanguage));
  // Interpret mode: who said it (G3). Every row is labelled: the log is scanned, not read in one go.
  const who = line.speaker ? t(line.speaker === "self" ? "speaker.self" : "speaker.partner") : "";
  if ((orig.dataset.who ?? "") !== who) {
    if (who) orig.dataset.who = who;
    else delete orig.dataset.who;
  }
  li.classList.toggle("is-self", line.speaker === "self");
  li.classList.toggle("is-partner", line.speaker === "partner");
  // Settled prefix of the translation and its tail that may still change (G1).
  const [trStable, trUnstable] = tr.children as unknown as [HTMLElement, HTMLElement];
  setIfChanged(trStable, "textContent", shown.translationParts.stable);
  setIfChanged(trUnstable, "textContent", shown.translationParts.unstable);
  tr.classList.toggle("is-empty", shown.translation === "");
  setIfChanged(tr, "lang", htmlLang(line.targetLanguage));
  // Shown by CSS when the translation is empty (not translated yet, or failed).
  const empty = line.isFinal ? t("transcript.noTranslation") : t("transcript.translating");
  if (tr.dataset.empty !== empty) tr.dataset.empty = empty;
}

function newLineElement(): HTMLLIElement {
  return h(
    "li",
    { class: "utt" },
    h("span", { class: "utt-time" }),
    h("span", { class: "utt-lang" }),
    // dir="auto": Arabic runs right to left, line by line.
    // The two spans carry no dir of their own: the paragraph's direction orders them.
    h(
      "div",
      { class: "utt-text" },
      h("button", { type: "button", class: "utt-voice", hidden: true }),
      h("p", { class: "utt-orig", dir: "auto" }, h("span", { class: "utt-stable" }), h("span", { class: "utt-unstable" })),
      h("p", { class: "utt-tr", dir: "auto" }, h("span", { class: "utt-stable" }), h("span", { class: "utt-unstable" })),
    ),
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
      // Lets a selection find its line again (G4 "add to glossary").
      li.dataset.id = String(line.utteranceId);
      lineElements.set(line.utteranceId, li);
    }
    updateLineElement(li, line);
    const expectedNext: ChildNode | null = previous ? previous.nextSibling : el.transcript.firstChild;
    if (expectedNext !== li) el.transcript.insertBefore(li, expectedNext);
    previous = li;
  }
  for (const [id, li] of lineElements) {
    if (!keep.has(id)) {
      // An open rename box goes with the row it sits in: keep what was typed and park the box.
      if (li.contains(el.voiceRename)) parkVoiceRename();
      li.remove();
      lineElements.delete(id);
    }
  }
  el.transcriptEmpty.hidden = state.subtitles.lines.length > 0;
  if (nearBottom && scroller) scroller.scrollTop = scroller.scrollHeight;
  renderExport();
}

// ---------------------------------------------------------------- transcript export

function exportFormatButtons(): HTMLButtonElement[] {
  return Array.from(el.exportFormats.querySelectorAll<HTMLButtonElement>("button[data-format]"));
}

function renderExport(): void {
  if (exportFormatButtons().length === 0) {
    // Format names are file types, not interface text.
    replace(
      el.exportFormats,
      ...EXPORT_FORMATS.map((format) => h("button", { type: "button", class: "ghost", "data-format": format }, format.toUpperCase())),
    );
    for (const button of exportFormatButtons()) {
      button.addEventListener("click", () => void exportAs(button.dataset.format as ExportFormat));
    }
  }
  const any = hasExportableLines(state.exportLog.lines);
  for (const button of exportFormatButtons()) button.disabled = !any;
  // Runs on every subtitle event (renderTranscript).
  setIfChanged(el.exportHelp, "textContent", any ? t("export.help") : t("export.empty"));
}

function exportContent(): ExportContent {
  const v = el.exportContent.value;
  return (EXPORT_CONTENTS as readonly string[]).includes(v) ? (v as ExportContent) : "both";
}

/** What one export writes: taken at once, so a new session starting mid-save cannot mix in. */
interface TranscriptSnapshot {
  lines: readonly SubtitleLine[];
  startedAt: Date;
  mode: Mode;
  speakerNames: readonly string[];
}

function transcriptSnapshot(): TranscriptSnapshot {
  return {
    lines: state.exportLog.lines,
    startedAt: state.sessionStartedAt ?? new Date(),
    mode: state.sessionMode,
    speakerNames: [...state.speakerNames],
  };
}

/** Writes one format through Rust; resolves to the saved path. */
async function saveTranscript(snap: TranscriptSnapshot, format: ExportFormat, content: ExportContent): Promise<string> {
  const name = transcriptFileName(snap.startedAt, snap.mode, format);
  // Who said each line: "You"/"Them" in interpret sessions (G3), the speaker labels in media sessions.
  const nameOf: LineNamer =
    snap.mode === "interpret"
      ? interpretNamer({ self: t("speaker.self"), partner: t("speaker.partner") })
      : (line) => (line.voice === null ? null : voiceName(line.voice, snap.speakerNames, speakerFallback));
  return api.transcriptSave(name, exportTranscript(snap.lines, format, content, nameOf));
}

function notifySaved(text: string): void {
  notify(text, "info", [{ label: t("export.openFolder"), run: () => void openOutputFolder("transcripts") }], {
    key: EXPORT_NOTICE,
  });
}

async function exportAs(format: ExportFormat): Promise<void> {
  if (!hasExportableLines(state.exportLog.lines)) return;
  for (const button of exportFormatButtons()) button.disabled = true;
  try {
    const path = await saveTranscript(transcriptSnapshot(), format, exportContent());
    el.exportMenu.open = false;
    notifySaved(t("export.saved", { path }));
  } catch (e) {
    reportError("error.export", e);
  } finally {
    renderExport();
  }
}

/** "每次結束時自動存檔": SRT and TXT (both languages) once per session. */
async function autoSaveTranscript(): Promise<void> {
  if (!state.settings?.ui.auto_save_transcripts || !hasExportableLines(state.exportLog.lines)) return;
  const session = `${state.exportLog.sessionId ?? "-"}@${state.sessionStartedAt?.getTime() ?? 0}`;
  if (state.autoSavedSession === session) return;
  state.autoSavedSession = session;
  const snap = transcriptSnapshot();
  try {
    const srt = await saveTranscript(snap, "srt", "both");
    await saveTranscript(snap, "txt", "both");
    notifySaved(t("export.savedAuto", { path: srt }));
  } catch (e) {
    reportError("error.autoSave", e);
  }
}

async function openOutputFolder(folder: OutputFolder): Promise<void> {
  try {
    await api.openOutputFolder(folder);
  } catch (e) {
    reportError("error.openFolder", e);
  }
}

function renderOutputPaths(): void {
  const o = state.output;
  el.transcriptsPath.textContent = o?.transcripts_dir
    ? t("output.transcriptsPath", { path: o.transcripts_dir })
    : o
      ? t("output.noDocuments")
      : "";
  el.openTranscripts.hidden = !o?.transcripts_dir;
  const obsFile = state.obs?.path ?? o?.obs_file ?? null;
  const obsError = state.obs && state.obs.content !== "off" ? state.obs.error : null;
  el.obsPath.textContent = obsError
    ? t("output.obsError", { error: obsError })
    : obsFile
      ? t("output.obsPath", { path: obsFile })
      : o
        ? t("output.noDocuments")
        : "";
  el.obsPath.classList.toggle("warning", obsError !== null);
  el.openObs.hidden = !obsFile;
}

/** Sends the saved OBS choice to Rust (engine start, settings saved). */
async function pushObsOutput(): Promise<void> {
  if (!state.settings) return;
  try {
    state.obs = await api.obsSetOutput(state.settings.obs_output);
  } catch (e) {
    reportError("error.applyObs", e);
  }
  renderOutputPaths();
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
  const label = (raw: string) => raw.replace("CommandOrControl", t("transcript.shortcutCtrl"));
  const keys = label(o.shortcut);
  const lines = [o.shortcut_registered ? t("transcript.shortcutOn", { keys }) : t("transcript.shortcutTaken", { keys })];
  for (const s of o.shortcuts) {
    if (s.action === "click_through") continue;
    const text = SHORTCUT_TEXT[s.action];
    const actionKeys = label(s.keys);
    lines.push(
      s.registered
        ? t(text.on, { keys: actionKeys })
        : t("transcript.shortcutActionTaken", { keys: actionKeys, action: t(text.action) }),
    );
  }
  replace(el.shortcutNote, ...lines.flatMap((line, i) => (i === 0 ? [line] : [h("br"), line])));
}

/** How each shortcut (except click-through) is described under the transcript. */
const SHORTCUT_TEXT: Readonly<Record<Exclude<ShortcutAction, "click_through">, { on: StringKey; action: StringKey }>> = {
  toggle_translation: { on: "transcript.shortcutTranslation", action: "shortcut.toggleTranslation" },
  clear_subtitles: { on: "transcript.shortcutClear", action: "shortcut.clearSubtitles" },
  say_again: { on: "transcript.shortcutSayAgain", action: "shortcut.sayAgain" },
  pause_subtitles: { on: "transcript.shortcutPause", action: "shortcut.pauseSubtitles" },
  show_history: { on: "transcript.shortcutHistory", action: "shortcut.showHistory" },
};

/** A global shortcut handled here (Rust forwards it; see lib.rs `run_shortcut`). */
function onShortcut(action: ShortcutAction): void {
  if (action === "say_again") {
    void sayAgain();
    return;
  }
  if (action !== "toggle_translation") return;
  // Same rules as the switches: not while the engine is down or a switch is in flight.
  if (!engineReady() || state.translationBusy || !state.settings) return;
  void changeShowTranslation(!showsTranslation());
}

/**
 * Interpret mode (G3): shows "could you say that again?" in the partner's
 * language on the overlay. Does nothing outside a running interpret session.
 */
async function sayAgain(): Promise<void> {
  const settings = state.settings;
  if (!settings || settings.mode !== "interpret" || state.sessionState !== "running") return;
  const phrase = sayAgainPhrase(settings.partner_language);
  try {
    // The other person has to see it: bring a hidden subtitle window back first.
    if (state.overlay && !state.overlay.visible) await setOverlayVisible(true);
    await api.overlayNotice(phrase.text, phrase.lang);
  } catch (e) {
    reportError("error.sayAgain", e);
  }
}

/** Moves the subtitle window back onto the main screen (G6). */
async function resetOverlayPosition(): Promise<void> {
  try {
    state.overlay = await api.overlayResetPosition();
  } catch (e) {
    reportError("error.resetOverlay", e);
  }
  renderOverlay();
}

async function setOverlayVisible(visible: boolean): Promise<void> {
  try {
    state.overlay = await api.overlaySetVisible(visible);
  } catch (e) {
    reportError(visible ? "error.showOverlay" : "error.hideOverlay", e);
  }
  renderOverlay();
}

// ---------------------------------------------------------------- "顯示翻譯"

function renderTranslationSwitches(): void {
  const on = showsTranslation();
  for (const input of [el.showTranslation, el.settingsShowTranslation]) {
    input.checked = on;
    input.disabled = state.translationBusy || !engineReady();
  }
}

/**
 * Both switches (subtitle log and settings page) change `translation.enabled`;
 * a running session switches at once (lib/translation-toggle.ts).
 */
async function changeShowTranslation(enabled: boolean): Promise<void> {
  state.translationBusy = true;
  renderTranslationSwitches();
  try {
    const result = await setShowTranslation(enabled, state.sessionState, {
      sessionSetTranslation: (on) => api.sessionSetTranslation(on),
      settingsSet: (patch) => api.settingsSet(patch),
      overlaySetShowTranslation: (on) => api.overlaySetShowTranslation(on),
    });
    adoptSettings(result.settings.settings, result.settings.revision);
    if (result.overlay) state.overlay = result.overlay;
    else reportError("error.setTranslation", result.overlayError);
    renderTranscript();
    renderModeNotes();
    renderTranslationGuide();
    // The preview says whether a translation model is needed at all.
    scheduleLanguagePreview(0);
  } catch (e) {
    reportError("error.setTranslation", e);
  } finally {
    state.translationBusy = false;
    renderTranslationSwitches();
  }
}

/** Sends the saved "顯示翻譯" to the overlay (engine start, reconnect). */
async function pushOverlayShowTranslation(): Promise<void> {
  if (!state.settings) return;
  try {
    state.overlay = await api.overlaySetShowTranslation(state.settings.translation.enabled);
  } catch (e) {
    reportError("error.setTranslation", e);
  }
}

// ---------------------------------------------------------------- furigana (settings + models page)

function statusText(line: StatusLine): string {
  return t(line.key, line.params);
}

/** The settings-page switch and its status line (download progress, errors). */
function renderFuriganaSwitch(): void {
  const view = furiganaSwitchView({
    enabled: state.settings?.furigana.enabled ?? false,
    dictionary: state.models?.furigana ?? null,
    modelsLoaded: state.models !== null,
    download: state.furiganaDownload,
    pendingEnable: state.furiganaPendingEnable,
    busy: state.furiganaBusy,
    engineReady: engineReady(),
  });
  el.furiganaSwitch.checked = view.checked;
  el.furiganaSwitch.disabled = view.disabled;
  const size = state.models?.furigana?.download_bytes ?? null;
  setIfChanged(el.furiganaHelp, "textContent", t("settings.furiganaHelp", { size: formatBytes(size) }));
  if (!view.status) {
    replace(el.furiganaStatus);
    el.furiganaStatus.hidden = true;
    return;
  }
  el.furiganaStatus.hidden = false;
  el.furiganaStatus.classList.toggle("field-error", view.status.error);
  replace(
    el.furiganaStatus,
    view.percent === null
      ? null
      : h("progress", { max: 100, value: view.percent, "aria-label": t("furigana.progressLabel") }),
    h("span", {}, statusText(view.status)),
  );
}

/** Models page: version, disk use, license and Delete with an in-page confirmation. */
function renderFuriganaBlock(): void {
  const dictionary = state.models?.furigana ?? null;
  const section = el.furiganaBlock.closest("section");
  if (section) section.hidden = state.models !== null && dictionary === null;
  if (!dictionary) {
    replace(el.furiganaBlock, h("p", { class: "rail-hint" }, t("common.loading")));
    return;
  }
  const view = furiganaBlockView(dictionary, {
    downloading: state.furiganaDownload?.state === "downloading",
    deleting: state.furiganaDeleting,
    engineReady: engineReady(),
  });
  const lines = view.lines.map((line) => h("p", { class: "gpu-state" }, statusText(line)));
  const deletable = view.deletable;
  if (!deletable) {
    replace(el.furiganaBlock, ...lines);
    return;
  }
  if (state.furiganaConfirmDelete && !deletable.enabled) state.furiganaConfirmDelete = false;
  if (state.furiganaConfirmDelete) {
    const yes = h("button", { type: "button", class: "danger" }, t("asr.deleteYes"));
    yes.addEventListener("click", () => void deleteFuriganaDictionary());
    const no = h("button", { type: "button", class: "ghost" }, t("asr.deleteNo"));
    no.addEventListener("click", () => {
      state.furiganaConfirmDelete = false;
      renderFuriganaBlock();
    });
    const size = formatBytes(dictionary.disk_bytes ?? dictionary.installed_bytes);
    replace(
      el.furiganaBlock,
      ...lines,
      h(
        "div",
        { class: "delete-confirm", role: "group" },
        h("p", {}, t("furigana.deleteConfirm", { size })),
        h("div", { class: "gpu-actions" }, yes, no),
      ),
    );
    return;
  }
  const del = h(
    "button",
    { type: "button", class: "ghost", title: deletable.reason ? statusText(deletable.reason) : null },
    state.furiganaDeleting ? t("asr.deleting") : t("furigana.delete"),
  );
  del.disabled = !deletable.enabled;
  del.addEventListener("click", () => {
    state.furiganaConfirmDelete = true;
    renderFuriganaBlock();
    // Keyboard users land on the safe choice (only when it opens, not on every redraw).
    el.furiganaBlock.querySelector<HTMLButtonElement>(".delete-confirm .ghost")?.focus();
  });
  replace(
    el.furiganaBlock,
    ...lines,
    deletable.reason ? h("p", { class: "gpu-state" }, statusText(deletable.reason)) : null,
    h("div", { class: "gpu-actions" }, del),
  );
}

function renderFurigana(): void {
  renderFuriganaSwitch();
  renderFuriganaBlock();
}

/** Re-reads `models.list` (the furigana dictionary and the speaker model live there too). */
async function refreshModels(): Promise<void> {
  try {
    state.models = await api.modelsList();
  } catch (e) {
    reportError("error.readModels", e);
  }
  renderModels();
}

/** `session.set_furigana`; the running session switches at once. */
async function setFurigana(enabled: boolean): Promise<void> {
  state.furiganaBusy = true;
  renderFuriganaSwitch();
  try {
    const result = await api.sessionSetFurigana(enabled);
    adoptSettings(result.settings, result.revision);
  } catch (e) {
    reportError("error.setFurigana", e);
    if (engineCode(e) === "model_not_downloaded") void refreshModels();
  } finally {
    state.furiganaBusy = false;
    renderFuriganaSwitch();
  }
}

/** The settings switch: turning it on downloads the dictionary first when it is missing. */
async function changeFurigana(enabled: boolean): Promise<void> {
  if (!enabled) {
    state.furiganaPendingEnable = false;
    await setFurigana(false);
    return;
  }
  const action = furiganaEnableAction(state.models?.furigana ?? null);
  if (action === "enable") {
    await setFurigana(true);
    return;
  }
  if (action === "unsupported") {
    renderFuriganaSwitch();
    return;
  }
  state.furiganaPendingEnable = true;
  state.furiganaDownload = {
    model: FURIGANA_DICTIONARY_ID,
    kind: "furigana",
    downloaded_bytes: 0,
    total_bytes: state.models?.furigana?.download_bytes ?? 0,
    state: "downloading",
    error: null,
  };
  renderFurigana();
  try {
    await api.modelsDownload(FURIGANA_DICTIONARY_ID);
  } catch (e) {
    if (engineCode(e) === "download_in_progress") return; // progress events follow
    state.furiganaPendingEnable = false;
    state.furiganaDownload = null;
    reportError("error.setFurigana", e);
    renderFurigana();
  }
}

/** `download_progress` of the dictionary: progress, then furigana on (if asked) or the error. */
function onFuriganaDownload(p: DownloadProgressEvent): void {
  state.furiganaDownload = p.state === "done" ? null : p;
  const next = afterFuriganaDownload(p, state.furiganaPendingEnable);
  if (p.state !== "downloading") state.furiganaPendingEnable = false;
  if (next === "failed") {
    notify(t("furigana.downloadFailed", { error: p.error ?? t("common.unknownReason") }), "bad");
  }
  if (p.state === "done") {
    void refreshModels().then(async () => {
      if (next !== "enable") return;
      await setFurigana(true);
      if (state.settings?.furigana.enabled) notify(t("furigana.ready"), "info", [], { autoDismiss: true });
    });
  }
  renderFurigana();
}

/** `models.delete` of the dictionary after the user confirmed. */
async function deleteFuriganaDictionary(): Promise<void> {
  state.furiganaConfirmDelete = false;
  state.furiganaDeleting = true;
  renderFuriganaBlock();
  try {
    const result = await api.modelsDelete(FURIGANA_DICTIONARY_ID);
    notify(t("furigana.deleted", { size: formatBytes(result.freed_bytes) }), "info", [], { autoDismiss: true });
  } catch (e) {
    reportError("error.deleteFurigana", e);
  } finally {
    state.furiganaDeleting = false;
  }
  await refreshModels();
}

// ---------------------------------------------------------------- speaker labels (settings + models page + transcript)

function speakerFallback(voice: number): string {
  return t("speaker.voice", { n: voice });
}

/** The shown name of `voice` (from 1) this session. */
function speakerName(voice: number): string {
  return voiceName(voice, state.speakerNames, speakerFallback);
}

/** Saves this session's names and hands them to the overlay; redraws the transcript. */
async function setSpeakerNames(names: string[]): Promise<void> {
  state.speakerNames = names;
  renderTranscript();
  try {
    state.overlay = await api.overlaySetSpeakerNames(names);
  } catch (e) {
    reportError("error.setSpeakers", e);
  }
}

/** Commits an open rename and moves the box back out of the transcript rows. */
function parkVoiceRename(): void {
  commitVoiceRename();
  el.transcript.after(el.voiceRename);
}

/** Opens the inline rename box in the row of the clicked chip. */
function openVoiceRename(chip: HTMLButtonElement, voice: number): void {
  const row = chip.closest("li");
  if (!row) return;
  // A box still open for another speaker keeps its text for that speaker.
  commitVoiceRename();
  el.voiceRename.dataset.voice = String(voice);
  // Appended after the row's three parts, which updateLineElement reads by position.
  row.append(el.voiceRename);
  el.voiceRename.hidden = false;
  el.voiceRenameInput.value = (state.speakerNames[voice - 1] ?? "").trim() || speakerName(voice);
  el.voiceRenameInput.focus();
  el.voiceRenameInput.select();
}

function closeVoiceRename(): void {
  delete el.voiceRename.dataset.voice;
  el.voiceRename.hidden = true;
}

/** Enter or leaving the box: the typed name (blank = back to "Speaker N"). */
function commitVoiceRename(): void {
  const voice = Number(el.voiceRename.dataset.voice);
  if (el.voiceRename.hidden || !Number.isInteger(voice) || voice < 1) return;
  closeVoiceRename();
  // Same limits as Rust (`overlay::validate_speaker_names`): one line, at most 40 characters.
  const typed = Array.from(el.voiceRenameInput.value.replace(/\p{Cc}+/gu, " ").trim())
    .slice(0, MAX_SPEAKER_NAME_CHARS)
    .join("");
  const names = [...state.speakerNames];
  while (names.length < voice) names.push("");
  names[voice - 1] = typed === speakerFallback(voice) ? "" : typed;
  while (names.length && names[names.length - 1] === "") names.pop();
  void setSpeakerNames(names);
}

/** The rail select (under the audio source) and its status line (download progress, errors). */
function renderSpeakerSelect(): void {
  // Interpret mode already says who spoke ("You" / "Them"); the labels are for media mode only.
  el.speakersBlock.hidden = state.mode !== "media";
  if (el.speakersSelect.options.length === 0) {
    replace(
      el.speakersSelect,
      ...SPEAKER_CHOICES.map((choice) =>
        h(
          "option",
          { value: choice },
          choice === "off"
            ? t("settings.speakersOff")
            : choice === "auto"
              ? t("settings.speakersAuto")
              : t("settings.speakersCount", { n: Number(choice) }),
        ),
      ),
    );
  }
  const view = speakerSelectView({
    speakers: state.settings?.speakers ?? DEFAULT_SETTINGS.speakers,
    model: state.models?.speaker_model ?? null,
    modelsLoaded: state.models !== null,
    download: state.speakerDownload,
    pending: state.speakersPending,
    busy: state.speakersBusy,
    engineReady: engineReady(),
  });
  el.speakersSelect.value = view.value;
  el.speakersSelect.disabled = view.disabled;
  const size = state.models?.speaker_model?.download_bytes ?? null;
  setIfChanged(el.speakersHelp, "textContent", t("settings.speakersHelp", { size: formatBytes(size) }));
  if (!view.status) {
    replace(el.speakersStatus);
    el.speakersStatus.hidden = true;
    return;
  }
  el.speakersStatus.hidden = false;
  el.speakersStatus.classList.toggle("field-error", view.status.error);
  replace(
    el.speakersStatus,
    view.percent === null
      ? null
      : h("progress", { max: 100, value: view.percent, "aria-label": t("speakers.progressLabel") }),
    h("span", {}, statusText(view.status)),
  );
}

/** Models page: model name, disk use, license and Delete with an in-page confirmation. */
function renderSpeakerBlock(): void {
  const model = state.models?.speaker_model ?? null;
  const section = el.speakerBlock.closest("section");
  if (section) section.hidden = state.models !== null && model === null;
  if (!model) {
    replace(el.speakerBlock, h("p", { class: "rail-hint" }, t("common.loading")));
    return;
  }
  const view = speakerBlockView(model, {
    downloading: state.speakerDownload?.state === "downloading",
    deleting: state.speakerDeleting,
    engineReady: engineReady(),
  });
  const lines = view.lines.map((line) => h("p", { class: "gpu-state" }, statusText(line)));
  const deletable = view.deletable;
  if (!deletable) {
    replace(el.speakerBlock, ...lines);
    return;
  }
  if (state.speakerConfirmDelete && !deletable.enabled) state.speakerConfirmDelete = false;
  if (state.speakerConfirmDelete) {
    const yes = h("button", { type: "button", class: "danger" }, t("asr.deleteYes"));
    yes.addEventListener("click", () => void deleteSpeakerModel());
    const no = h("button", { type: "button", class: "ghost" }, t("asr.deleteNo"));
    no.addEventListener("click", () => {
      state.speakerConfirmDelete = false;
      renderSpeakerBlock();
    });
    const size = formatBytes(model.disk_bytes ?? model.installed_bytes);
    replace(
      el.speakerBlock,
      ...lines,
      h(
        "div",
        { class: "delete-confirm", role: "group" },
        h("p", {}, t("speakers.deleteConfirm", { size })),
        h("div", { class: "gpu-actions" }, yes, no),
      ),
    );
    return;
  }
  const del = h("button", { type: "button", class: "ghost" }, state.speakerDeleting ? t("asr.deleting") : t("speakers.delete"));
  del.disabled = !deletable.enabled;
  del.addEventListener("click", () => {
    state.speakerConfirmDelete = true;
    renderSpeakerBlock();
    el.speakerBlock.querySelector<HTMLButtonElement>(".delete-confirm .ghost")?.focus();
  });
  replace(el.speakerBlock, ...lines, h("div", { class: "gpu-actions" }, del));
}

function renderSpeakers(): void {
  renderSpeakerSelect();
  renderSpeakerBlock();
}

/** `settings.set` of `speakers`; read by the next session. */
async function saveSpeakers(choice: SpeakerChoice): Promise<void> {
  state.speakersBusy = true;
  renderSpeakerSelect();
  try {
    const result = await api.settingsSet(speakersPatch(choice));
    adoptSettings(result.settings, result.revision);
  } catch (e) {
    reportError("error.setSpeakers", e);
  } finally {
    state.speakersBusy = false;
    renderSpeakerSelect();
  }
}

/** The settings select: choosing labels downloads the model first when it is missing. */
async function changeSpeakers(choice: SpeakerChoice): Promise<void> {
  const action = speakerChoiceAction(choice, state.models?.speaker_model ?? null);
  if (action === "save") {
    state.speakersPending = null;
    await saveSpeakers(choice);
    return;
  }
  if (action === "unsupported") {
    renderSpeakerSelect();
    return;
  }
  state.speakersPending = choice;
  state.speakerDownload = {
    model: SPEAKER_MODEL_ID,
    kind: "speaker",
    downloaded_bytes: 0,
    total_bytes: state.models?.speaker_model?.download_bytes ?? 0,
    state: "downloading",
    error: null,
  };
  renderSpeakers();
  try {
    await api.modelsDownload(SPEAKER_MODEL_ID);
  } catch (e) {
    if (engineCode(e) === "download_in_progress") return; // progress events follow
    state.speakersPending = null;
    state.speakerDownload = null;
    reportError("error.setSpeakers", e);
    renderSpeakers();
  }
}

/** `download_progress` of the speaker model: progress, then the waiting choice saved, or the error. */
function onSpeakerDownload(p: DownloadProgressEvent): void {
  state.speakerDownload = p.state === "done" ? null : p;
  const pending = state.speakersPending;
  const next = afterSpeakerDownload(p, pending);
  if (p.state !== "downloading") state.speakersPending = null;
  if (next === "failed") {
    notify(t("speakers.downloadFailed", { error: p.error ?? t("common.unknownReason") }), "bad");
  }
  if (p.state === "done") {
    void refreshModels().then(async () => {
      if (next !== "save" || pending === null) return;
      await saveSpeakers(pending);
      if (state.settings?.speakers.enabled) notify(t("speakers.ready"), "info", [], { autoDismiss: true });
    });
  }
  renderSpeakers();
}

/** `models.delete` of the speaker model after the user confirmed. */
async function deleteSpeakerModel(): Promise<void> {
  state.speakerConfirmDelete = false;
  state.speakerDeleting = true;
  renderSpeakerBlock();
  try {
    const result = await api.modelsDelete(SPEAKER_MODEL_ID);
    notify(t("speakers.deleted", { size: formatBytes(result.freed_bytes) }), "info", [], { autoDismiss: true });
    // The engine turned speaker labels off with the model; read the saved settings back.
    const current = await api.settingsGet();
    adoptSettings(current.settings, current.revision);
  } catch (e) {
    reportError("error.deleteSpeakers", e);
  } finally {
    state.speakerDeleting = false;
  }
  await refreshModels();
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
  const settings = adoptSettings(result.settings, result.revision);
  if (state.hardware) state.hardware = { ...state.hardware, recommendation: result.recommendation };
  if (result.warnings.length) console.warn("settings fields replaced by defaults:", result.warnings);
  // The saved settings now equal the recommendation: drop the offer, and any
  // preview still in flight (computed against the old settings).
  forgetPreview();
  fillCombo(settings, force);
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
    const { settings, revision } = await api.settingsGet();
    fillCombo(adoptSettings(settings, revision));
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
    const { settings, revision } = await api.settingsSet({ ui: { dismissed_recommendations: dismissedWith(current, fingerprint) } });
    adoptSettings(settings, revision);
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

/**
 * `session.start` reported `model_not_downloaded`: download the chosen ASR
 * models that are missing and start again. Returns whether any were found.
 */
async function downloadMissingThenStart(): Promise<boolean> {
  if (!state.settings || !engineReady()) return false;
  try {
    state.models = await api.modelsList();
  } catch (e) {
    reportError("error.readModels", e);
    return false;
  }
  renderModels();
  const items = asrStartItems();
  if (items.length) startFlow.begin(items);
  return items.length > 0;
}

/** The start panel showed the missing models' downloads when the notice was last rendered. */
let missingInStartPanel = false;

function renderMissingNotice(): void {
  if (!state.missing) return;
  const view = missingModelsView(state.missing, state.models?.asr ?? null, state.downloads);
  const inPanel = missingShownByStartPanel(state.missing, (id) => startFlow.tracks("asr", id));
  const mode = missingNoticeMode(view.state, inPanel, missingInStartPanel);
  missingInStartPanel = inPanel;
  if (mode !== "notice") {
    // One progress display is enough: the start panel's (lib/recommend.ts missingNoticeMode).
    noticeFor(MISSING_NOTICE)?.remove();
    if (mode === "forget") state.missing = null;
    return;
  }
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
  state.downloads.set(id, { model: id, kind: null, downloaded_bytes: 0, total_bytes: 0, state: "downloading", error: null });
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

/** "Downloaded", the space it takes, and Delete with an in-page confirmation (F7). */
function downloadedCell(model: AsrModel): HTMLElement {
  const offer = deleteOffer(model, {
    fastModel: state.settings?.asr.fast_model ?? null,
    finalModel: state.settings?.asr.final_model ?? null,
    sessionActive: sessionActive(),
    downloading: state.downloads.get(model.id)?.state === "downloading",
    deleting: state.deleting.has(model.id),
    engineReady: engineReady(),
  });
  const wrap = h(
    "div",
    { class: "model-state" },
    h("span", { class: "ok" }, t("asr.downloaded")),
    model.disk_bytes === null ? null : h("span", { class: "model-disk" }, t("asr.diskUse", { size: formatBytes(model.disk_bytes) })),
  );
  if (!offer) return wrap;
  // A confirmation interrupted by a block (session start, engine restart) is dropped,
  // so it never reappears without a click.
  if (state.confirmDelete === model.id && offer.blocked !== null) state.confirmDelete = null;
  if (state.confirmDelete === model.id) {
    const yes = h("button", { type: "button", class: "danger" }, t("asr.deleteYes"));
    yes.addEventListener("click", () => void deleteModel(model));
    const no = h("button", { type: "button", class: "ghost" }, t("asr.deleteNo"));
    no.addEventListener("click", () => {
      state.confirmDelete = null;
      renderModels();
    });
    wrap.append(
      h("div", { class: "delete-confirm", role: "group" }, h("p", {}, deleteConfirmText(model, offer)), h("div", { class: "gpu-actions" }, yes, no)),
    );
    return wrap;
  }
  const del = h(
    "button",
    { type: "button", class: "ghost", title: offer.blocked ? deleteBlockedText(offer.blocked) : null },
    offer.blocked === "deleting" ? t("asr.deleting") : t("asr.delete"),
  );
  del.disabled = offer.blocked !== null;
  del.addEventListener("click", () => {
    state.confirmDelete = model.id;
    renderModels();
    // Keyboard users land on the safe choice (only when it opens, not on every redraw).
    el.asrModels.querySelector<HTMLButtonElement>(".delete-confirm .ghost")?.focus();
  });
  wrap.append(del);
  return wrap;
}

/** `models.delete` after the user confirmed; the list is reloaded either way. */
async function deleteModel(model: AsrModel): Promise<void> {
  state.confirmDelete = null;
  state.deleting.add(model.id);
  renderModels();
  try {
    const result = await api.modelsDelete(model.id);
    notify(t("asr.deleted", { model: result.model, size: formatBytes(result.freed_bytes) }), "info", [], { autoDismiss: true });
  } catch (e) {
    // model_not_downloaded / model_in_use / unknown_model have their own sentences;
    // the reload below shows the list as the engine sees it now.
    reportError("error.deleteModel", e, { model: model.id });
  } finally {
    state.deleting.delete(model.id);
  }
  try {
    state.models = await api.modelsList();
  } catch (e) {
    reportError("error.readModels", e);
  }
  renderModels();
  renderMissingNotice();
}

function downloadCell(model: AsrModel): HTMLElement {
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
  if (model.downloaded) return downloadedCell(model);
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
  renderFurigana();
  renderSpeakers();
  if (!m) {
    replace(el.asrModels, h("tr", {}, h("td", { colspan: 6 }, t("common.loading"))));
    renderTranslationPickers();
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
  renderComboOptions();
  renderTranslationGuide();
}

// ---------------------------------------------------------------- translation server guide

/** The guide that applies now (lib/translation-guide.ts), or null. */
function currentGuide(): GuideView | null {
  const server = state.models?.translation.server ?? state.hardware?.hardware.translation_server ?? null;
  return translationGuide(
    {
      translationEnabled: showsTranslation(),
      server,
      startBlockedUnreachable: startFlow.state.blocker === "server_unreachable",
    },
    state.settings?.translation.base_url ?? DEFAULT_SETTINGS.translation.base_url,
  );
}

/**
 * "Install Ollama" / "Use original only" when the translation server cannot be
 * reached: on the settings page, and once as a notice each time it appears, so
 * it is seen before starting.
 */
function renderTranslationGuide(): void {
  const guide = currentGuide();
  el.guide.hidden = guide === null;
  if (!guide) {
    state.guideShown = false;
    noticeFor(GUIDE_NOTICE)?.remove();
    return;
  }
  // The guide says the same in the interface language; the engine's raw text stays in diagnostics.
  noticeFor(TRANSLATION_LOG_NOTICE)?.remove();
  const text = t(guide.offerInstall ? "guide.local" : "guide.remote", { url: guide.url });
  el.guideText.textContent = text;
  el.guideInstall.hidden = !guide.offerInstall;
  el.guideRecheck.disabled = !engineReady();
  el.guideOriginal.disabled = !engineReady() || state.translationBusy;
  if (state.guideShown) return;
  state.guideShown = true;
  const actions = [
    ...(guide.offerInstall ? [{ label: t("guide.install"), run: () => void openOllamaDownload() }] : []),
    { label: t("guide.originalOnly"), run: () => void changeShowTranslation(false) },
  ];
  notify(text, "info", actions, { key: GUIDE_NOTICE });
}

async function openOllamaDownload(): Promise<void> {
  try {
    await api.openOllamaDownload();
  } catch (e) {
    reportError("error.openOllama", e);
  }
}

/** Probes the translation server again (models.list) after the user installed or started it. */
async function recheckTranslationServer(): Promise<void> {
  if (!engineReady()) return;
  el.guideRecheck.disabled = true;
  try {
    state.models = await api.modelsList();
    renderModels();
    renderMissingNotice();
  } catch (e) {
    reportError("error.readModels", e);
  } finally {
    renderTranslationGuide();
  }
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
  state.downloads.set(id, { model: id, kind: null, downloaded_bytes: 0, total_bytes: 0, state: "downloading", error: null });
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
  renderTranslationPickers();
}

const TRANSLATION_FIELDS = ["translation_fast", "translation_final"] as const;
type TranslationField = (typeof TRANSLATION_FIELDS)[number];
/** Fields where the user picked "other": the text field stays open while they type. */
const translationCustomOpen: Record<TranslationField, boolean> = { translation_fast: false, translation_final: false };

function translationSelect(field: TranslationField): HTMLSelectElement {
  const select = el.comboForm.querySelector<HTMLSelectElement>(`select[data-pick-for="${field}"]`);
  if (!select) throw new Error(`combo form has no picker for ${field}`);
  return select;
}

/** Dropdowns from `models.list`; the hidden text field keeps the value that is validated and saved. */
function renderTranslationPickers(): void {
  const models = state.models?.translation.models ?? [];
  for (const field of TRANSLATION_FIELDS) {
    const textField = namedField(el.comboForm, field);
    const picker = translationPicker(models, textField.value, translationCustomOpen[field]);
    const select = translationSelect(field);
    replace(select, ...picker.options.map((o) => h("option", { value: o.value }, o.label)));
    select.value = picker.selected;
    textField.hidden = !picker.showTextField;
  }
}

/**
 * Runs on the select's `input` event, before the form's listener re-validates, so the text
 * field already holds the picked name. Arrow keys on a closed select fire `input` on every
 * step, so "other" only reveals the text field; taking focus would trap keyboard users.
 */
function onTranslationPicked(field: TranslationField): void {
  const select = translationSelect(field);
  const textField = namedField(el.comboForm, field);
  if (select.value === CUSTOM_TRANSLATION_MODEL) {
    translationCustomOpen[field] = true;
    textField.hidden = false;
  } else {
    translationCustomOpen[field] = false;
    textField.value = select.value;
    textField.hidden = true;
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
  for (const field of TRANSLATION_FIELDS) translationCustomOpen[field] = false;
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
  // The text field is hidden unless "other" is picked; the select is what the user sees.
  for (const field of TRANSLATION_FIELDS) translationSelect(field).setAttribute("aria-invalid", String(Boolean(errors[field])));
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
    const { settings: saved, revision } = await api.settingsSet(combinationPatch(form));
    const settings = adoptSettings(saved, revision);
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
  "font_size",
  "background_opacity_percent",
  "position",
  "max_lines",
  "min_display_secs",
  "idle_clear_secs",
  "pace_by_length",
  "max_line_em",
  "original_scale_percent",
  "original_position",
  "furigana_first_only",
  "obs_output",
  "auto_save_transcripts",
];

function readForm(): SettingsForm {
  const field = (name: keyof SettingsForm) => namedField(el.settingsForm, name);
  return {
    base_url: field("base_url").value,
    api_key_env: field("api_key_env").value,
    allow_restricted: (field("allow_restricted") as HTMLInputElement).checked,
    font_size: field("font_size").value,
    background_opacity_percent: field("background_opacity_percent").value,
    position: field("position").value,
    max_lines: field("max_lines").value,
    min_display_secs: field("min_display_secs").value,
    idle_clear_secs: field("idle_clear_secs").value,
    pace_by_length: (field("pace_by_length") as HTMLInputElement).checked,
    max_line_em: field("max_line_em").value,
    original_scale_percent: field("original_scale_percent").value,
    original_position: field("original_position").value,
    furigana_first_only: (field("furigana_first_only") as HTMLInputElement).checked,
    obs_output: field("obs_output").value,
    auto_save_transcripts: (field("auto_save_transcripts") as HTMLInputElement).checked,
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
  // G7: original size and place, as the overlay will draw them.
  const scale = Number.parseInt(form.original_scale_percent, 10);
  el.oOrigScale.textContent = Number.isFinite(scale) ? `${scale}%` : "";
  if (Number.isFinite(scale)) el.preview.style.setProperty("--orig-scale", String(scale / 100));
  el.preview.dataset.original = form.original_position;
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
    const { settings, revision } = await api.settingsSet(settingsPatchFromForm(form));
    adoptSettings(settings, revision);
    await pushOverlayStyle();
    if (state.obs?.content !== obsOutputFromForm(form)) await pushObsOutput();
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

// ---------------------------------------------------------------- settings page: glossary

let nextGlossaryRowId = 1;
/** Rows added in this window and not edited yet: their "required" errors wait for a save attempt. */
const freshGlossaryRows = new Set<number>();
const GLOSSARY_FIELDS: readonly GlossaryField[] = ["source", "target", "target_language"];

function glossaryEntries(): GlossaryEntry[] {
  return state.glossary.rows.map((r) => r.entry);
}

function toGlossaryRows(entries: readonly GlossaryEntry[]): GlossaryRow[] {
  return entries.map((entry) => ({ id: nextGlossaryRowId++, entry: { ...entry } }));
}

/** Puts the saved glossary into the editor unless the user has unsaved edits (or `force`). */
function fillGlossary(settings: Settings, force = false): void {
  if (state.glossary.dirty && !force) return;
  const g = settings.translation.glossary;
  state.glossary = {
    builtin: g.builtin,
    rows: toGlossaryRows(g.entries),
    dirty: false,
    engineError: null,
    saving: false,
    revision: state.glossary.revision,
    corrections: correctionsToText(g.corrections),
  };
  el.correctionsInput.value = state.glossary.corrections;
  freshGlossaryRows.clear();
  el.glossaryStatus.textContent = "";
  renderGlossary();
}

/** Local problems plus the engine's last rejection, placed on its row (or the list). */
function glossaryErrors(): { rows: Map<number, GlossaryRowErrors>; list: string | null } {
  const local = validateGlossary(glossaryEntries(), state.languages?.map((l) => l.code) ?? null);
  const rows = new Map(local.rows);
  let list = local.list;
  const rejected = state.glossary.engineError;
  if (rejected) {
    const message = t("form.engineRejected", { message: rejected.message });
    if (rejected.target.kind === "entry" && rejected.target.index < state.glossary.rows.length) {
      const field = rejected.target.field ?? "source";
      const current = rows.get(rejected.target.index) ?? {};
      if (!current[field]) rows.set(rejected.target.index, { ...current, [field]: message });
    } else {
      list = list ?? message;
    }
  }
  return { rows, list };
}

function glossaryLanguageSelect(value: string | null, label: string): HTMLSelectElement {
  const options = [h("option", { value: "" }, t("glossary.allLanguages"))];
  const list = state.languages ?? [];
  for (const o of languageOptions(list)) options.push(h("option", { value: o.value, dir: "auto" }, o.label));
  // A code the list does not have (older settings) stays visible instead of silently changing.
  if (value !== null && !list.some((l) => l.code === value)) options.push(h("option", { value }, value));
  const select = h("select", { "data-field": "target_language", "aria-label": label }, ...options);
  select.value = value ?? "";
  return select;
}

function glossaryRowElement(row: GlossaryRow, index: number): HTMLTableRowElement {
  const n = index + 1;
  const input = (field: "source" | "target", label: string) => {
    const i = h("input", { "data-field": field, "aria-label": label, autocomplete: "off", spellcheck: "false", dir: "auto" });
    i.value = row.entry[field];
    return i;
  };
  const cell = (control: HTMLElement, field: GlossaryField) =>
    h("td", { "data-cell": field }, control, h("p", { class: "field-error", "data-error-for": field }));
  const remove = h(
    "button",
    { type: "button", class: "ghost", "aria-label": t("glossary.removeLabel", { row: n }) },
    t("glossary.remove"),
  );
  remove.addEventListener("click", () => removeGlossaryRow(row.id));
  return h(
    "tr",
    { "data-row": row.id },
    cell(input("source", t("glossary.sourceLabel", { row: n })), "source"),
    cell(input("target", t("glossary.targetLabel", { row: n })), "target"),
    cell(glossaryLanguageSelect(row.entry.target_language, t("glossary.languageLabel", { row: n })), "target_language"),
    h("td", { class: "glossary-actions" }, remove),
  );
}

/** Rebuilds the table (rows added, removed, imported, reloaded or relabelled). */
function renderGlossary(): void {
  el.glossaryBuiltin.checked = state.glossary.builtin;
  replace(el.glossaryRows, ...state.glossary.rows.map((row, i) => glossaryRowElement(row, i)));
  el.glossaryEmpty.hidden = state.glossary.rows.length > 0;
  renderGlossaryErrors();
}

/** Updates only error texts and buttons, so typing never loses focus. */
function renderGlossaryErrors(): void {
  const errors = glossaryErrors();
  state.glossary.rows.forEach((row, i) => {
    const tr = el.glossaryRows.querySelector<HTMLElement>(`tr[data-row="${row.id}"]`);
    if (!tr) return;
    const rowErrors = freshGlossaryRows.has(row.id) ? {} : (errors.rows.get(i) ?? {});
    for (const field of GLOSSARY_FIELDS) {
      const message = rowErrors[field] ?? "";
      const slot = tr.querySelector<HTMLElement>(`[data-error-for="${field}"]`);
      if (slot) slot.textContent = message;
      tr.querySelector(`[data-field="${field}"]`)?.setAttribute("aria-invalid", String(message !== ""));
      tr.querySelector(`[data-cell="${field}"]`)?.classList.toggle("has-error", message !== "");
    }
  });
  el.glossaryListError.textContent = errors.list ?? "";
  // Correction rules: every problem with its line number, one per line.
  const fixes = parseCorrectionsText(state.glossary.corrections).errors;
  el.correctionsError.textContent = fixes
    .map((e) => (e.line > 0 ? t("glossary.correctionLine", { line: e.line, message: e.message }) : e.message))
    .join("\n");
  el.correctionsInput.setAttribute("aria-invalid", String(fixes.length > 0));
  el.glossarySave.disabled = !engineReady() || !state.settings || !state.glossary.dirty || state.glossary.saving;
  el.glossaryReset.disabled = !state.glossary.dirty;
  el.glossaryExport.disabled = state.glossary.rows.length === 0;
}

function markGlossaryEdited(): void {
  state.glossary.dirty = true;
  state.glossary.revision++;
  el.glossaryStatus.textContent = "";
}

function addGlossaryRow(): void {
  const [row] = toGlossaryRows([{ source: "", target: "", target_language: null }]);
  if (!row) return;
  state.glossary.rows.push(row);
  freshGlossaryRows.add(row.id);
  // Row positions changed: an engine error by index no longer points at the right row.
  state.glossary.engineError = null;
  markGlossaryEdited();
  renderGlossary();
  el.glossaryRows.querySelector<HTMLInputElement>(`tr[data-row="${row.id}"] [data-field="source"]`)?.focus();
}

/** Text selected inside one original of the transcript (G4), or null. */
let transcriptSelection: { text: string; targetLanguage: string } | null = null;

/** The selection lies within a single original (not across rows, not in a translation). */
function selectedOriginal(): { text: string; targetLanguage: string } | null {
  const selection = document.getSelection();
  if (!selection || selection.rangeCount === 0 || selection.isCollapsed) return null;
  const range = selection.getRangeAt(0);
  // Furigana: the selection may include <rt> readings; the glossary wants the original only.
  const copy = range.cloneContents();
  for (const rt of copy.querySelectorAll("rt")) rt.remove();
  const text = (copy.textContent ?? "").trim();
  if (text === "") return null;
  const start = range.startContainer.parentElement?.closest(".utt-orig");
  const end = range.endContainer.parentElement?.closest(".utt-orig");
  if (!start || start !== end || !el.transcript.contains(start)) return null;
  const id = Number(start.closest<HTMLElement>("li.utt")?.dataset.id);
  const line = state.subtitles.lines.find((l) => l.utteranceId === id);
  return { text, targetLanguage: line?.targetLanguage ?? state.settings?.target_language ?? "" };
}

function onTranscriptSelection(): void {
  // Clicking the button must not lose the selection it acts on: only a new selection replaces it.
  const current = selectedOriginal();
  if (current) transcriptSelection = current;
  else if (document.activeElement !== el.addToGlossary) transcriptSelection = null;
  el.addToGlossary.disabled = transcriptSelection === null;
}

/** Opens the glossary with a new row for the selected words (G4); the user fills in the translation. */
function addSelectionToGlossary(): void {
  const picked = transcriptSelection;
  if (!picked) return;
  const source = Array.from(picked.text).slice(0, GLOSSARY_MAX_SOURCE_CHARS).join("");
  const language = state.languages?.some((l) => l.code === picked.targetLanguage) ? picked.targetLanguage : null;
  const [row] = toGlossaryRows([{ source, target: "", target_language: language }]);
  if (!row) return;
  state.glossary.rows.push(row);
  state.glossary.engineError = null;
  markGlossaryEdited();
  showPage("settings");
  renderGlossary();
  const target = el.glossaryRows.querySelector<HTMLInputElement>(`tr[data-row="${row.id}"] [data-field="target"]`);
  target?.scrollIntoView({ block: "center" });
  target?.focus();
}

function removeGlossaryRow(id: number): void {
  const index = state.glossary.rows.findIndex((r) => r.id === id);
  if (index < 0) return;
  state.glossary.rows.splice(index, 1);
  freshGlossaryRows.delete(id);
  state.glossary.engineError = null;
  markGlossaryEdited();
  renderGlossary();
  // Keep the keyboard in the table: the next row's (or the previous row's) remove button.
  const next = state.glossary.rows[Math.min(index, state.glossary.rows.length - 1)];
  const focus = next
    ? el.glossaryRows.querySelector<HTMLButtonElement>(`tr[data-row="${next.id}"] .glossary-actions button`)
    : el.glossaryAdd;
  focus?.focus();
}

function onGlossaryInput(e: Event): void {
  const control = e.target;
  if (!(control instanceof HTMLInputElement || control instanceof HTMLSelectElement)) return;
  const id = Number(control.closest<HTMLElement>("tr[data-row]")?.dataset.row);
  const index = state.glossary.rows.findIndex((r) => r.id === id);
  const row = state.glossary.rows[index];
  const field = control.dataset.field;
  if (!row || (field !== "source" && field !== "target" && field !== "target_language")) return;
  row.entry =
    field === "target_language"
      ? { ...row.entry, target_language: control.value === "" ? null : control.value }
      : { ...row.entry, [field]: control.value };
  freshGlossaryRows.delete(row.id);
  const rejected = state.glossary.engineError?.target;
  if (rejected && (rejected.kind !== "entry" || rejected.index === index)) state.glossary.engineError = null;
  markGlossaryEdited();
  renderGlossaryErrors();
}

async function submitGlossary(): Promise<void> {
  freshGlossaryRows.clear();
  const entries = glossaryEntries();
  const local = validateGlossary(entries, state.languages?.map((l) => l.code) ?? null);
  renderGlossaryErrors();
  const corrections = parseCorrectionsText(state.glossary.corrections);
  if (hasGlossaryErrors(local) || corrections.errors.length > 0) {
    el.glossaryStatus.textContent = t("form.fixFields");
    const invalidRow = el.glossaryRows.querySelector<HTMLElement>('[aria-invalid="true"]');
    (invalidRow ?? el.correctionsInput).focus();
    return;
  }
  const revision = state.glossary.revision;
  state.glossary.saving = true;
  renderGlossaryErrors();
  el.glossaryStatus.textContent = t("common.saving");
  try {
    const { settings: saved, revision: settingsRev } = await api.settingsSet(glossaryPatch({ builtin: state.glossary.builtin, entries, corrections: corrections.entries }));
    const settings = adoptSettings(saved, settingsRev);
    state.glossary.saving = false;
    // Rows edited (or imported) while saving stay as they are, still unsaved.
    if (state.glossary.revision === revision) fillGlossary(settings, true);
    el.glossaryStatus.textContent = sessionActive() ? t("glossary.savedNextStart") : t("glossary.saved");
  } catch (e) {
    state.glossary.saving = false;
    const ce = asCommandError(e);
    const target = ce?.kind === "engine" && ce.code === "invalid_settings" ? glossaryErrorForEnginePath(ce.message) : null;
    if (target && ce?.kind === "engine") {
      state.glossary.engineError = { target, message: ce.message };
      el.glossaryStatus.textContent = t("form.fixFields");
    } else if (ce?.kind === "engine" && ce.message.startsWith("translation.glossary.corrections")) {
      // A correction rule the engine still rejects: show it under the box it came from.
      el.correctionsError.textContent = ce.message;
      el.glossaryStatus.textContent = t("form.fixFields");
    } else {
      el.glossaryStatus.textContent = describeError(e);
    }
    renderGlossaryErrors();
  }
}

/** Writes the table as it is now (saved or not) to `<Documents>/kikitori/transcripts/`. */
async function exportGlossary(): Promise<void> {
  el.glossaryExport.disabled = true;
  try {
    const path = await api.transcriptSave(glossaryFileName(new Date()), glossaryToCsv(glossaryEntries()));
    notify(t("glossary.exported", { path }), "info", [{ label: t("export.openFolder"), run: () => void openOutputFolder("transcripts") }], {
      key: EXPORT_NOTICE,
    });
  } catch (e) {
    reportError("error.exportGlossary", e);
  } finally {
    renderGlossaryErrors();
  }
}

/** Adds the rows of a CSV file to the table (saved with "儲存術語表" like typed rows). */
async function importGlossary(file: File): Promise<void> {
  if (file.size > GLOSSARY_MAX_CSV_BYTES) {
    el.glossaryStatus.textContent = t("glossary.importTooLarge", { size: formatBytes(GLOSSARY_MAX_CSV_BYTES) });
    return;
  }
  let text: string;
  try {
    text = await file.text();
  } catch (e) {
    reportError("error.importGlossary", e);
    return;
  }
  const parsed = parseGlossaryCsv(text);
  const merged = mergeGlossary(glossaryEntries(), parsed.entries);
  state.glossary.rows.push(...toGlossaryRows(merged.entries.slice(state.glossary.rows.length)));
  state.glossary.engineError = null;
  if (merged.added > 0) markGlossaryEdited();
  renderGlossary();
  const parts = [t("glossary.imported", { added: merged.added, skipped: merged.skipped })];
  if (parsed.badLines.length) {
    const shown = parsed.badLines.slice(0, 10).map(String);
    if (parsed.badLines.length > shown.length) shown.push("…");
    parts.push(t("glossary.importBadLines", { lines: joinList(shown) }));
  }
  el.glossaryStatus.textContent = parts.join(" ");
}

// ---------------------------------------------------------------- settings page: languages

const LANGUAGE_FIELDS: readonly LanguageField[] = ["source", "target", "partner"];
const LANGUAGE_PREVIEW_DEBOUNCE_MS = 250;
const languagePreviews = new Latest();
let languagePreviewTimer: number | undefined;

/** Fields of the current mode: media uses source + target, interpret target + partner. */
function languageFieldsFor(mode: Mode): readonly LanguageField[] {
  return mode === "media" ? ["source", "target"] : ["target", "partner"];
}

/** Puts the saved languages into the section unless the user has unsaved edits (or `force`). */
function fillLanguageForm(settings: Settings, force = false): void {
  if (state.langDirty && !force) return;
  state.langForm = languageFormFromSettings(settings, state.mode, el.applyRecommended.checked);
  state.langDirty = false;
  for (const f of LANGUAGE_FIELDS) {
    state.langFilters[f] = "";
    el.langFilters[f].value = "";
  }
  el.languagesStatus.textContent = "";
  showLanguageErrors({});
  renderLanguages();
}

function renderLanguageOptions(field: LanguageField): void {
  const form = state.langForm;
  const select = el.langSelects[field];
  const list = state.languages;
  if (!form || !list) {
    replace(select);
    return;
  }
  const value = form[field];
  const options = filterLanguageOptions(languageOptions(list, field === "source"), state.langFilters[field], value);
  replace(select, ...options.map((o) => h("option", { value: o.value, dir: "auto" }, o.label)));
  select.value = value;
}

function renderLanguages(): void {
  const form = state.langForm;
  const mode = form?.mode ?? state.mode;
  const used = languageFieldsFor(mode);
  for (const f of LANGUAGE_FIELDS) {
    el.langFields[f].hidden = !used.includes(f);
    renderLanguageOptions(f);
    const usable = !!form && !!state.languages && engineReady();
    el.langSelects[f].disabled = !usable;
    el.langFilters[f].disabled = !usable;
  }
  el.languagesModeNote.textContent = mode === "media" ? t("settings.languageModeMedia") : t("settings.languageModeInterpret");
  el.labelTarget.textContent = mode === "media" ? t("settings.target") : t("settings.own");
  el.helpTarget.textContent = mode === "media" ? t("settings.targetHelp") : t("settings.ownHelp");
  const labels: Record<LanguageField, string> = {
    source: t("settings.source"),
    target: el.labelTarget.textContent,
    partner: t("settings.partner"),
  };
  for (const f of LANGUAGE_FIELDS) el.langFilters[f].setAttribute("aria-label", t("settings.languageFilterLabel", { field: labels[f] }));
  renderLanguagePreview();
}

function showLanguageErrors(errors: LanguageErrors): void {
  for (const f of LANGUAGE_FIELDS) {
    const message = errors[f] ?? "";
    const slot = el.languagesForm.querySelector<HTMLElement>(`[data-error-for="${f}"]`);
    if (slot) slot.textContent = message;
    el.langFields[f].classList.toggle("has-error", message !== "");
    el.langSelects[f].setAttribute("aria-invalid", String(message !== ""));
  }
}

function renderLanguagePreview(): void {
  const v = state.langPreview;
  switch (v.state) {
    case "unavailable":
      replace(el.languagePreview, h("p", {}, t("settings.languagesUnavailable")));
      break;
    case "local_errors":
      replace(el.languagePreview);
      break;
    case "checking":
      replace(el.languagePreview, h("p", {}, t("preview.checking")));
      break;
    case "error":
      replace(el.languagePreview, h("p", { class: "is-error" }, t("preview.unavailable", { reason: v.message })));
      break;
    case "ready": {
      const rows = previewRows(v.preview);
      const notes = previewNotes(v.preview);
      replace(
        el.languagePreview,
        h("h3", {}, t("preview.title")),
        h(
          "dl",
          { class: "facts" },
          ...rows.flatMap((r) => [
            h("dt", {}, r.label),
            h("dd", { "data-tone": r.tone }, r.model ? withNote(r.model, r.status) : r.status),
          ]),
        ),
        h("p", {}, previewSummary(v.preview)),
        notes.length ? h("ul", { class: "notes" }, ...notes.map((m) => h("li", {}, formatEngineMessage(m)))) : null,
      );
      break;
    }
  }
  const checking = v.state === "checking" && !!state.langForm?.applyRecommended;
  el.languagesSave.disabled =
    !engineReady() || !state.languages || !state.langForm || v.state === "local_errors" || checking;
}

/** Local checks now, then (debounced) `settings.preview_languages` for the current choice. */
function scheduleLanguagePreview(delay = LANGUAGE_PREVIEW_DEBOUNCE_MS): void {
  window.clearTimeout(languagePreviewTimer);
  const form = state.langForm;
  if (!form || !state.languages || !engineReady()) {
    languagePreviews.invalidate();
    state.langPreview = { state: "unavailable" };
    renderLanguagePreview();
    return;
  }
  const errors = validateLanguageForm(form, state.languages);
  showLanguageErrors(errors);
  if (Object.keys(errors).length > 0) {
    languagePreviews.invalidate();
    state.langPreview = { state: "local_errors" };
  } else {
    state.langPreview = { state: "checking" };
    languagePreviewTimer = window.setTimeout(() => void runLanguagePreview(form), delay);
  }
  renderLanguagePreview();
}

/** Maps the engine's `invalid_settings` path to the language field it is about. */
function languageFieldForEnginePath(message: string): LanguageField | null {
  const path = message.split(":", 1)[0]?.trim();
  if (path === "source_language") return "source";
  if (path === "target_language") return "target";
  if (path === "partner_language") return "partner";
  return null;
}

async function runLanguagePreview(form: LanguageForm): Promise<void> {
  const token = languagePreviews.next();
  let next: LanguagePreviewView;
  try {
    next = { state: "ready", preview: await api.previewLanguages(previewRequest(form)) };
  } catch (e) {
    const ce = asCommandError(e);
    const field = ce?.kind === "engine" && ce.code === "invalid_settings" ? languageFieldForEnginePath(ce.message) : null;
    if (field && ce?.kind === "engine" && languagePreviews.isCurrent(token)) {
      showLanguageErrors({ [field]: t("form.engineRejected", { message: ce.message }) });
      next = { state: "local_errors" };
    } else {
      console.error("settings.preview_languages failed", e);
      next = { state: "error", message: describeError(e) };
    }
  }
  // An answer for an earlier choice must not replace the one for the current choice.
  if (!languagePreviews.isCurrent(token)) return;
  state.langPreview = next;
  renderLanguagePreview();
}

function chooseLanguage(field: LanguageField, value: string): void {
  if (!state.langForm) return;
  state.langForm = { ...state.langForm, [field]: value };
  state.langDirty = true;
  el.languagesStatus.textContent = "";
  scheduleLanguagePreview();
}

async function loadLanguages(): Promise<void> {
  try {
    state.languages = await api.languagesList();
  } catch (e) {
    reportError("error.readLanguages", e);
  }
  renderLanguages();
  // The glossary's language menus list the engine's languages.
  renderGlossary();
  scheduleLanguagePreview(0);
}

async function submitLanguages(): Promise<void> {
  const form = state.langForm;
  const list = state.languages;
  if (!form || !list) return;
  const errors = validateLanguageForm(form, list);
  showLanguageErrors(errors);
  if (Object.keys(errors).length > 0) {
    el.languagesStatus.textContent = t("form.fixFields");
    return;
  }
  const preview = state.langPreview.state === "ready" ? state.langPreview.preview : null;
  const save = languagesSave(form, list, preview);
  el.languagesSave.disabled = true;
  el.languagesStatus.textContent = t("common.saving");
  try {
    const { settings: saved, revision } = await api.settingsSet(save.patch);
    const settings = adoptSettings(saved, revision);
    state.langDirty = false;
    if (save.withModels) {
      // A deliberate model choice: never auto-applied over, and the combination
      // form and the "better combination" offer follow the new settings.
      state.userChoseModels = true;
      forgetPreview();
      fillCombo(settings, true);
      renderModels();
    }
    fillLanguageForm(settings, true);
    renderModeNotes();
    el.languagesStatus.textContent = sessionActive()
      ? t("settings.languagesSavedNextStart")
      : save.withModels
        ? t("settings.languagesSavedWithModels")
        : form.applyRecommended && !previewMatches(preview, form)
          ? t("settings.languagesSavedNoPreview")
          : t("settings.languagesSaved");
    // `differs` and the download sizes changed with the saved settings.
    scheduleLanguagePreview(0);
  } catch (e) {
    const ce = asCommandError(e);
    const field = ce?.kind === "engine" && ce.code === "invalid_settings" ? languageFieldForEnginePath(ce.message) : null;
    if (field && ce?.kind === "engine") {
      showLanguageErrors({ [field]: t("form.engineRejected", { message: ce.message }) });
      el.languagesStatus.textContent = t("form.fixFields");
    } else {
      el.languagesStatus.textContent = describeError(e);
    }
  } finally {
    renderLanguagePreview();
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

/**
 * "複製診斷資訊": builds the redacted report (lib/diagnostics.ts) from what this
 * window already knows — no engine call, so the click's user activation is
 * still valid for the clipboard. If the clipboard is refused, the text is
 * shown for copying by hand.
 */
async function copyDiagnostics(): Promise<void> {
  const text = buildDiagnostics({
    appVersion: state.appVersion,
    uiLocale: getLocale(),
    userAgent: navigator.userAgent,
    bridgePhase: state.bridge?.phase ?? null,
    bridgeDetail: state.bridge?.detail ?? null,
    engine: state.bridge?.info ?? null,
    sessionState: state.sessionState,
    hardware: state.hardware?.hardware ?? null,
    settings: state.settings,
    settingsPath: state.settingsPath,
    recentLog: recentLog.entries(),
    now: new Date(),
  });
  try {
    await navigator.clipboard.writeText(text);
    el.diagnosticsText.hidden = true;
    el.diagnosticsStatus.textContent = t("about.diagnosticsCopied");
  } catch (e) {
    console.error("clipboard write failed:", e);
    el.diagnosticsText.value = text;
    el.diagnosticsText.hidden = false;
    el.diagnosticsText.select();
    el.diagnosticsStatus.textContent = t("about.diagnosticsManual");
  }
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
    const { settings, revision } = await api.settingsSet(uiLocalePatch(pref));
    adoptSettings(settings, revision);
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
  renderTranslationSwitches();
  renderFurigana();
  renderSpeakers();
  renderLanguages();
  renderStartDownloads();
  renderHardware();
  renderModels();
  renderCheck();
  renderSettingsPath();
  renderAbout();
  renderMissingNotice();
  renderOutputPaths();
  renderTranslationGuide();
  renderGlossary();
  renderMetrics();
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
  // The diagnostics report and the guide use the hardware summary; detect it once.
  if (page === "settings" && !state.hardware && engineReady()) void detectHardwareQuietly();
}

async function detectHardwareQuietly(): Promise<void> {
  try {
    state.hardware = await api.hardwareDetect();
    renderHardware();
    renderTranslationGuide();
  } catch (e) {
    console.error("hardware.detect failed:", e);
  }
}

/**
 * `models.list` at engine start, so an unreachable translation server is noticed
 * (and the guide shown) before the user presses Start. Failures are only logged:
 * the models page reports them when opened.
 */
async function loadModelsQuietly(): Promise<void> {
  if (state.models || state.page === "models") return;
  try {
    state.models = await api.modelsList();
    renderModels();
    renderMissingNotice();
  } catch (e) {
    console.error("models.list failed:", e);
  }
}

// ---------------------------------------------------------------- engine data

async function loadEngineData(): Promise<void> {
  let isNew = false;
  try {
    // A (re)started engine counts its settings revisions from the beginning.
    settingsRevision.reset();
    const { settings, path, warnings, isNew: fresh, revision } = await api.settingsGet();
    isNew = fresh;
    if (warnings.length) {
      notify(tp("notice.settingsFieldsUnreadable", warnings.length, { fields: joinList(warnings) }), "bad");
    }
    adoptSettings(settings, revision);
    await syncUiLocale(settings.ui.locale);
    state.settingsPath = path;
    state.mode = settings.mode;
    fillForm(settings);
    fillCombo(settings);
    fillLanguageForm(settings);
    fillGlossary(settings);
    fillSessionInfo(settings);
    renderSettingsPath();
    renderTranslationSwitches();
    renderFuriganaSwitch();
    renderSpeakerSelect();
    await pushOverlayStyle();
    await pushOverlayShowTranslation();
    await pushObsOutput();
  } catch (e) {
    reportError("error.readSettings", e);
  }
  await loadLanguages();
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
  void loadModelsQuietly();
}

function onBridge(status: BridgeStatus): void {
  const wasReady = engineReady();
  state.bridge = status;
  if (status.phase !== "ready") {
    // A restarted engine has no session and no downloads in flight.
    setSessionState("idle", true);
    for (const [id, d] of state.downloads) if (d.state === "downloading") state.downloads.delete(id);
    if (state.furiganaDownload?.state === "downloading") state.furiganaDownload = null;
    state.furiganaPendingEnable = false;
    if (state.speakerDownload?.state === "downloading") state.speakerDownload = null;
    state.speakersPending = null;
  }
  renderStatus();
  renderCheck();
  renderTranslationSwitches();
  renderFurigana();
  renderSpeakers();
  renderGlossaryErrors();
  if (status.phase !== "ready") {
    // A restarted engine has no downloads for the start in flight any more.
    startFlow.cancel();
    scheduleLanguagePreview(0);
  }
  if (status.phase === "ready" && !wasReady) void loadEngineData();
  if (status.phase === "failed") {
    recentLog.push("bridge", `engine failed: ${status.detail ?? "unknown"}`);
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

/**
 * The release notes as a collapsed "see what's new" section, or null when there
 * are none. Built with text nodes only: the notes come from the network.
 */
function updateNotesDetail(notes: string | null, version: string): HTMLElement | null {
  const blocks = updateNotesBlocks(notes, version);
  if (!blocks.length) return null;
  const render = (b: NotesBlock): HTMLElement =>
    b.kind === "heading"
      ? h("p", { class: "update-notes-heading" }, b.text)
      : b.kind === "list"
        ? h("ul", {}, ...b.items.map((item) => h("li", {}, item)))
        : h("p", {}, b.text);
  return h(
    "details",
    { class: "update-notes" },
    h("summary", {}, t("update.showNotes")),
    h("div", { class: "update-notes-body", tabindex: 0 }, ...blocks.map(render)),
  );
}

function updateUi(): UpdateUi {
  let notice: HTMLElement | null = null;
  return {
    offer(version, notes, install) {
      const detail = updateNotesDetail(notes, version) ?? undefined;
      notice = notify(
        t("update.available", { version }),
        "update",
        [
          {
            label: t("update.install"),
            run: () => {
              notice?.remove();
              notice = notify(t("update.downloading"), "update");
              void install();
            },
          },
        ],
        { detail },
      );
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

/**
 * The notices stick to the top of the scrolling content and would cover whatever
 * focus or scrolling brings to the top (the settings form's first row). Their
 * height becomes the scroller's scroll padding (`--notices-h` in main.css).
 */
function keepContentBelowNotices(): void {
  const scroller = el.notices.closest<HTMLElement>(".content");
  if (!scroller) return;
  new ResizeObserver(() => {
    const height = el.notices.childElementCount > 0 ? el.notices.getBoundingClientRect().height : 0;
    scroller.style.setProperty("--notices-h", `${Math.ceil(height)}px`);
  }).observe(el.notices);
}

function wire(): void {
  keepContentBelowNotices();
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
  el.resetOverlay.addEventListener("click", () => void resetOverlayPosition());
  el.overlayClick.addEventListener("change", async () => {
    try {
      state.overlay = await api.overlaySetClickThrough(el.overlayClick.checked);
    } catch (e) {
      reportError("error.toggleClickThrough", e);
    }
    renderOverlay();
  });
  for (const input of [el.showTranslation, el.settingsShowTranslation]) {
    input.addEventListener("change", () => void changeShowTranslation(input.checked));
  }
  el.furiganaSwitch.addEventListener("change", () => void changeFurigana(el.furiganaSwitch.checked));
  el.speakersSelect.addEventListener("change", () => {
    const choice = parseChoice(el.speakersSelect.value);
    if (choice) void changeSpeakers(choice);
  });
  // Rows are reused across redraws, so the chip clicks are handled on the list.
  el.transcript.addEventListener("click", (e) => {
    const chip = (e.target as HTMLElement).closest<HTMLButtonElement>("button.utt-voice");
    const voice = Number(chip?.dataset.voice);
    if (chip && Number.isInteger(voice) && voice >= 1) openVoiceRename(chip, voice);
  });
  el.voiceRename.addEventListener("submit", (e) => {
    e.preventDefault();
    commitVoiceRename();
  });
  el.voiceRenameInput.addEventListener("keydown", (e) => {
    if (e.key === "Escape") closeVoiceRename();
  });
  el.voiceRenameInput.addEventListener("blur", () => commitVoiceRename());
  el.startDownloadsRetry.addEventListener("click", () => startFlow.retry());
  el.startDownloadsCancel.addEventListener("click", () => startFlow.cancel());
  for (const f of LANGUAGE_FIELDS) {
    el.langSelects[f].addEventListener("change", () => chooseLanguage(f, el.langSelects[f].value));
    el.langFilters[f].addEventListener("input", () => {
      state.langFilters[f] = el.langFilters[f].value;
      renderLanguageOptions(f);
    });
    // Enter in the filter picks the first match instead of submitting the form.
    el.langFilters[f].addEventListener("keydown", (e) => {
      if (e.key !== "Enter") return;
      e.preventDefault();
      const current = state.langForm?.[f];
      const first = Array.from(el.langSelects[f].options).find((o) => o.value !== current) ?? null;
      if (first && state.langFilters[f].trim() !== "") {
        chooseLanguage(f, first.value);
        renderLanguageOptions(f);
      }
    });
  }
  el.applyRecommended.addEventListener("change", () => {
    if (state.langForm) state.langForm = { ...state.langForm, applyRecommended: el.applyRecommended.checked };
    renderLanguagePreview();
  });
  el.languagesForm.addEventListener("submit", (e) => {
    e.preventDefault();
    void submitLanguages();
  });
  el.clear.addEventListener("click", () => {
    state.subtitles = emptySubtitles(state.subtitles.sessionId);
    // Export follows the log: what was cleared is not exported later.
    state.exportLog = emptySubtitles(state.exportLog.sessionId);
    renderTranscript();
  });
  el.openTranscripts.addEventListener("click", () => void openOutputFolder("transcripts"));
  el.openObs.addEventListener("click", () => void openOutputFolder("obs"));
  el.guideInstall.addEventListener("click", () => void openOllamaDownload());
  el.guideOriginal.addEventListener("click", () => void changeShowTranslation(false));
  el.guideRecheck.addEventListener("click", () => void recheckTranslationServer());
  el.copyDiagnostics.addEventListener("click", () => void copyDiagnostics());
  el.glossaryRows.addEventListener("input", onGlossaryInput);
  el.correctionsInput.addEventListener("input", () => {
    state.glossary.corrections = el.correctionsInput.value;
    markGlossaryEdited();
    renderGlossaryErrors();
  });
  el.addToGlossary.addEventListener("click", addSelectionToGlossary);
  // Pressing the button must not clear the selection it acts on (some WebViews do on mousedown).
  el.addToGlossary.addEventListener("mousedown", (e) => e.preventDefault());
  el.sessionInfo.addEventListener("input", onSessionInfoInput);
  el.sessionInfo.addEventListener("blur", () => void saveSessionInfo());
  document.addEventListener("selectionchange", onTranscriptSelection);
  el.glossaryBuiltin.addEventListener("change", () => {
    state.glossary.builtin = el.glossaryBuiltin.checked;
    if (state.glossary.engineError?.target.kind === "builtin") state.glossary.engineError = null;
    markGlossaryEdited();
    renderGlossaryErrors();
  });
  el.glossaryAdd.addEventListener("click", addGlossaryRow);
  el.glossaryImport.addEventListener("click", () => el.glossaryFile.click());
  el.glossaryFile.addEventListener("change", () => {
    const file = el.glossaryFile.files?.[0];
    // Cleared so picking the same file again (after fixing it) imports it again.
    el.glossaryFile.value = "";
    if (file) void importGlossary(file);
  });
  el.glossaryExport.addEventListener("click", () => void exportGlossary());
  el.glossaryReset.addEventListener("click", () => {
    if (state.settings) fillGlossary(state.settings, true);
  });
  el.glossaryForm.addEventListener("submit", (e) => {
    e.preventDefault();
    void submitGlossary();
  });
  el.modelsRefresh.addEventListener("click", () => void loadModels().then(checkRecommendation));
  for (const field of TRANSLATION_FIELDS) {
    translationSelect(field).addEventListener("input", () => onTranslationPicked(field));
  }
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
      setSessionState(s.state, true);
      if (s.state === "error") recentLog.push("status", `${s.code ?? "error"}: ${s.detail ?? ""}`);
      if (s.state === "error" && s.code === "translation_model_missing" && s.missing_translation_models.length) {
        // Pull them on the user's Ollama, then start again (lib/start-downloads.ts).
        startFlow.begin(
          s.missing_translation_models.map((model) => ({ kind: "translation", model, sizeBytes: translationSize(model) })),
        );
      } else if (s.state === "error" && s.code === "model_not_downloaded") {
        // Download exactly the missing models and start again; fall back to the generic message.
        void downloadMissingThenStart().then((found) => {
          if (!found) notify(describeStatusError(s.code, s.detail), "bad");
        });
      } else if (s.state === "error") {
        notify(describeStatusError(s.code, s.detail), "bad");
      }
      renderStatus();
    }),
    events.subtitle((ev) => {
      state.subtitles = applySubtitle(state.subtitles, ev, TRANSCRIPT_MAX_LINES);
      state.exportLog = applySubtitle(state.exportLog, ev, EXPORT_MAX_LINES);
      renderTranscript();
    }),
    events.download((p) => {
      // The furigana dictionary has its own switch and block; it is not an ASR model.
      if (isFuriganaDownload(p)) {
        onFuriganaDownload(p);
        return;
      }
      // So does the speaker model (speaker-labels.md).
      if (isSpeakerDownload(p)) {
        onSpeakerDownload(p);
        return;
      }
      startFlow.onProgress(p);
      // Translation pulls only matter to the start flow; the models page lists
      // ASR models and the GPU runtime by name.
      if (p.kind === "translation") {
        if (p.state === "done" && state.page === "models") void loadModels();
        return;
      }
      // Downloads for a start are shown in their own panel, not as notices too.
      const quiet = startFlow.tracks("asr", p.model);
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
          if (!quiet) notify(t("download.done", { model: p.model }), "info", [], { autoDismiss: true });
          void loadModels();
        }
      } else if (p.state === "error" && !quiet) {
        const what = p.model === GPU_RUNTIME_ID ? t("gpu.name") : p.model;
        notify(t("download.failed", { what, error: p.error ?? t("common.unknownReason") }), "bad");
      }
      if (state.page === "models") renderModels();
      renderMissingNotice();
    }),
    events.log((l) => {
      if (l.level !== "debug") recentLog.push(l.level, l.message);
      if (l.level === "warning" || l.level === "error") {
        console.warn(`engine ${l.level}: ${l.message}`);
        const translationLog = isTranslationServerLog(l.message);
        // A translation-server failure the localized guide already explains is not repeated as raw engine text.
        if (translationLog && currentGuide() !== null) return;
        notify(
          t("engine.log", { message: l.message }),
          l.level === "error" ? "bad" : "info",
          [],
          translationLog ? { key: TRANSLATION_LOG_NOTICE } : {},
        );
      }
    }),
    events.metrics(onMetrics),
    events.overlay((o) => {
      state.overlay = o;
      renderOverlay();
    }),
    events.shortcut(onShortcut),
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
    state.appVersion = await getVersion();
    el.aboutVersion.textContent = `kikitori ${state.appVersion}`;
  } catch (e) {
    console.error("getVersion failed:", e);
  }
  try {
    state.output = await api.outputInfo();
  } catch (e) {
    console.error("output_info failed:", e);
  }
  renderOutputPaths();
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
