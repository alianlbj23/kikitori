/**
 * Main window controller.
 *
 * Role: wires the static markup in index.html to the engine (through `lib/api`)
 * and keeps it in sync with engine/bridge/overlay events. Pure decisions
 * (subtitle ordering, validation, wording) live in `lib/`; this file only holds
 * UI state and renders it.
 */

import { getVersion } from "@tauri-apps/api/app";

import { api, describeError, events } from "../lib/api";
import { byId, h, replace } from "../lib/dom";
import {
  describeStatus,
  describeStatusError,
  formatBytes,
  formatTimecode,
  htmlLang,
  languageName,
  languageShort,
  osName,
  percent,
  speedTierLabel,
} from "../lib/format";
import {
  devicePatch,
  formFieldForEnginePath,
  recommendationPatch,
  formFromSettings,
  settingsPatchFromForm,
  validateSettingsForm,
  type FormErrors,
  type SettingsForm,
} from "../lib/settings";
import { applySubtitle, emptySubtitles, type SubtitleLine, type SubtitleState } from "../lib/subtitles";
import type {
  AsrChoice,
  BridgeStatus,
  DeviceInfo,
  DownloadProgressEvent,
  EngineState,
  HardwareReport,
  Mode,
  ModelsList,
  OverlaySnapshot,
  Settings,
} from "../lib/types";
import { asCommandError } from "../lib/validate";
import { checkForUpdate, type UpdateUi } from "./updater";

const TRANSCRIPT_MAX_LINES = 300;

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
  recommendation: byId("recommendation"),
  asrModels: byId("asr-models"),
  asrChoice: byId<HTMLFormElement>("asr-choice"),
  asrFast: byId<HTMLSelectElement>("asr-fast"),
  asrFinal: byId<HTMLSelectElement>("asr-final"),
  asrDevice: byId<HTMLSelectElement>("asr-device"),
  modelsRefresh: byId<HTMLButtonElement>("models-refresh"),
  gpuButton: byId<HTMLButtonElement>("gpu-runtime"),
  gpuNote: byId("gpu-note"),
  trSource: byId("tr-source"),
  trPresets: byId("tr-presets"),
  trChoice: byId<HTMLFormElement>("tr-choice"),
  trFast: byId<HTMLInputElement>("tr-fast"),
  trFinal: byId<HTMLInputElement>("tr-final"),
  trNames: byId<HTMLDataListElement>("tr-names"),
  settingsForm: byId<HTMLFormElement>("settings-form"),
  settingsPath: byId("settings-path"),
  settingsStatus: byId("settings-status"),
  preview: byId("style-preview"),
  oFontSize: byId<HTMLOutputElement>("o-font-size"),
  oOpacity: byId<HTMLOutputElement>("o-opacity"),
  aboutVersion: byId("about-version"),
  aboutEngine: byId("about-engine"),
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

// ---------------------------------------------------------------- status & controls

function sessionActive(): boolean {
  return state.sessionState === "running" || state.sessionState === "loading";
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

  const ready = phase === "ready";
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

// ---------------------------------------------------------------- models page

function renderHardware(): void {
  const hw = state.hardware;
  if (!hw) {
    replace(el.hardware, h("dt", {}, "狀態"), h("dd", {}, "偵測中"));
    replace(el.recommendation, h("p", { class: "block-lede" }, "偵測完成後會在這裡列出建議。"));
    return;
  }
  const x = hw.hardware;
  const gpus = x.gpus.length
    ? x.gpus.map((g) => (g.vram_bytes ? `${g.name}（顯示記憶體 ${formatBytes(g.vram_bytes)}）` : g.name)).join("、")
    : "沒有偵測到 NVIDIA 顯示卡";
  replace(
    el.hardware,
    h("dt", {}, "作業系統"),
    h("dd", {}, x.os_version.startsWith(osName(x.os)) ? x.os_version : `${osName(x.os)} ${x.os_version}`.trim()),
    h("dt", {}, "處理器"),
    h("dd", {}, x.cpu_count ? `${x.cpu_count} 個執行緒（${x.machine}）` : x.machine || "未知"),
    h("dt", {}, "記憶體"),
    h("dd", {}, formatBytes(x.ram_bytes)),
    h("dt", {}, "顯示卡"),
    h("dd", {}, x.apple_silicon ? "Apple Silicon" : gpus),
  );

  const rec = hw.recommendation;
  if (!rec || (!rec.fast_asr && !rec.final_asr && !rec.translation_fast)) {
    replace(
      el.recommendation,
      h("p", { class: "block-lede" }, "這一版還不會自動推薦。一般電腦建議即時字幕用 base、完整翻譯用 large-v3-turbo；較舊的電腦可以先試 small。"),
    );
    return;
  }
  const describeChoice = (c: AsrChoice | null): string => {
    if (!c) return "維持目前設定";
    const device = c.device === "cuda" ? "NVIDIA GPU" : c.device === "cpu" ? "CPU" : "自動";
    const extra = [c.compute_type, c.cpu_threads ? `${c.cpu_threads} 執行緒` : null].filter(Boolean).join("、");
    return extra ? `${c.model}（${device}，${extra}）` : `${c.model}（${device}）`;
  };
  const translation =
    rec.translation_fast && rec.translation_final && rec.translation_fast !== rec.translation_final
      ? `即時 ${rec.translation_fast}、完整 ${rec.translation_final}`
      : (rec.translation_fast ?? rec.translation_final ?? "翻譯服務不在本機，請在設定頁選擇");
  const dl = h(
    "dl",
    { class: "facts" },
    h("dt", {}, "即時字幕的辨識模型"),
    h("dd", {}, describeChoice(rec.fast_asr)),
    h("dt", {}, "完整翻譯的辨識模型"),
    h("dd", {}, describeChoice(rec.final_asr)),
    h("dt", {}, "翻譯模型"),
    h("dd", {}, translation),
  );
  const notes = h(
    "ul",
    { class: "notes" },
    ...rec.reasons.map((r) => h("li", {}, r)),
    ...rec.warnings.map((w) => h("li", { class: "warning" }, w)),
  );
  const apply = h("button", { type: "button", class: "primary" }, "套用建議組合");
  apply.addEventListener("click", () => {
    const withTypes = state.settings?.asr_has_compute_types ?? false;
    void saveSettingsPatch(recommendationPatch(rec, withTypes), "已套用建議組合。");
  });
  replace(el.recommendation, dl, notes, h("div", { class: "form-actions" }, apply));
}

/** GPU runtime (cuBLAS) section: shown as unavailable until the engine reports it. */
function renderGpuRuntime(): void {
  const rt = state.models?.gpu_runtime ?? null;
  const missing = state.hardware?.recommendation?.gpu_runtime_missing ?? false;
  if (!rt) {
    el.gpuButton.disabled = true;
    replace(el.gpuNote, "之後的版本會提供這個下載。");
    return;
  }
  if (!rt.supported) {
    el.gpuButton.disabled = true;
    replace(el.gpuNote, "這台電腦的平台不支援 GPU 加速元件（需要 Windows 或 Linux x86-64 與 NVIDIA 顯示卡）。");
    return;
  }
  const progress = state.downloads.get(rt.id);
  if (progress?.state === "downloading") {
    const pct = percent(progress.downloaded_bytes, progress.total_bytes);
    el.gpuButton.disabled = true;
    replace(
      el.gpuNote,
      h("span", { class: "progress" }, h("progress", { max: 100, value: pct, "aria-label": "GPU 加速元件下載進度" }), h("span", {}, `${pct}%`)),
    );
    return;
  }
  if (rt.installed) {
    el.gpuButton.disabled = true;
    replace(el.gpuNote, "已安裝。");
    return;
  }
  el.gpuButton.disabled = false;
  const size = rt.download_bytes ? `約 ${formatBytes(rt.download_bytes)}` : "數百 MB";
  replace(
    el.gpuNote,
    `${missing ? "目前因為缺少這個元件，辨識改用 CPU。" : ""}這會從 PyPI 下載 NVIDIA cuBLAS（${size}），使用即表示同意 ${rt.license ?? "NVIDIA CUDA EULA"}`,
    rt.license_url ? h("span", { class: "url" }, `（${rt.license_url}）`) : null,
    "。",
  );
}

function confirmGpuRuntime(): void {
  const rt = state.models?.gpu_runtime;
  if (!rt) return;
  notify(`下載 NVIDIA cuBLAS 前請先閱讀授權：${rt.license_url ?? "NVIDIA CUDA EULA"}`, "update", [
    { label: "同意並下載", run: () => void downloadModel(rt.id) },
  ]);
}

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

function renderModels(): void {
  const m = state.models;
  const s = state.settings;
  if (!m) {
    replace(el.asrModels, h("tr", {}, h("td", { colspan: 5 }, "讀取中")));
    return;
  }
  replace(
    el.asrModels,
    ...m.asr.map((model) => {
      const uses: string[] = [];
      if (s?.asr.fast_model === model.id) uses.push("即時");
      if (s?.asr.final_model === model.id) uses.push("完整");
      const recommended = model.recommended_for.flatMap((r) =>
        r === "fast" ? ["即時"] : r === "final" ? ["完整"] : [],
      );
      return h(
        "tr",
        {},
        h(
          "td",
          {},
          h("span", { class: "model-id" }, model.id),
          uses.length ? h("span", { class: "tag" }, `使用中：${uses.join("、")}`) : null,
          recommended.length ? h("span", { class: "tag tag-quiet" }, `建議：${recommended.join("、")}`) : null,
        ),
        h("td", {}, formatBytes(model.size_bytes)),
        h("td", {}, speedTierLabel(model.speed_tier)),
        h("td", {}, model.license),
        h("td", {}, downloadCell(model)),
      );
    }),
  );
  const asrOptions = (current: string | undefined) => {
    const ids = m.asr.map((x) => x.id);
    const all = current && !ids.includes(current) ? [current, ...ids] : ids;
    return all.map((id) => {
      const info = m.asr.find((x) => x.id === id);
      const suffix = info ? (info.downloaded ? "" : "（未下載）") : "（不在清單中）";
      return h("option", { value: id }, `${id}${suffix}`);
    });
  };
  replace(el.asrFast, ...asrOptions(s?.asr.fast_model));
  replace(el.asrFinal, ...asrOptions(s?.asr.final_model));
  if (s) {
    el.asrFast.value = s.asr.fast_model;
    el.asrFinal.value = s.asr.final_model;
    el.asrDevice.value = s.asr.device;
  }

  const t = m.translation;
  el.trSource.textContent = t.error
    ? `無法連到翻譯服務 ${t.base_url}（${t.error}）。如果使用本機 Ollama，請先啟動它。`
    : `翻譯服務 ${t.base_url} 上已安裝 ${t.installed?.length ?? 0} 個模型。`;
  replace(
    el.trPresets,
    ...t.presets.map((p) =>
      h(
        "li",
        {},
        h("span", { class: "preset-name" }, p.label),
        h("span", { class: "preset-meta" }, p.model),
        h("span", { class: "preset-meta" }, `授權 ${p.license}`),
        p.commercial_use ? null : h("span", { class: "preset-meta warning" }, "不可商用"),
        p.excluded_regions.length ? h("span", { class: "preset-meta warning" }, `授權不適用於 ${p.excluded_regions.join("、")}`) : null,
        h("span", { class: "preset-meta" }, p.installed === null ? "安裝狀態未知" : p.installed ? "已安裝" : "未安裝"),
      ),
    ),
  );
  const names = new Set<string>([...t.presets.map((p) => p.model), ...(t.installed ?? [])]);
  replace(el.trNames, ...Array.from(names).map((n) => h("option", { value: n })));
  if (s) {
    el.trFast.value = s.translation.fast_model;
    el.trFinal.value = s.translation.final_model;
  }  renderGpuRuntime();
}

async function loadModels(): Promise<void> {
  el.modelsRefresh.disabled = true;
  try {
    const [hardware, models] = await Promise.all([api.hardwareDetect(), api.modelsList()]);
    state.hardware = hardware;
    state.models = models;
  } catch (e) {
    reportError("無法讀取模型資訊", e);
  } finally {
    el.modelsRefresh.disabled = false;
  }
  renderHardware();
  renderModels();
}

async function downloadModel(id: string): Promise<void> {
  state.downloads.set(id, { model: id, downloaded_bytes: 0, total_bytes: 0, state: "downloading", error: null });
  renderModels();
  try {
    await api.modelsDownload(id);
  } catch (e) {
    state.downloads.delete(id);
    renderModels();
    reportError(`無法下載 ${id}`, e);
  }
}

// ---------------------------------------------------------------- settings page

function formElement(name: keyof SettingsForm): HTMLInputElement | HTMLSelectElement {
  const found = el.settingsForm.elements.namedItem(name);
  if (!(found instanceof HTMLInputElement || found instanceof HTMLSelectElement)) {
    throw new Error(`settings form has no field ${name}`);
  }
  return found;
}

const FORM_FIELDS: (keyof SettingsForm)[] = [
  "base_url",
  "api_key_env",
  "fast_model",
  "final_model",
  "partner_language",
  "font_size",
  "background_opacity_percent",
  "position",
  "max_lines",
];

function readForm(): SettingsForm {
  const form = {} as SettingsForm;
  for (const name of FORM_FIELDS) form[name] = formElement(name).value;
  return form;
}

function fillForm(settings: Settings): void {
  const form = formFromSettings(settings);
  for (const name of FORM_FIELDS) formElement(name).value = form[name];
  showFormErrors({});
  renderPreview();
}

function showFormErrors(errors: FormErrors): void {
  for (const name of FORM_FIELDS) {
    const message = errors[name] ?? "";
    const slot = el.settingsForm.querySelector<HTMLElement>(`[data-error-for="${name}"]`);
    if (slot) slot.textContent = message;
    formElement(name).closest(".field")?.classList.toggle("has-error", message !== "");
    formElement(name).setAttribute("aria-invalid", String(message !== ""));
  }
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

async function saveSettingsPatch(patch: Record<string, unknown>, success: string): Promise<boolean> {
  try {
    const { settings, warnings } = await api.settingsSet(patch);
    state.settings = settings;
    if (warnings.length) console.warn("settings fields replaced by defaults:", warnings);
    await pushOverlayStyle();
    renderModels();
    renderModeNotes();
    notify(success);
    return true;
  } catch (e) {
    reportError("無法儲存設定", e);
    return false;
  }
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
  try {
    const { settings } = await api.settingsSet(settingsPatchFromForm(form));
    state.settings = settings;
    await pushOverlayStyle();
    renderModeNotes();
    renderModels();
    el.settingsStatus.textContent = "已儲存設定。";
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

// ---------------------------------------------------------------- navigation

function showPage(page: Page): void {
  state.page = page;
  for (const [name, section] of Object.entries(el.pages)) section.hidden = name !== page;
  for (const item of el.navItems) {
    if (item.dataset.page === page) item.setAttribute("aria-current", "page");
    else item.removeAttribute("aria-current");
  }
  if (page === "models" && state.bridge?.phase === "ready") void loadModels();
}

// ---------------------------------------------------------------- engine data

async function loadEngineData(): Promise<void> {
  try {
    const { settings, warnings } = await api.settingsGet();
    if (warnings.length) {
      notify(`設定檔中有 ${warnings.length} 個欄位無法讀取，暫時使用預設值：${warnings.join("、")}`, "bad");
    }
    state.settings = settings;
    state.mode = settings.mode;
    fillForm(settings);
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
  const wasReady = state.bridge?.phase === "ready";
  state.bridge = status;
  if (status.phase !== "ready") {
    // A restarted engine has no session.
    state.sessionState = "idle";
  }
  renderStatus();
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
  el.gpuButton.addEventListener("click", confirmGpuRuntime);
  el.asrChoice.addEventListener("submit", (e) => {
    e.preventDefault();
    void saveSettingsPatch(
      { asr: { fast_model: el.asrFast.value, final_model: el.asrFinal.value, device: el.asrDevice.value } },
      "已套用語音辨識模型。",
    );
  });
  el.trChoice.addEventListener("submit", (e) => {
    e.preventDefault();
    const fast = el.trFast.value.trim();
    const final = el.trFinal.value.trim();
    if (!fast || !final) {
      notify("請填寫兩個翻譯模型的名稱。", "bad");
      return;
    }
    void saveSettingsPatch({ translation: { fast_model: fast, final_model: final } }, "已套用翻譯模型。");
  });
  el.settingsForm.addEventListener("input", () => {
    el.settingsStatus.textContent = "";
    renderPreview();
  });
  el.settingsForm.addEventListener("submit", (e) => {
    e.preventDefault();
    void submitSettings();
  });
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
        notify(`${p.model} 下載完成。`);
        void loadModels();
      } else if (p.state === "error") {
        notify(`${p.model} 下載失敗：${p.error ?? "未知原因"}`, "bad");
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
  try {
    el.aboutVersion.textContent = `kikitori ${await getVersion()}`;
  } catch (e) {
    console.error("getVersion failed:", e);
  }
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
