/**
 * Main window controller.
 *
 * Role: wires the static markup in index.html to the engine (through `lib/api`)
 * and keeps it in sync with engine/bridge/overlay events. Pure decisions
 * (subtitle ordering, validation, wording, combination-check rules) live in
 * `lib/`; this file only holds UI state and renders it.
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
  languageName,
  languageShort,
  osName,
  percent,
  regionNames,
  speedTierLabel,
  threadsLabel,
  tierLabel,
} from "../lib/format";
import {
  COMPUTE_TYPES,
  combinationFieldForEnginePath,
  combinationFormFromSettings,
  combinationPatch,
  devicePatch,
  formFieldForEnginePath,
  formFromSettings,
  settingsPatchFromForm,
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
  check: { state: "unavailable", message: "引擎尚未就緒" },
  comboDirty: false,
  gpuLicenseAccepted: false,
  about: null,
};

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

/** Shows a dismissible message at the top of the content area; returns it. */
function notify(text: string, tone: "info" | "bad" | "update" = "info", actions: NoticeAction[] = []): HTMLElement {
  for (const existing of Array.from(el.notices.children)) {
    if (existing.getAttribute("data-text") === text) existing.remove();
  }
  const notice = h("div", { class: "notice", "data-tone": tone, "data-text": text, role: tone === "bad" ? "alert" : "status" });
  const body = h("p", {}, text);
  const buttons = h("div", { class: "notice-actions" });
  for (const action of actions) {
    const b = h("button", { type: "button", class: "ghost" }, action.label);
    b.addEventListener("click", action.run);
    buttons.appendChild(b);
  }
  const close = h("button", { type: "button", class: "ghost", "aria-label": "關閉這則訊息" }, "關閉");
  close.addEventListener("click", () => notice.remove());
  buttons.appendChild(close);
  replace(notice, body, buttons);
  el.notices.prepend(notice);
  while (el.notices.children.length > 3) el.notices.lastElementChild?.remove();
  return notice;
}

function reportError(context: string, e: unknown): void {
  console.error(context, e);
  notify(`${context}：${describeError(e)}`, "bad");
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
  el.start.textContent = sessionActive() ? "停止" : "開始";
  for (const button of el.modes) {
    const mode = button.dataset.mode as Mode;
    button.setAttribute("aria-checked", String(mode === state.mode));
    button.disabled = sessionActive() || state.busy;
  }
  el.device.disabled = sessionActive() || state.busy || !ready;

  const info = state.bridge?.info;
  el.aboutEngine.textContent = info
    ? `引擎 ${info.version}，協定第 ${info.protocol_version} 版${info.fake ? "，目前使用假引擎（開發模式）" : ""}。`
    : "引擎尚未連線。";
}

function renderModeNotes(): void {
  const partner = state.settings?.partner_language ?? "ja";
  if (el.modeNotes.interpret) el.modeNotes.interpret.textContent = `中文與${languageName(partner)}互譯`;
}

function renderDevices(): void {
  const kind = state.mode === "media" ? "loopback" : "microphone";
  const list = state.devices.filter((d) => d.kind === kind);
  const selected = state.mode === "media" ? state.settings?.media_device_id : state.settings?.interpret_device_id;
  const fallback = list.find((d) => d.is_default);
  const options = [
    h("option", { value: "" }, fallback ? `預設：${fallback.name}` : "系統預設"),
    ...list.map((d) => h("option", { value: d.id }, d.name)),
  ];
  replace(el.device, ...options);
  const known = list.some((d) => d.id === selected);
  el.device.value = selected && known ? selected : "";
  el.deviceHint.textContent =
    selected && !known
      ? "先前選擇的裝置目前找不到，會改用系統預設。"
      : state.mode === "media"
        ? "影片翻譯會擷取電腦正在播放的聲音。"
        : "即時口譯會使用麥克風。";
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
    reportError("無法記住模式", e);
  }
}

async function setDevice(deviceId: string | null): Promise<void> {
  try {
    const { settings } = await api.settingsSet(devicePatch(state.mode, deviceId));
    state.settings = settings;
    renderDevices();
  } catch (e) {
    reportError("無法記住聲音來源", e);
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
    reportError(sessionActive() ? "無法停止" : "無法開始", e);
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
  lang.title = `${languageName(line.sourceLanguage)}譯成${languageName(line.targetLanguage)}`;
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
  const keys = o.shortcut.replace("CommandOrControl", "Ctrl（macOS 為 ⌘）");
  el.shortcutNote.textContent = o.shortcut_registered
    ? `開啟滑鼠穿透後，點擊會穿過字幕視窗。按 ${keys} 可隨時切換。`
    : `快速鍵 ${keys} 已被其他程式占用，請用這裡的開關切換滑鼠穿透。`;
}

async function setOverlayVisible(visible: boolean): Promise<void> {
  try {
    state.overlay = await api.overlaySetVisible(visible);
  } catch (e) {
    reportError(visible ? "無法顯示字幕視窗" : "無法隱藏字幕視窗", e);
  }
  renderOverlay();
}

// ---------------------------------------------------------------- models page: hardware

const CUBLAS_SOURCE: Record<string, string> = {
  runtime: "kikitori 下載的 GPU 加速元件",
  configured: "自訂組合指定的資料夾",
  system: "系統安裝的 CUDA",
};

function renderHardware(): void {
  const hw = state.hardware;
  if (!hw) {
    replace(el.hardware, h("dt", {}, "狀態"), h("dd", {}, "偵測中"));
    el.hardwareErrors.hidden = true;
    replace(el.recommendation, h("p", { class: "block-lede" }, "偵測完成後會在這裡列出建議。"));
    return;
  }
  const x = hw.hardware;
  const cores =
    x.cpu.physical_cores && x.cpu.logical_cores
      ? `${x.cpu.physical_cores} 核心、${x.cpu.logical_cores} 執行緒`
      : x.cpu_count
        ? `${x.cpu_count} 個執行緒`
        : "核心數未知";
  const gpus = x.apple_silicon
    ? "Apple Silicon（與處理器共用記憶體）"
    : x.gpus.length
      ? x.gpus
          .map((g) => {
            const extra = [
              `顯示記憶體 ${formatMiB(g.memory_total_mb)}`,
              g.driver_version ? `驅動 ${g.driver_version}` : null,
              g.compute_capability ? `運算能力 ${g.compute_capability}` : null,
            ].filter(Boolean);
            return `${g.name}（${extra.join("，")}）`;
          })
          .join("、")
      : "沒有偵測到 NVIDIA 顯示卡";
  const cublas = !x.cublas.supported_platform
    ? "這個平台不使用"
    : x.cublas.available
      ? `可以使用（${CUBLAS_SOURCE[x.cublas.source ?? ""] ?? x.cublas.source ?? "來源不明"}）`
      : "未安裝";
  const ts = x.translation_server;
  const server = !ts
    ? "未偵測"
    : ts.reachable
      ? `${ts.base_url}（${ts.is_local ? "本機" : "遠端"}，${ts.models === null ? "模型清單未知" : `${ts.models.length} 個模型`}）`
      : `${ts.base_url}（無法連線${ts.error ? `：${ts.error}` : ""}）`;
  const memory =
    x.memory.total_bytes === null
      ? "未知"
      : x.memory.available_bytes === null
        ? formatBytes(x.memory.total_bytes)
        : `${formatBytes(x.memory.total_bytes)}（目前可用 ${formatBytes(x.memory.available_bytes)}）`;
  replace(
    el.hardware,
    h("dt", {}, "作業系統"),
    h("dd", {}, x.os_version.startsWith(osName(x.os)) ? x.os_version : `${osName(x.os)} ${x.os_version}`.trim()),
    h("dt", {}, "處理器"),
    h("dd", {}, `${x.cpu.model ?? x.machine ?? "未知"}，${cores}`),
    h("dt", {}, "記憶體"),
    h("dd", {}, memory),
    h("dt", {}, "顯示卡"),
    h("dd", {}, gpus),
    h("dt", {}, "cuBLAS（GPU 辨識）"),
    h("dd", {}, cublas),
    h("dt", {}, "翻譯服務"),
    h("dd", {}, server),
  );
  el.hardwareErrors.hidden = x.errors.length === 0;
  replace(el.hardwareErrors, ...x.errors.map((e) => h("li", { class: "warning" }, `偵測不完整：${e}`)));
  renderRecommendation();
}

function describeChoice(c: AsrChoice): string {
  const parts = [deviceLabel(c.device), computeTypeLabel(c.compute_type)];
  if (c.device !== "cuda") parts.push(threadsLabel(c.cpu_threads));
  return `${c.model}（${parts.join("，")}）`;
}

function renderRecommendation(): void {
  const rec = state.hardware?.recommendation;
  if (!rec) return;
  const notLocal = "無法推薦（翻譯服務不在本機），請在自訂組合選擇";
  const dl = h(
    "dl",
    { class: "facts" },
    h("dt", {}, "硬體等級"),
    h("dd", {}, tierLabel(rec.tier)),
    h("dt", {}, "即時字幕的辨識"),
    h("dd", {}, describeChoice(rec.fast_asr)),
    h("dt", {}, "完整翻譯的辨識"),
    h("dd", {}, describeChoice(rec.final_asr)),
    h("dt", {}, "即時字幕的翻譯"),
    h("dd", {}, rec.translation_fast ?? notLocal),
    h("dt", {}, "完整翻譯的翻譯"),
    h("dd", {}, rec.translation_final ?? notLocal),
    rec.translation_budget_gb === null ? null : h("dt", {}, "可給翻譯模型的記憶體"),
    rec.translation_budget_gb === null ? null : h("dd", {}, `約 ${rec.translation_budget_gb.toFixed(1)} GB`),
  );
  const notes = h(
    "ul",
    { class: "notes" },
    ...rec.reasons.map((r) => h("li", {}, r)),
    ...rec.warnings.map((w) => h("li", { class: "warning" }, w)),
    rec.gpu_runtime_missing
      ? h("li", {}, "下載下方的「GPU 加速元件」後，建議組合會改用顯示卡。")
      : null,
  );
  const apply = h("button", { type: "button", class: "primary", id: "apply-recommendation" }, "套用建議組合");
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
      h("span", { class: "form-status" }, "會寫入辨識模型、運算裝置、精度、執行緒與翻譯模型。"),
    ),
  );
}

async function applyRecommendation(button: HTMLButtonElement): Promise<void> {
  button.disabled = true;
  try {
    const result = await api.applyRecommendation();
    state.settings = result.settings;
    if (state.hardware) state.hardware = { ...state.hardware, recommendation: result.recommendation };
    if (result.warnings.length) console.warn("settings fields replaced by defaults:", result.warnings);
    const r = result.recommendation;
    notify(
      `已套用建議組合：即時字幕 ${r.fast_asr.model}、完整翻譯 ${r.final_asr.model}（${deviceLabel(r.fast_asr.device)}）。` +
        (sessionActive() ? "下一次開始時生效。" : ""),
    );
    fillCombo(result.settings, true);
    renderHardware();
    renderModels();
    scheduleCheck(0);
  } catch (e) {
    reportError("無法套用建議組合", e);
  } finally {
    button.disabled = !engineReady();
  }
}

// ---------------------------------------------------------------- models page: GPU runtime

function renderGpuRuntime(): void {
  const rt = state.models?.gpu_runtime;
  if (!rt) {
    replace(el.gpuRuntime, h("p", { class: "gpu-state rail-hint" }, "讀取中"));
    return;
  }
  if (!rt.supported) {
    replace(
      el.gpuRuntime,
      h(
        "p",
        { class: "gpu-state" },
        "這台電腦的平台用不到這個元件：GPU 加速只支援 Windows 與 Linux 的 x86-64 搭配 NVIDIA 顯示卡，其他平台一律用 CPU 辨識。",
      ),
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
        h("progress", { max: 100, value: pct, "aria-label": "GPU 加速元件下載進度" }),
        h(
          "span",
          {},
          finishing
            ? "下載完成，正在驗證與解壓（約數秒）"
            : `${pct}%，${formatBytes(progress.downloaded_bytes)}／${formatBytes(progress.total_bytes)}`,
        ),
      ),
    );
    return;
  }
  if (rt.installed) {
    const what = [rt.package ?? "cuBLAS", rt.version].filter(Boolean).join(" ");
    replace(
      el.gpuRuntime,
      h("p", { class: "gpu-state" }, `已安裝 ${what}${rt.path ? `，位置 ${rt.path}` : ""}。`),
      rt.cublas_available
        ? null
        : h("p", { class: "gpu-state warning" }, "已安裝但目前偵測不到可用的 cuBLAS。請重新啟動引擎後再按「重新偵測」。"),
    );
    return;
  }

  const hw = state.hardware;
  const size = rt.download_bytes ? `約 ${formatBytes(rt.download_bytes)}` : "數百 MB";
  const pkg = [rt.package ?? "nvidia-cublas-cu12", rt.version].filter(Boolean).join(" ");
  const situation = hw?.recommendation.gpu_runtime_missing
    ? "這台電腦的 NVIDIA 顯示卡可以加速辨識，但缺少這個元件，目前改用 CPU。"
    : hw && hw.hardware.gpus.length === 0
      ? "沒有偵測到 NVIDIA 顯示卡；沒有顯示卡時不需要下載。"
      : null;
  const licenseName = rt.license ?? "NVIDIA CUDA EULA";
  const read = h("button", { type: "button", class: "ghost" }, "閱讀授權條款");
  read.addEventListener("click", () => void openEula());
  const consent = h("input", { type: "checkbox", id: "gpu-eula-consent" });
  consent.checked = state.gpuLicenseAccepted;
  const download = h("button", { type: "button", class: "primary" }, "下載 GPU 加速元件");
  download.disabled = !state.gpuLicenseAccepted || !engineReady();
  consent.addEventListener("change", () => {
    state.gpuLicenseAccepted = consent.checked;
    download.disabled = !consent.checked || !engineReady();
  });
  download.addEventListener("click", () => void downloadGpuRuntime());
  replace(
    el.gpuRuntime,
    situation ? h("p", { class: "gpu-state" }, situation) : null,
    h(
      "p",
      { class: "gpu-state" },
      `這會從 PyPI 下載 NVIDIA cuBLAS（${pkg}，${size}），使用即表示同意 NVIDIA CUDA EULA（${licenseName}）。`,
    ),
    rt.license_url ? h("p", { class: "gpu-state eula-url url" }, rt.license_url) : null,
    progress?.state === "error"
      ? h("p", { class: "field-error" }, `上次下載失敗：${progress.error ?? "未知原因"}`)
      : null,
    h(
      "label",
      { class: "consent", for: "gpu-eula-consent" },
      consent,
      h("span", {}, "我同意 NVIDIA CUDA EULA"),
    ),
    h("div", { class: "gpu-actions" }, download, canOpenLicenseUrl(rt.license_url) ? read : null),
  );
}

async function openEula(): Promise<void> {
  try {
    await api.openNvidiaEula();
  } catch (e) {
    reportError("無法開啟授權條款，請複製網址到瀏覽器開啟", e);
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
    reportError("無法下載 GPU 加速元件", e);
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
    notify("GPU 加速元件已安裝。正在重新啟動引擎以啟用 GPU。");
    void restartEngine();
    return;
  }
  notify(
    "GPU 加速元件已安裝。目前有工作在進行，所以沒有自動重新啟動引擎；完成後請重新啟動引擎，才會使用 GPU。",
    "update",
    [{ label: "重新啟動引擎", run: () => void restartEngine() }],
  );
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
      h("progress", { max: 100, value: pct, "aria-label": `${model.id} 下載進度` }),
      h("span", {}, `${pct}%，${formatBytes(progress.downloaded_bytes)}`),
    );
  }
  if (model.downloaded) return h("span", { class: "ok" }, "已下載");
  const wrap = h("div", {});
  if (progress?.state === "error") {
    wrap.append(h("p", { class: "field-error" }, `下載失敗：${progress.error ?? "未知原因"}`));
  }
  const button = h("button", { type: "button", class: "ghost" }, progress?.state === "error" ? "重新下載" : "下載");
  button.addEventListener("click", () => void downloadModel(model.id));
  wrap.append(button);
  return wrap;
}

function roleNames(roles: readonly string[]): string {
  return roles.map((r) => (r === "fast" ? "即時" : "完整")).join("、");
}

function memoryCell(cpu: number | null, gpu: number | null): string {
  const parts = [cpu === null ? null : `CPU ${formatMiB(cpu)}`, gpu === null ? null : `GPU ${formatMiB(gpu)}`];
  const known = parts.filter(Boolean);
  return known.length ? known.join("／") : "未知";
}

function renderModels(): void {
  const m = state.models;
  const s = state.settings;
  renderGpuRuntime();
  if (!m) {
    replace(el.asrModels, h("tr", {}, h("td", { colspan: 6 }, "讀取中")));
    return;
  }
  replace(
    el.asrModels,
    ...m.asr.map((model) => {
      const uses: string[] = [];
      if (s?.asr.fast_model === model.id) uses.push("即時");
      if (s?.asr.final_model === model.id) uses.push("完整");
      return h(
        "tr",
        {},
        h(
          "td",
          {},
          h("span", { class: "model-id" }, model.id),
          uses.length ? h("span", { class: "tag" }, `使用中：${uses.join("、")}`) : null,
          model.recommended_for.length
            ? h("span", { class: "tag tag-quiet" }, `建議：${roleNames(model.recommended_for)}`)
            : null,
          model.no_speech_reliable === false
            ? h(
                "span",
                {
                  class: "tag tag-quiet",
                  title: "這個模型無法可靠判斷「不是人聲」，用在完整翻譯時改用即時字幕模型的判斷過濾雜音。",
                },
                "不判斷非人聲",
              )
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

  const t = m.translation;
  const installed = t.models.filter((x) => x.installed === true).length;
  el.trSource.textContent = !t.server.reachable
    ? `無法連到翻譯服務 ${t.server.base_url}${t.server.error ? `（${t.server.error}）` : ""}。如果使用本機 Ollama，請先啟動它。`
    : `翻譯服務 ${t.server.base_url}${t.server.is_local ? "（本機）" : ""}上已安裝 ${installed} 個模型。`;
  replace(
    el.trPresets,
    ...t.models.map((p) =>
      h(
        "li",
        {},
        h("span", { class: "preset-name" }, p.label),
        p.label !== p.name ? h("span", { class: "preset-meta" }, p.name) : null,
        p.recommended_for.length ? h("span", { class: "tag tag-quiet" }, `建議：${roleNames(p.recommended_for)}`) : null,
        p.commercial_use === false ? h("span", { class: "tag tag-warn" }, "不可商用") : null,
        p.excluded_regions.length
          ? h("span", { class: "tag tag-warn" }, `授權不適用於${regionNames(p.excluded_regions)}`)
          : null,
        h(
          "span",
          { class: "preset-meta" },
          p.known ? `授權 ${p.license ?? "未知"}` : "不在 kikitori 的清單中，授權請自行確認",
        ),
        p.approx_vram_gb === null ? null : h("span", { class: "preset-meta" }, `約 ${p.approx_vram_gb.toFixed(1)} GB`),
        h("span", { class: "preset-meta" }, p.installed === null ? "安裝狀態未知" : p.installed ? "已安裝" : "未安裝"),
        p.license_url ? h("span", { class: "preset-meta url" }, p.license_url) : null,
      ),
    ),
  );
  replace(el.trNames, ...t.models.map((x) => h("option", { value: x.name })));
  renderComboOptions();
}

async function loadModels(): Promise<void> {
  if (!engineReady()) return;
  el.modelsRefresh.disabled = true;
  // Both are answered by the engine's background worker in order; matching is
  // by id, so neither waits on the other's position in the reply stream.
  const [hardware, models] = await Promise.allSettled([api.hardwareDetect(), api.modelsList()]);
  if (hardware.status === "fulfilled") state.hardware = hardware.value;
  else reportError("無法偵測硬體", hardware.reason);
  if (models.status === "fulfilled") state.models = models.value;
  else reportError("無法讀取模型清單", models.reason);
  el.modelsRefresh.disabled = false;
  renderHardware();
  renderModels();
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
    reportError(`無法下載 ${id}`, e);
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
  const all = keep ? options : [{ value, label: `${value}（不在清單中）` }, ...options];
  replace(select, ...all.map((o) => h("option", { value: o.value }, o.label)));
  select.value = value;
}

function renderComboOptions(): void {
  const current = readCombo();
  const asr = (state.models?.asr ?? []).map((x) => ({
    value: x.id,
    label: `${x.id}${x.downloaded ? "" : "（未下載）"}`,
  }));
  for (const key of ["fast_model", "final_model"] as const) {
    setOptions(namedField(el.comboForm, key) as HTMLSelectElement, asr, current[key]);
  }
  const precisions = [{ value: "", label: "自動" }, ...COMPUTE_TYPES.map((c) => ({ value: c, label: c }))];
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
      select.append(h("option", { value: form[key] }, form[key] || "自動"));
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
  const list = (items: string[], cls: string | null) =>
    h("ul", {}, ...items.map((m) => h("li", cls ? { class: cls } : {}, m)));
  switch (v.state) {
    case "local_errors":
      replace(el.comboCheck, h("p", { class: "is-error" }, "請先修正標示的欄位。"));
      break;
    case "checking":
      replace(el.comboCheck, h("p", {}, "正在檢查這個組合"));
      break;
    case "checked": {
      const { ok, errors, warnings } = v.check;
      replace(
        el.comboCheck,
        h(
          "p",
          ok ? {} : { class: "is-error" },
          ok
            ? warnings.length
              ? "這台電腦可以使用這個組合，但請留意："
              : "這台電腦可以使用這個組合。"
            : "這個組合目前無法使用：",
        ),
        errors.length ? list(errors, "is-error") : null,
        warnings.length ? list(warnings, null) : null,
      );
      break;
    }
    case "rejected":
      replace(el.comboCheck, h("p", { class: "is-error" }, `引擎不接受這個設定：${v.message}`));
      break;
    case "unavailable":
      replace(
        el.comboCheck,
        h("p", {}, `暫時無法檢查組合（${v.message}）。仍可儲存，引擎會在儲存時檢查設定格式。`),
      );
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
    state.check = { state: "unavailable", message: "引擎尚未就緒" };
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
      if (checks.isCurrent(token) && field) showComboErrors({ [field]: `引擎拒絕這個值：${ce.message}` });
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
  el.comboStatus.textContent = "儲存中";
  try {
    const { settings } = await api.settingsSet(combinationPatch(form));
    state.settings = settings;
    fillCombo(settings, true);
    renderModels();
    el.comboStatus.textContent = sessionActive() ? "已儲存，下一次開始時生效。" : "已儲存組合。";
  } catch (e) {
    const ce = asCommandError(e);
    const field =
      ce?.kind === "engine" && ce.code === "invalid_settings" ? combinationFieldForEnginePath(ce.message) : null;
    if (field && ce?.kind === "engine") {
      showComboErrors({ [field]: `引擎拒絕這個值：${ce.message}` });
      el.comboStatus.textContent = "請修正標示的欄位。";
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
  el.settingsPath.textContent = state.settingsPath ? `設定檔：${state.settingsPath}` : "";
}

async function submitSettings(): Promise<void> {
  const form = readForm();
  const errors = validateSettingsForm(form);
  showFormErrors(errors);
  if (Object.keys(errors).length > 0) {
    el.settingsStatus.textContent = "請修正標示的欄位。";
    return;
  }
  el.settingsStatus.textContent = "儲存中";
  const restrictedChanged = state.settings?.translation.allow_restricted !== form.allow_restricted;
  try {
    const { settings } = await api.settingsSet(settingsPatchFromForm(form));
    state.settings = settings;
    await pushOverlayStyle();
    renderModeNotes();
    renderModels();
    el.settingsStatus.textContent = restrictedChanged
      ? "已儲存設定。建議組合會依新的授權地區重新計算（到「模型」頁查看）。"
      : "已儲存設定。";
  } catch (e) {
    const ce = asCommandError(e);
    const field = ce?.kind === "engine" && ce.code === "invalid_settings" ? formFieldForEnginePath(ce.message) : null;
    if (field && ce?.kind === "engine") {
      showFormErrors({ [field]: `引擎拒絕這個值：${ce.message}` });
      el.settingsStatus.textContent = "請修正標示的欄位。";
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
    reportError("無法套用字幕外觀", e);
  }
}

// ---------------------------------------------------------------- about

function renderAbout(): void {
  const a = state.about;
  el.aboutLicense.textContent = a ? `kikitori 以 ${a.license} 授權發布。` : "";
  const app = a?.app_licenses ?? null;
  const engine = a?.engine_licenses ?? null;
  if (app || engine) {
    // Each folder is listed on its own line so its path stays selectable.
    replace(
      el.aboutThirdParty,
      "第三方元件的授權全文：",
      app && h("br"),
      app && "程式本身（Tauri／Rust 套件、前端套件，以及 kikitori 的 LICENSE）：",
      app && h("span", { class: "url" }, app),
      engine && h("br"),
      engine && "語音引擎（Python 套件）：",
      engine && h("span", { class: "url" }, engine),
    );
  } else {
    el.aboutThirdParty.textContent =
      "開發版沒有內附授權資料夾。正式版的授權全文在安裝目錄的 licenses（程式本身）與 " +
      "kikitori-engine/THIRD_PARTY_LICENSES（語音引擎）資料夾。";
  }
  el.openAppLicenses.hidden = !app;
  el.openEngineLicenses.hidden = !engine;
}

async function openLicenses(folder: LicenseFolder): Promise<void> {
  try {
    await api.openThirdPartyLicenses(folder);
  } catch (e) {
    reportError("無法開啟授權資料夾，請依上面的路徑自行開啟", e);
  }
}

// ---------------------------------------------------------------- navigation

function showPage(page: Page): void {
  state.page = page;
  for (const [name, section] of Object.entries(el.pages)) section.hidden = name !== page;
  for (const item of el.navItems) {
    if (item.dataset.page === page) item.setAttribute("aria-current", "page");
    else item.removeAttribute("aria-current");
  }
  if (page === "models") void loadModels();
}

// ---------------------------------------------------------------- engine data

async function loadEngineData(): Promise<void> {
  try {
    const { settings, path, warnings } = await api.settingsGet();
    if (warnings.length) {
      notify(`設定檔中有 ${warnings.length} 個欄位無法讀取，暫時使用預設值：${warnings.join("、")}`, "bad");
    }
    state.settings = settings;
    state.settingsPath = path;
    state.mode = settings.mode;
    fillForm(settings);
    fillCombo(settings);
    renderSettingsPath();
    await pushOverlayStyle();
  } catch (e) {
    reportError("無法讀取設定", e);
  }
  try {
    state.devices = await api.devicesList();
  } catch (e) {
    reportError("無法列出音訊裝置", e);
  }
  renderModeNotes();
  renderDevices();
  renderStatus();
  if (state.page === "models") void loadModels();
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
    notify(`引擎無法啟動：${status.detail ?? "原因不明"}`, "bad", [
      { label: "重新啟動引擎", run: () => void restartEngine() },
    ]);
  }
}

async function restartEngine(): Promise<void> {
  el.restart.disabled = true;
  try {
    onBridge(await api.restartEngine());
  } catch (e) {
    reportError("無法重新啟動引擎", e);
  } finally {
    el.restart.disabled = false;
  }
}

// ---------------------------------------------------------------- updates

function updateUi(): UpdateUi {
  let notice: HTMLElement | null = null;
  return {
    offer(version, _notes, install) {
      notice = notify(`kikitori ${version} 已經推出，可以下載並安裝。`, "update", [
        {
          label: "下載並安裝",
          run: () => {
            notice?.remove();
            notice = notify("正在下載更新。", "update");
            void install();
          },
        },
      ]);
    },
    progress(pct) {
      const p = notice?.querySelector("p");
      if (p) p.textContent = pct === null ? "正在下載更新。" : `正在下載更新：${pct}%`;
    },
    installed(version, restart) {
      notice?.remove();
      notice = notify(`kikitori ${version} 已安裝，重新啟動後生效。`, "update", [
        { label: "立即重新啟動", run: () => void restart() },
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
  el.updateStatus.textContent = "檢查中";
  try {
    const version = await checkForUpdate(updateUi(), true);
    if (version) el.updateStatus.textContent = `有新版本 ${version}。`;
    else if (el.updateStatus.textContent === "檢查中") el.updateStatus.textContent = "目前已是最新版本。";
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
      reportError("無法切換滑鼠穿透", e);
    }
    renderOverlay();
  });
  el.clear.addEventListener("click", () => {
    state.subtitles = emptySubtitles(state.subtitles.sessionId);
    renderTranscript();
  });
  el.modelsRefresh.addEventListener("click", () => void loadModels());
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
}

async function subscribe(): Promise<void> {
  await Promise.all([
    events.bridge(onBridge),
    events.status((s) => {
      if (s.session_id !== null && state.sessionId !== null && s.session_id < state.sessionId) return;
      if (s.session_id !== null) state.sessionId = s.session_id;
      state.sessionState = s.state;
      if (s.state === "error") notify(describeStatusError(s.code, s.detail), "bad");
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
          notify(`${p.model} 下載完成。`);
          void loadModels();
        }
      } else if (p.state === "error") {
        const what = p.model === GPU_RUNTIME_ID ? "GPU 加速元件" : p.model;
        notify(`${what}下載失敗：${p.error ?? "未知原因"}`, "bad");
      }
      if (state.page === "models") renderModels();
    }),
    events.log((l) => {
      if (l.level === "warning" || l.level === "error") {
        console.warn(`engine ${l.level}: ${l.message}`);
        notify(`引擎提醒：${l.message}`, l.level === "error" ? "bad" : "info");
      }
    }),
    events.overlay((o) => {
      state.overlay = o;
      renderOverlay();
    }),
  ]);
}

async function init(): Promise<void> {
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
    reportError("無法讀取字幕視窗狀態", e);
  }
  try {
    onBridge(await api.bridgeStatus());
  } catch (e) {
    reportError("無法讀取引擎狀態", e);
  }
  if (import.meta.env.PROD) {
    // Silent on failure: being offline at startup is normal.
    void checkForUpdate(updateUi(), false);
  }
}

init().catch((e: unknown) => {
  console.error("main window failed to initialise", e);
  notify(`畫面初始化失敗：${describeError(e)}`, "bad");
});
