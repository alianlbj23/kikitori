/**
 * Automatic hardware recommendation: first-run auto-apply, the "better
 * combination" offer, and getting the chosen models downloaded.
 *
 * Role: the decisions and wording behind these flows, kept out of `main.ts` so
 * they can be tested. The engine owns every rule about *what* to recommend and
 * how it becomes settings (`settings.apply_recommendation`,
 * `settings.preview_recommendation`, whose `differs` already compares with the
 * saved settings); this module only decides *when* to apply or offer it, and
 * builds the Traditional Chinese messages from the engine's answers.
 */

import { formatBytes, percent } from "./format";
import { MAX_DISMISSED } from "./settings";
import type {
  AppliedRecommendation,
  AsrModel,
  DownloadProgressEvent,
  HardwareInfo,
  HardwareReport,
  Recommendation,
  RecommendationPreview,
  Settings,
} from "./types";

const GIB = 1024 ** 3;

// ---------------------------------------------------------------- wording

/** "NVIDIA GeForce RTX 5070，12 GB", "Apple Silicon，16 GB 記憶體", or the CPU. */
export function machineSummary(hw: HardwareInfo): string {
  // The recommendation is sized for the GPU with the most memory (hardware.md).
  const gpu = hw.gpus.reduce<HardwareInfo["gpus"][number] | null>(
    (best, g) => (best === null || g.memory_total_mb > best.memory_total_mb ? g : best),
    null,
  );
  if (gpu && !hw.apple_silicon) return `${gpu.name}，${Math.round(gpu.memory_total_mb / 1024)} GB`;
  const ram = hw.memory.total_bytes === null ? null : `${Math.round(hw.memory.total_bytes / GIB)} GB 記憶體`;
  const name = hw.apple_silicon ? "Apple Silicon" : hw.cpu.model;
  if (name && ram) return `${name}，${ram}`;
  return name ?? (ram ? `這台電腦的處理器，${ram}` : "這台電腦的處理器");
}

function deviceShort(device: string): string {
  if (device === "cuda") return "GPU";
  if (device === "cpu") return "CPU";
  return "自動";
}

/** "即時 small、完整 large-v3-turbo（GPU），翻譯 qwen3.5:9b". */
export function combinationSummary(rec: Recommendation): string {
  const { fast_asr: fast, final_asr: final } = rec;
  const asr =
    fast.device === final.device
      ? `即時 ${fast.model}、完整 ${final.model}（${deviceShort(fast.device)}）`
      : `即時 ${fast.model}（${deviceShort(fast.device)}）、完整 ${final.model}（${deviceShort(final.device)}）`;
  const a = rec.translation_fast;
  const b = rec.translation_final;
  // The engine leaves translation untouched when it cannot recommend (remote server).
  const translation =
    a === null || b === null
      ? "翻譯模型沿用目前設定（翻譯服務不在本機）"
      : a === b
        ? `翻譯 ${a}`
        : `即時翻譯 ${a}、完整翻譯 ${b}`;
  return `${asr}，${translation}`;
}

/** Notice after the first-run auto-apply: what was chosen, and for which machine. */
export function autoAppliedNotice(hw: HardwareInfo, rec: Recommendation): string {
  const text = `已依這台電腦（${machineSummary(hw)}）套用建議組合：${combinationSummary(rec)}。`;
  return rec.gpu_runtime_missing
    ? `${text}顯示卡缺少 GPU 加速元件，目前先用 CPU；到「模型」頁下載後可以改用顯示卡。`
    : text;
}

function sentence(text: string): string {
  return /[。！？.!?]$/.test(text) ? text : `${text}。`;
}

/** Notice when the first run could not detect or apply: the safe defaults stay. */
export function defaultsKeptNotice(
  stage: "detect" | "apply",
  reason: string,
  asr: Pick<Settings["asr"], "fast_model" | "final_model">,
): string {
  if (stage === "apply") {
    // The request may have timed out after the engine wrote the file, so the
    // current combination is not stated; the models page shows what is saved.
    return `無法套用建議組合：${sentence(reason)}請到「模型」頁確認目前的組合，需要時按「套用建議組合」。`;
  }
  return (
    `無法偵測這台電腦的硬體：${sentence(reason)}先使用預設的組合（即時 ${asr.fast_model}、完整 ${asr.final_model}），` +
    "可以到「模型」頁重新偵測後套用建議組合。"
  );
}

/** Text of the "better combination" offer. */
export function offerText(preview: RecommendationPreview): string {
  return `有更適合這台電腦的組合：${combinationSummary(preview.recommendation)}。`;
}

// ---------------------------------------------------------------- first run

/**
 * Lets the first-run auto-apply start at most once per window. Settings are
 * reloaded on every engine (re)start; without this a failed first run (which
 * leaves the file missing, so `is_new` stays true) would retry in a loop. A
 * failed first run is retried on the next launch, unless anything was saved
 * meanwhile (changing the mode or "不要再提醒" also writes the file).
 */
export class FirstRunGate {
  private claimed = false;

  /** True exactly once, and only for a new settings file the user has not chosen models in. */
  claim(s: { isNew: boolean; userChoseModels: boolean }): boolean {
    if (this.claimed || !s.isNew || s.userChoseModels) return false;
    this.claimed = true;
    return true;
  }
}

export interface FirstRunDeps {
  detect(): Promise<HardwareReport>;
  /**
   * `settings.apply_recommendation` with `only_if_new`: the engine checks and
   * writes under one lock, so a combination saved meanwhile is never replaced.
   */
  apply(): Promise<AppliedRecommendation>;
  /**
   * Re-checked after detection, which can take seconds: if the user saved a
   * combination meanwhile, their choice wins.
   */
  userChoseModels(): boolean;
  /** Turns a failure into a sentence for the notice. */
  describe?(e: unknown): string;
}

export type FirstRunOutcome =
  | { kind: "applied"; report: HardwareReport; applied: AppliedRecommendation; notice: string }
  /** Nothing written: the user chose (or saved) settings first. */
  | { kind: "kept_defaults"; report: HardwareReport | null; notice: string }
  | { kind: "skipped" };

function plainError(e: unknown): string {
  return e instanceof Error ? e.message : String(e);
}

/**
 * First run: wait for `hardware.detect`, then `settings.apply_recommendation`
 * (which reuses that detection), and say what was chosen. Any failure keeps the
 * engine's safe defaults (base/small) and says so; nothing is retried here.
 */
export async function runFirstRun(deps: FirstRunDeps, settings: Settings): Promise<FirstRunOutcome> {
  const describe = deps.describe ?? plainError;
  let report: HardwareReport;
  try {
    report = await deps.detect();
  } catch (e) {
    return { kind: "kept_defaults", report: null, notice: defaultsKeptNotice("detect", describe(e), settings.asr) };
  }
  if (deps.userChoseModels()) return { kind: "skipped" };
  try {
    const applied = await deps.apply();
    if (applied.applied === null) return { kind: "skipped" };
    return {
      kind: "applied",
      report,
      applied,
      notice: autoAppliedNotice(report.hardware, applied.recommendation),
    };
  } catch (e) {
    return { kind: "kept_defaults", report, notice: defaultsKeptNotice("apply", describe(e), settings.asr) };
  }
}

// ---------------------------------------------------------------- the offer

/** Show the offer when applying would change something and this one was not dismissed. */
export function shouldOfferRecommendation(
  preview: RecommendationPreview | null,
  dismissed: readonly string[],
): boolean {
  return preview !== null && preview.differs && !dismissed.includes(preview.fingerprint);
}

/**
 * The dismissal list after "don't remind me": newest last, no duplicates, at
 * most `max` entries. A few are kept (not just the last) so a recommendation
 * that flips back and forth (e.g. Ollama sometimes not running) stays quiet.
 */
export function dismissedWith(dismissed: readonly string[], fingerprint: string, max = MAX_DISMISSED): string[] {
  return [...dismissed.filter((f) => f !== fingerprint), fingerprint].slice(-max);
}

// ---------------------------------------------------------------- missing models

export interface MissingModel {
  id: string;
  size_bytes: number;
}

type AsrChoiceIds = Pick<Settings["asr"], "fast_model" | "final_model">;

/**
 * The chosen ASR models that are known but not downloaded, once each. Unknown
 * ids are left out: they cannot be downloaded (`session.start` reports
 * `unknown_model` for them).
 */
export function missingModels(asr: AsrChoiceIds, models: readonly AsrModel[]): MissingModel[] {
  const ids = [...new Set([asr.fast_model, asr.final_model])];
  return ids.flatMap((id) => {
    const m = models.find((x) => x.id === id);
    return m && !m.downloaded ? [{ id, size_bytes: m.size_bytes }] : [];
  });
}

export type MissingModelsState = "offer" | "downloading" | "failed" | "done";

/** What the missing-models notice says, from the latest model list and download events. */
export function missingModelsView(
  required: readonly MissingModel[],
  models: readonly AsrModel[] | null,
  downloads: ReadonlyMap<string, DownloadProgressEvent>,
): { state: MissingModelsState; text: string } {
  const downloading: string[] = [];
  const failed: string[] = [];
  const pending: MissingModel[] = [];
  for (const m of required) {
    if (models?.find((x) => x.id === m.id)?.downloaded) continue;
    const d = downloads.get(m.id);
    if (d?.state === "downloading") downloading.push(`${m.id} ${percent(d.downloaded_bytes, d.total_bytes)}%`);
    else if (d?.state === "error") failed.push(`${m.id} 下載失敗：${d.error ?? "未知原因"}`);
    else pending.push(m);
  }
  if (downloading.length) return { state: "downloading", text: `正在下載 ${downloading.join("、")}，完成後就可以開始。` };
  if (failed.length) return { state: "failed", text: `${failed.join("；")}。可以重新下載。` };
  if (pending.length) {
    const total = pending.reduce((sum, m) => sum + m.size_bytes, 0);
    return {
      state: "offer",
      text: `還需要下載 ${pending.map((m) => m.id).join("、")}（共約 ${formatBytes(total)}）才能開始。`,
    };
  }
  return { state: "done", text: "模型已下載完成，可以按「開始」了。" };
}

/** Chosen models still downloading: starting now would fail with `model_not_downloaded`. */
export function blockingDownloads(asr: AsrChoiceIds, downloads: ReadonlyMap<string, DownloadProgressEvent>): string[] {
  return [...new Set([asr.fast_model, asr.final_model])].filter((id) => downloads.get(id)?.state === "downloading");
}
