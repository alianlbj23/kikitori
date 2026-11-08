/**
 * Registry of UI locales and their dictionaries.
 *
 * Role: the single list of interface languages (docs/specs/languages-i18n.md
 * 「介面多語系」), their names in their own language, and the dictionary of
 * each. Adding a locale = add `<code>.ts` typed as `Messages` and register it in
 * `DICTIONARIES` (typed as a complete record, so a locale cannot be listed
 * without its dictionary).
 */

import { de } from "./de";
import { en } from "./en";
import { es } from "./es";
import { fr } from "./fr";
import { id } from "./id";
import { ja } from "./ja";
import { ko } from "./ko";
import { ptBR } from "./pt-BR";
import { ru } from "./ru";
import { th } from "./th";
import type { Messages } from "./types";
import { vi } from "./vi";
import { zhCN } from "./zh-CN";
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

/** Every UI locale has a complete dictionary (missing keys are type errors). */
export const DICTIONARIES: Readonly<Record<UiLocale, Messages>> = {
  "zh-TW": zhTW,
  "zh-CN": zhCN,
  en,
  ja,
  ko,
  es,
  fr,
  de,
  "pt-BR": ptBR,
  ru,
  vi,
  th,
  id,
};
