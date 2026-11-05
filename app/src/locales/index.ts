/**
 * Registry of UI locales and their dictionaries.
 *
 * Role: the single list of interface languages (docs/specs/languages-i18n.md
 * 「介面多語系」), their names in their own language, and the dictionary of
 * each. Adding a locale = add `<code>.ts` typed as `Messages` and register it in
 * `DICTIONARIES`; a locale without a dictionary yet falls back to English at
 * runtime (see `lib/i18n.ts`).
 */

import { en } from "./en";
import type { Messages } from "./types";
import { zhTW } from "./zh-TW";

export const UI_LOCALES = [
  "zh-TW",
  "zh-CN",
  "en",
  "ja",
  "ko",
  "es",
  "fr",
  "de",
  "pt-BR",
  "ru",
  "vi",
  "th",
  "id",
] as const;

export type UiLocale = (typeof UI_LOCALES)[number];

/** Each locale's name in its own language, for the interface-language menu. */
export const NATIVE_NAMES: Readonly<Record<UiLocale, string>> = {
  "zh-TW": "繁體中文（台灣）",
  "zh-CN": "简体中文",
  en: "English",
  ja: "日本語",
  ko: "한국어",
  es: "Español",
  fr: "Français",
  de: "Deutsch",
  "pt-BR": "Português (Brasil)",
  ru: "Русский",
  vi: "Tiếng Việt",
  th: "ไทย",
  id: "Bahasa Indonesia",
};

/** Dictionaries that exist so far; the rest use the English fallback. */
export const DICTIONARIES: Readonly<Partial<Record<UiLocale, Messages>>> = {
  "zh-TW": zhTW,
  en,
};
