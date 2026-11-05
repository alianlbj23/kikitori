/**
 * Interface translation: current locale, message lookup, static markup.
 *
 * Role: every user-visible string goes through `t()` / `tp()` (or the
 * `data-i18n*` attributes that `applyStatic` fills), so switching the interface
 * language is `setLocale()` followed by each page re-rendering from its state
 * (`onLocaleChange`). Dictionaries live in `src/locales/`; zh-TW is the base
 * whose keys type all the others (see `locales/types.ts`). No i18n library:
 * placeholders are `{name}`, plurals use `Intl.PluralRules`, and language /
 * region names come from `Intl.DisplayNames` instead of the dictionaries.
 */

import { DICTIONARIES, UI_LOCALES, type UiLocale } from "../locales";
import type { MessageKey, Messages, PluralKey, PluralMessage, StringKey } from "../locales/types";
import { zhTW } from "../locales/zh-TW";

export { NATIVE_NAMES, UI_LOCALES, type UiLocale } from "../locales";
export type { MessageKey, PluralKey, StringKey } from "../locales/types";

/** Stored in settings `ui.locale`: a locale, or "system" to follow the OS. */
export type UiLocalePreference = UiLocale | "system";

export const FALLBACK_LOCALE: UiLocale = "en";
const BASE_LOCALE: UiLocale = "zh-TW";

export type MessageParams = Readonly<Record<string, string | number>>;

export function isUiLocale(v: unknown): v is UiLocale {
  return typeof v === "string" && (UI_LOCALES as readonly string[]).includes(v);
}

export function isMessageKey(key: string): key is MessageKey {
  return Object.hasOwn(zhTW, key);
}

/**
 * The supported locale closest to the user's preferred languages
 * (`navigator.languages`, most preferred first).
 *
 * 中文要看文字與地區：zh-Hant／台灣／香港／澳門是繁體，其他（含單純的 `zh`）
 * 是簡體；葡萄牙語一律對到 pt-BR（介面用巴西用詞）；其餘只比對主要語言子標籤。
 * 依偏好順序找第一個對得到的，都對不到用 English。
 */
export function resolveLocale(preferred: readonly string[]): UiLocale {
  for (const tag of preferred) {
    const found = matchLocale(tag);
    if (found) return found;
  }
  return FALLBACK_LOCALE;
}

function matchLocale(tag: string): UiLocale | null {
  if (typeof tag !== "string") return null;
  const parts = tag.trim().toLowerCase().replaceAll("_", "-").split("-");
  const language = parts[0] ?? "";
  if (language === "zh") {
    const rest = parts.slice(1);
    if (rest.includes("hant") || rest.some((p) => p === "tw" || p === "hk" || p === "mo")) return "zh-TW";
    return "zh-CN";
  }
  if (language === "pt") return "pt-BR";
  // Remaining locales are identified by their language subtag alone (en, ja, ko, …).
  return UI_LOCALES.find((l) => l === language) ?? null;
}

// ---------------------------------------------------------------- current locale

let current: UiLocale = BASE_LOCALE;
const listeners = new Set<(locale: UiLocale) => void>();

export function getLocale(): UiLocale {
  return current;
}

/** Value for `<html lang>`: Chinese gets its script so fonts pick the right glyphs. */
export function htmlLangForLocale(locale: UiLocale): string {
  if (locale === "zh-TW") return "zh-Hant-TW";
  if (locale === "zh-CN") return "zh-Hans-CN";
  return locale;
}

/**
 * Switches the interface language, updates `<html lang>` and tells every
 * listener (pages re-render from their state). Listeners also run when the
 * locale does not change: callers use this to (re)apply the first render.
 */
export function setLocale(locale: UiLocale): void {
  current = locale;
  if (typeof document !== "undefined") document.documentElement.lang = htmlLangForLocale(locale);
  for (const listener of listeners) {
    try {
      listener(locale);
    } catch (e) {
      // 一個畫面重繪失敗不能讓其他畫面停在舊語系；記錄後繼續。
      console.error("locale listener failed:", e);
    }
  }
}

/** Calls `listener` after every `setLocale`; returns the unsubscribe function. */
export function onLocaleChange(listener: (locale: UiLocale) => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

// ---------------------------------------------------------------- lookup

/** Dictionaries to try, in order: the current locale, then English, then the base. */
function chain(locale: UiLocale): [UiLocale, Messages][] {
  const out: [UiLocale, Messages][] = [];
  for (const l of [locale, FALLBACK_LOCALE, BASE_LOCALE]) {
    const d = DICTIONARIES[l];
    if (d && !out.some(([seen]) => seen === l)) out.push([l, d]);
  }
  return out;
}

interface Found {
  value: string | PluralMessage;
  /** Locale of the dictionary the value came from (its plural rules apply). */
  locale: UiLocale;
}

function lookup(key: MessageKey, locale: UiLocale): Found | undefined {
  for (const [l, d] of chain(locale)) {
    const v = d[key];
    if (typeof v === "string" ? v !== "" : v !== undefined) return { value: v, locale: l };
  }
  return undefined;
}

/** Replaces `{name}` placeholders; unknown placeholders are left visible so they get noticed. */
export function format(template: string, params: MessageParams = {}): string {
  return template.replace(/\{([A-Za-z0-9_]+)\}/g, (whole, name: string) =>
    Object.hasOwn(params, name) ? String(params[name]) : whole,
  );
}

/** The message for `key` in the current locale with `{name}` placeholders filled. */
export function t(key: StringKey, params?: MessageParams): string {
  const v = lookup(key, current)?.value;
  if (typeof v !== "string") {
    console.error(`missing message ${key}`);
    return key;
  }
  return format(v, params);
}

const pluralRules = new Map<UiLocale, Intl.PluralRules>();

function pluralCategory(locale: UiLocale, count: number): Intl.LDMLPluralRule {
  let rules = pluralRules.get(locale);
  if (!rules) {
    rules = new Intl.PluralRules(locale);
    pluralRules.set(locale, rules);
  }
  return rules.select(count);
}

/**
 * A count-dependent message: picks the plural form for `count` in the current
 * locale (falling back to `other`) and fills `{count}` plus `params`.
 */
export function tp(key: PluralKey, count: number, params?: MessageParams): string {
  const found = lookup(key, current);
  if (found === undefined || typeof found.value === "string") {
    console.error(`missing plural message ${key}`);
    return key;
  }
  // 用字典所屬語系的複數規則：目前語系還沒有字典而退回 en 時，要用 en 的 one/other。
  const forms = found.value;
  const form = forms[pluralCategory(found.locale, count)] ?? forms.other;
  return format(form, { ...params, count });
}

/**
 * Looks up a key that is only known at runtime (e.g. `engine.<code>` from an
 * engine message); null when the dictionary has no such plain-string key.
 */
export function tDynamic(key: string, params?: MessageParams): string | null {
  if (!isMessageKey(key)) return null;
  const v = lookup(key, current)?.value;
  return typeof v === "string" ? format(v, params) : null;
}

// ---------------------------------------------------------------- names from Intl

const languageNames = new Map<UiLocale, Intl.DisplayNames>();
const regionNamesCache = new Map<UiLocale, Intl.DisplayNames>();
const LANGUAGE_TAG = /^[A-Za-z]{2,3}(-[A-Za-z0-9]{1,8})*$/;

function displayNames(cache: Map<UiLocale, Intl.DisplayNames>, type: "language" | "region"): Intl.DisplayNames {
  let names = cache.get(current);
  if (!names) {
    names = new Intl.DisplayNames([current], { type, fallback: "code" });
    cache.set(current, names);
  }
  return names;
}

/**
 * A language code's name in the interface language (`ja` → 日文 / Japanese).
 * Our subtitle codes zh-TW / zh-CN mean the scripts, so they are named as
 * Traditional / Simplified Chinese rather than "Chinese (Taiwan)".
 */
export function languageName(code: string): string {
  if (!LANGUAGE_TAG.test(code)) return code;
  const tag = code === "zh-TW" ? "zh-Hant" : code === "zh-CN" ? "zh-Hans" : code;
  try {
    return displayNames(languageNames, "language").of(tag) ?? code;
  } catch (e) {
    // DisplayNames 對格式正確但不合法的代碼會丟 RangeError；顯示原始代碼即可。
    console.warn(`no display name for language ${code}:`, e);
    return code;
  }
}

/** A region code's name in the interface language (`KR` → 南韓 / South Korea). */
export function regionName(code: string): string {
  const region = code === "UK" ? "GB" : code;
  if (!/^[A-Z]{2}$|^\d{3}$/.test(region)) return code;
  try {
    return displayNames(regionNamesCache, "region").of(region) ?? code;
  } catch (e) {
    console.warn(`no display name for region ${code}:`, e);
    return code;
  }
}

/** Joins list items with the locale's separator (、 in Chinese, ", " in English). */
export function joinList(items: readonly string[]): string {
  return items.join(t("common.listSeparator"));
}

// ---------------------------------------------------------------- static markup

const ATTRIBUTES = [
  ["data-i18n-title", "title"],
  ["data-i18n-placeholder", "placeholder"],
  ["data-i18n-aria-label", "aria-label"],
] as const;

function staticText(key: string, countAttr: string | null): string | null {
  if (!isMessageKey(key)) {
    console.error(`unknown i18n key in markup: ${key}`);
    return null;
  }
  const v = lookup(key, current)?.value;
  if (typeof v === "string") return format(v);
  const count = countAttr === null ? Number.NaN : Number(countAttr);
  if (v === undefined || !Number.isFinite(count)) {
    console.error(`plural key ${key} in markup needs a numeric data-i18n-count`);
    return null;
  }
  return tp(key as PluralKey, count);
}

/**
 * Fills static markup under `root` from the dictionary:
 * `data-i18n` (text; plural keys also take `data-i18n-count`),
 * `data-i18n-title`, `data-i18n-placeholder`, `data-i18n-aria-label`, and
 * `data-i18n-language` (text = that language code's name via `Intl.DisplayNames`).
 */
export function applyStatic(root: ParentNode): void {
  for (const el of Array.from(root.querySelectorAll<HTMLElement>("[data-i18n]"))) {
    const text = staticText(el.dataset.i18n ?? "", el.getAttribute("data-i18n-count"));
    if (text !== null) el.textContent = text;
  }
  for (const [data, attr] of ATTRIBUTES) {
    for (const el of Array.from(root.querySelectorAll<HTMLElement>(`[${data}]`))) {
      const text = staticText(el.getAttribute(data) ?? "", null);
      if (text !== null) el.setAttribute(attr, text);
    }
  }
  for (const el of Array.from(root.querySelectorAll<HTMLElement>("[data-i18n-language]"))) {
    el.textContent = languageName(el.dataset.i18nLanguage ?? "");
  }
}

/** The concrete locale for a stored preference ("system" follows the OS languages). */
export function localeForPreference(pref: UiLocalePreference, preferred: readonly string[]): UiLocale {
  return pref === "system" ? resolveLocale(preferred) : pref;
}

/** The OS language list as the webview reports it (most preferred first). */
export function systemLanguages(): readonly string[] {
  if (typeof navigator === "undefined") return [];
  return navigator.languages?.length ? navigator.languages : [navigator.language];
}
