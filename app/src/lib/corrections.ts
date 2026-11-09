/**
 * ASR correction rules (`translation.glossary.corrections`) as the glossary
 * page edits them: one rule per line, `wrong => right`.
 *
 * Role: pure logic behind the "原文校正" box (docs/specs/v0.3-features.md G4).
 * The engine owns the rules (`src/kikitori/pipeline/corrections.py`
 * `Correction`, `config/settings.py` `_parse_corrections`) and validates
 * authoritatively; this module mirrors them so a mistake is pointed at its line
 * before saving. A text box rather than a table: rules are short pairs that
 * people paste and edit in bulk, and the line number is enough to find one.
 */

import { matchKey } from "./match-key";
import { t } from "./i18n";
import type { CorrectionEntry } from "./types";

/** Engine limits (`pipeline/corrections.py`). */
export const CORRECTIONS_MAX = 200;
export const CORRECTION_MAX_CHARS = 100;
/** Written between the two sides; "→" is accepted too when reading. */
export const CORRECTION_SEPARATOR = "=>";
const SEPARATORS = /\s*(?:=>|→)\s*/u;

export interface CorrectionLineError {
  /** 1-based line in the text box. */
  readonly line: number;
  readonly message: string;
}

export interface ParsedCorrections {
  readonly entries: CorrectionEntry[];
  readonly errors: CorrectionLineError[];
}

/** The rules as the text box shows them. */
export function correctionsToText(entries: readonly CorrectionEntry[]): string {
  return entries.map((e) => `${e.wrong} ${CORRECTION_SEPARATOR} ${e.right}`).join("\n");
}

function hasControl(text: string): boolean {
  return /\p{Cc}/u.test(text);
}

/** Reads the text box: blank lines are skipped; every other line must be `wrong => right`. */
export function parseCorrectionsText(text: string): ParsedCorrections {
  const entries: CorrectionEntry[] = [];
  const errors: CorrectionLineError[] = [];
  const seen = new Map<string, number>();
  text.split(/\r\n|\r|\n/).forEach((raw, i) => {
    const line = i + 1;
    if (raw.trim() === "") return;
    const parts = raw.split(SEPARATORS);
    const [wrongRaw, rightRaw] = parts;
    if (parts.length !== 2 || wrongRaw === undefined || rightRaw === undefined) {
      errors.push({ line, message: t("glossary.correctionError.format") });
      return;
    }
    const wrong = wrongRaw.trim();
    const right = rightRaw.trim();
    if (wrong === "" || right === "") {
      errors.push({ line, message: t("glossary.correctionError.format") });
      return;
    }
    if (Array.from(wrong).length > CORRECTION_MAX_CHARS || Array.from(right).length > CORRECTION_MAX_CHARS) {
      errors.push({ line, message: t("glossary.correctionError.tooLong", { max: CORRECTION_MAX_CHARS }) });
      return;
    }
    if (hasControl(wrong) || hasControl(right)) {
      errors.push({ line, message: t("glossary.error.control") });
      return;
    }
    if (wrong === right) {
      errors.push({ line, message: t("glossary.correctionError.same") });
      return;
    }
    const key = matchKey(wrong);
    const first = seen.get(key);
    if (first !== undefined) {
      errors.push({ line, message: t("glossary.correctionError.duplicate", { line: first }) });
      return;
    }
    seen.set(key, line);
    entries.push({ wrong, right });
  });
  if (entries.length > CORRECTIONS_MAX) {
    errors.push({ line: 0, message: t("glossary.correctionError.tooMany", { max: CORRECTIONS_MAX }) });
  }
  return { entries, errors };
}

/** `translation.glossary.corrections` from the engine; malformed items are dropped and reported. */
export function correctionsFromSettings(raw: unknown, warn: (field: string) => void): CorrectionEntry[] {
  if (raw === undefined) return [];
  if (!Array.isArray(raw)) {
    warn("translation.glossary.corrections");
    return [];
  }
  const entries = raw.filter(
    (v): v is CorrectionEntry =>
      typeof v === "object" &&
      v !== null &&
      typeof (v as Record<string, unknown>).wrong === "string" &&
      typeof (v as Record<string, unknown>).right === "string",
  );
  if (entries.length !== raw.length) warn("translation.glossary.corrections");
  return entries.map((e) => ({ wrong: e.wrong, right: e.right }));
}
