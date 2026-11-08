/**
 * Listening / subtitle language selection on the settings page.
 *
 * Role: the engine owns the language list (`languages.list`), which languages
 * may be paired, and which models each combination needs
 * (`settings.preview_languages`). This module turns those answers into what the
 * settings page shows — menu options with a type-to-filter, the recommended
 * models with "installed / downloaded on first use" — checks the obvious
 * mistakes before asking the engine, and builds the `settings.set` patch. Kept
 * out of `main.ts` so every rule is unit-tested; texts come from the dictionary.
 */

import { formatBytes } from "./format";
import { languageName, t } from "./i18n";
import { parseEngineMessages, type EngineMessage } from "./messages";
import type { LanguageChoice, LanguageInfo, LanguagePreview, Mode, ModelRole, PreviewModel, Settings } from "./types";
import { ValidationError, parseRecommendation } from "./validate";

/** `source_language` value for "detect the listening language automatically". */
export const AUTO_LANGUAGE = "auto";

type Obj = Record<string, unknown>;

/** Language list codes and their aliases: letters, digits and `-` (e.g. `zh-TW`). */
const LANGUAGE_CODE = /^[A-Za-z]{2,3}(-[A-Za-z0-9]{1,8})?$/;
const MAX_NAME = 100;

function asObj(v: unknown, path: string): Obj {
  if (typeof v !== "object" || v === null || Array.isArray(v)) throw new ValidationError(path, "expected an object");
  return v as Obj;
}

/** A language list code (or "auto" where allowed). */
export function isLanguageCode(v: unknown, allowAuto = false): v is string {
  return typeof v === "string" && (LANGUAGE_CODE.test(v) || (allowAuto && v === AUTO_LANGUAGE));
}

function name(o: Obj, key: string, fallback: string): string {
  const v = o[key];
  return typeof v === "string" && v.trim() !== "" && v.length <= MAX_NAME ? v : fallback;
}

/**
 * `languages.list`. Malformed entries are dropped (the menu still works with the
 * rest); an answer without any usable language is an error.
 */
export function parseLanguageList(v: unknown): LanguageInfo[] {
  const p = "languages.list";
  const list = asObj(v, p).languages;
  if (!Array.isArray(list)) throw new ValidationError(`${p}.languages`, "expected an array");
  const seen = new Set<string>();
  const out: LanguageInfo[] = [];
  for (const item of list.slice(0, 200)) {
    if (typeof item !== "object" || item === null) continue;
    const o = item as Obj;
    if (!isLanguageCode(o.code) || seen.has(o.code)) continue;
    const whisper = isLanguageCode(o.whisper_code) ? o.whisper_code : o.code;
    seen.add(o.code);
    out.push({
      code: o.code,
      english_name: name(o, "english_name", o.code),
      native_name: name(o, "native_name", o.code),
      whisper_code: whisper,
      rtl: o.rtl === true,
      no_space: o.no_space === true,
    });
  }
  if (out.length === 0) throw new ValidationError(`${p}.languages`, "no usable language");
  return out;
}

function roles(v: unknown): ModelRole[] {
  return Array.isArray(v) ? v.filter((r): r is ModelRole => r === "fast" || r === "final") : [];
}

function size(v: unknown): number | null {
  return typeof v === "number" && Number.isSafeInteger(v) && v >= 0 ? v : null;
}

function previewModels(v: unknown, path: string): PreviewModel[] {
  if (!Array.isArray(v)) return [];
  return v.slice(0, 10).flatMap((item, i) => {
    const o = asObj(item, `${path}[${i}]`);
    if (typeof o.model !== "string" || o.model.length === 0 || o.model.length > 200) return [];
    return [
      {
        model: o.model,
        roles: roles(o.roles),
        installed: typeof o.installed === "boolean" ? o.installed : null,
        size_bytes: size(o.size_bytes),
        download_bytes: size(o.download_bytes),
        size_approximate: o.size_approximate === true,
      },
    ];
  });
}

function choice(v: unknown, path: string): LanguageChoice {
  const o = asObj(v, path);
  const mode = o.mode === "interpret" ? "interpret" : o.mode === "media" ? "media" : null;
  if (mode === null) throw new ValidationError(`${path}.mode`, "expected media or interpret");
  for (const key of ["target_language", "partner_language"] as const) {
    if (!isLanguageCode(o[key])) throw new ValidationError(`${path}.${key}`, "not a language code");
  }
  if (!isLanguageCode(o.source_language, true)) throw new ValidationError(`${path}.source_language`, "not a language code");
  return {
    mode,
    source_language: o.source_language,
    target_language: o.target_language as string,
    partner_language: o.partner_language as string,
  };
}

/** `settings.preview_languages` (see pipeline-engine.md). */
export function parseLanguagePreview(v: unknown): LanguagePreview {
  const p = "settings.preview_languages";
  const o = asObj(v, p);
  const models = typeof o.models === "object" && o.models !== null ? (o.models as Obj) : {};
  return {
    languages: choice(o.languages, `${p}.languages`),
    recommendation: parseRecommendation(o.recommendation, `${p}.recommendation`),
    patch: asObj(o.patch, `${p}.patch`),
    asr: previewModels(models.asr, `${p}.models.asr`),
    translation: previewModels(models.translation, `${p}.models.translation`),
    translation_needed: o.translation_needed !== false,
    download_bytes: size(o.download_bytes) ?? 0,
    download_size_known: o.download_size_known !== false,
    messages: parseEngineMessages(o.messages),
  };
}

// ---------------------------------------------------------------- menu options

export interface LanguageOption {
  value: string;
  /** "native name（name in the interface language）", or one name when they agree. */
  label: string;
  /** Folded text the filter matches against (code, both names, English name). */
  search: string;
  rtl: boolean;
}

/** Lowercase without diacritics, so "espanol" finds "Español". */
export function fold(text: string): string {
  return text.normalize("NFD").replace(/\p{M}/gu, "").toLowerCase();
}

/**
 * Menu options in the engine's order (most used first). The listening-language
 * menu starts with "detect automatically" (`withAuto`).
 */
export function languageOptions(list: readonly LanguageInfo[], withAuto = false): LanguageOption[] {
  const options = list.map((l) => {
    const local = languageName(l.code);
    const label = fold(local) === fold(l.native_name) ? l.native_name : t("common.parenthetical", { text: l.native_name, note: local });
    return { value: l.code, label, search: fold([l.code, l.native_name, local, l.english_name].join(" ")), rtl: l.rtl };
  });
  if (!withAuto) return options;
  const auto = t("language.auto");
  return [{ value: AUTO_LANGUAGE, label: auto, search: fold(`${AUTO_LANGUAGE} ${auto}`), rtl: false }, ...options];
}

/** Options matching the filter text; the selected one always stays so the menu never loses it. */
export function filterLanguageOptions(
  options: readonly LanguageOption[],
  query: string,
  selected: string,
): LanguageOption[] {
  const q = fold(query.trim());
  if (q === "") return [...options];
  return options.filter((o) => o.value === selected || o.search.includes(q));
}

// ---------------------------------------------------------------- the form

/** Raw values of the language section; which fields matter depends on `mode`. */
export interface LanguageForm {
  mode: Mode;
  /** Media: listening language or "auto". */
  source: string;
  /** Media: subtitle language. Interpret: my language. */
  target: string;
  /** Interpret: the other person's language. */
  partner: string;
  /** "同時改用推薦的模型". */
  applyRecommended: boolean;
}

export type LanguageField = "source" | "target" | "partner";
export type LanguageErrors = Partial<Record<LanguageField, string>>;

export function languageFormFromSettings(s: Settings, mode: Mode, applyRecommended = true): LanguageForm {
  return {
    mode,
    source: s.source_language,
    target: s.target_language,
    partner: s.partner_language,
    applyRecommended,
  };
}

export function whisperCode(code: string, list: readonly LanguageInfo[]): string | null {
  return list.find((l) => l.code === code)?.whisper_code ?? null;
}

/**
 * Checks before asking the engine: every code is from the list, and in
 * interpret mode the two languages can be told apart (different Whisper
 * languages; zh-TW and zh-CN are both `zh`). The engine checks again.
 */
export function validateLanguageForm(form: LanguageForm, list: readonly LanguageInfo[]): LanguageErrors {
  const errors: LanguageErrors = {};
  const known = (code: string) => list.some((l) => l.code === code);
  if (form.mode === "media") {
    if (form.source !== AUTO_LANGUAGE && !known(form.source)) errors.source = t("validation.language");
    if (!known(form.target)) errors.target = t("validation.language");
    return errors;
  }
  if (!known(form.target)) errors.target = t("validation.language");
  if (!known(form.partner)) errors.partner = t("validation.language");
  else if (!errors.target && whisperCode(form.partner, list) === whisperCode(form.target, list)) {
    errors.partner = t("validation.partner");
  }
  return errors;
}

/** Parameters for `settings.preview_languages`: only the fields of the current mode. */
export function previewRequest(form: LanguageForm): Partial<LanguageChoice> & { mode: Mode } {
  return form.mode === "media"
    ? { mode: "media", source_language: form.source, target_language: form.target }
    : { mode: "interpret", target_language: form.target, partner_language: form.partner };
}

/** Whether `preview` answers exactly the combination now in the form. */
export function previewMatches(preview: LanguagePreview | null, form: LanguageForm): boolean {
  if (!preview || preview.languages.mode !== form.mode) return false;
  const l = preview.languages;
  return form.mode === "media"
    ? l.source_language === form.source && l.target_language === form.target
    : l.target_language === form.target && l.partner_language === form.partner;
}

/**
 * An interpret partner that does not clash with `target`. Media mode does not
 * check the partner, but switching to interpret later would be refused, so a
 * new subtitle language that clashes moves the partner too (pipeline-engine.md).
 */
export function partnerWithoutConflict(target: string, partner: string, list: readonly LanguageInfo[]): string {
  const own = whisperCode(target, list);
  if (own === null || whisperCode(partner, list) !== own) return partner;
  return list.find((l) => l.whisper_code !== own)?.code ?? partner;
}

export interface LanguagesSave {
  patch: Record<string, unknown>;
  /** The recommended models are part of the patch. */
  withModels: boolean;
}

/**
 * The `settings.set` patch for the language section; call after validation.
 * With "同時改用推薦的模型" ticked, the recommended model settings are added —
 * only when `preview` is for exactly this combination (a stale or missing
 * preview saves the languages alone).
 */
export function languagesSave(form: LanguageForm, list: readonly LanguageInfo[], preview: LanguagePreview | null): LanguagesSave {
  const languages: Record<string, unknown> =
    form.mode === "media"
      ? {
          source_language: form.source,
          target_language: form.target,
          partner_language: partnerWithoutConflict(form.target, form.partner, list),
        }
      : { target_language: form.target, partner_language: form.partner };
  const withModels = form.applyRecommended && previewMatches(preview, form);
  return { patch: withModels && preview ? { ...preview.patch, ...languages } : languages, withModels };
}

// ---------------------------------------------------------------- preview display

export type RowTone = "ok" | "download" | "muted" | "warn";

export interface PreviewRow {
  label: string;
  /** Model name; null when no model is involved (e.g. translation not needed). */
  model: string | null;
  status: string;
  tone: RowTone;
}

function modelStatus(m: PreviewModel | undefined): { status: string; tone: RowTone } {
  if (!m) return { status: t("common.unknown"), tone: "muted" };
  if (m.installed === true) return { status: t("preview.installed"), tone: "ok" };
  if (m.installed === null) return { status: t("preview.installedUnknown"), tone: "muted" };
  const bytes = m.download_bytes ?? m.size_bytes;
  return bytes === null
    ? { status: t("preview.downloadUnknownSize"), tone: "download" }
    : { status: t("preview.downloadOnFirstUse", { size: formatBytes(bytes) }), tone: "download" };
}

function translationLabel(r: readonly ModelRole[]): string {
  if (r.length === 1) return r[0] === "fast" ? t("rec.fastTranslation") : t("rec.finalTranslation");
  return t("preview.translation");
}

/** The recommended models of a preview, each with its install state. */
export function previewRows(preview: LanguagePreview): PreviewRow[] {
  const rec = preview.recommendation;
  const asrRow = (label: string, model: string): PreviewRow => ({
    label,
    model,
    ...modelStatus(preview.asr.find((m) => m.model === model)),
  });
  const rows = [asrRow(t("rec.fastAsr"), rec.fast_asr.model), asrRow(t("rec.finalAsr"), rec.final_asr.model)];
  const label = t("preview.translation");
  if (!preview.translation_needed) {
    const off = preview.messages.some((m) => typeof m !== "string" && m.code === "translation_disabled");
    rows.push({ label, model: null, status: off ? t("preview.translationOff") : t("preview.notNeeded"), tone: "muted" });
  } else if (rec.translation_status === "unsupported") {
    rows.push({ label, model: null, status: t("preview.unsupported"), tone: "warn" });
  } else if (rec.translation_status === "remote" || preview.translation.length === 0) {
    rows.push({ label, model: null, status: t("preview.remote"), tone: "muted" });
  } else {
    for (const m of preview.translation) {
      rows.push({ label: translationLabel(m.roles), model: m.model, ...modelStatus(m) });
    }
  }
  return rows;
}

/** One sentence about what the first start will download. */
export function previewSummary(preview: LanguagePreview): string {
  if (preview.download_bytes === 0 && preview.download_size_known) return t("preview.allInstalled");
  if (!preview.download_size_known) return t("preview.totalUnknown", { size: formatBytes(preview.download_bytes) });
  return t("preview.total", { size: formatBytes(preview.download_bytes) });
}

/**
 * Engine notes worth showing next to the language choice: the preview's own
 * messages plus the recommendation's language-related reasons and warnings
 * (hardware notes stay on the models page).
 */
const LANGUAGE_NOTE_CODES = new Set([
  "asr_language_quality_low",
  "asr_model_language_unsupported",
  "translation_unsupported",
  "translation_not_needed",
  "source_language_auto",
  "translation_remote",
  "translation_server_unreachable",
  "translation_model_list_unavailable",
  "translation_model_language_unsupported",
  "translation_better_model_available",
]);

export function previewNotes(preview: LanguagePreview): EngineMessage[] {
  const rec = preview.recommendation;
  const related = [...rec.reasons, ...rec.warnings].filter((m) => typeof m !== "string" && LANGUAGE_NOTE_CODES.has(m.code));
  return [...preview.messages, ...related];
}
