/**
 * Types shared by every UI dictionary.
 *
 * Role: zh-TW (`zh-TW.ts`) is the base dictionary; its keys define `MessageKey`
 * and every other locale file is typed as `Messages`, so a missing or extra key
 * in any locale is a type error (`npm run build` / `tsc` fails), not a runtime
 * surprise.
 */

import type { zhTW } from "./zh-TW";

/**
 * A message that depends on a count, selected with `Intl.PluralRules`.
 * 每個語系必須提供自己語言的所有複數類別（例如 en 要 one/other、ru 要
 * one/few/many/other）；`other` 一律必填，因為 PluralRules 一定會回它。
 * 測試（`locales.test.ts`）檢查各語系的類別齊全。
 */
export interface PluralMessage {
  readonly zero?: string;
  readonly one?: string;
  readonly two?: string;
  readonly few?: string;
  readonly many?: string;
  readonly other: string;
}

type Base = typeof zhTW;

export type MessageKey = keyof Base;

/** Keys whose message is a plain string (use `t`). */
export type StringKey = { [K in MessageKey]: Base[K] extends string ? K : never }[MessageKey];

/** Keys whose message has plural forms (use `tp`). */
export type PluralKey = Exclude<MessageKey, StringKey>;

/** A complete dictionary: exactly the base keys, plain or plural like the base. */
export type Messages = { readonly [K in MessageKey]: Base[K] extends string ? string : PluralMessage };
