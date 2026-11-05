/**
 * The user glossary (`translation.glossary`): reading, checking, CSV, engine errors.
 *
 * Role: pure logic behind the settings page's glossary editor
 * (competitive-features.md F3). The engine owns the rules and validates
 * authoritatively (`src/kikitori/config/settings.py` `_parse_glossary`,
 * `translate/glossary.py` `GlossaryEntry`); this module mirrors them so a
 * mistake is pointed at the right row before saving, maps the engine's error
 * path (`translation.glossary.entries[3].source`) back to that row when it
 * still rejects something, and converts entries to and from CSV for
 * spreadsheets. Messages come from the i18n dictionary.
 *
 * The mirror is deliberately close but not perfect: duplicate detection uses
 * NFKC + an approximation of Python's `casefold()`, and "printable" follows
 * Unicode general categories as the JavaScript engine knows them. Whatever
 * slips through is reported by the engine on the same row.
 */

import { csvField } from "./export";
import { t } from "./i18n";
import { isLanguageCode } from "./languages";
import type { GlossaryEntry, GlossarySettings } from "./types";

/** Engine limits (`translate/glossary.py` `MAX_USER_ENTRIES`, `MAX_SOURCE_CHARS`, `MAX_TARGET_CHARS`). */
export const GLOSSARY_MAX_ENTRIES = 500;
export const GLOSSARY_MAX_SOURCE_CHARS = 100;
export const GLOSSARY_MAX_TARGET_CHARS = 200;
/** A glossary CSV is a few dozen KB at most; anything far larger is the wrong file. */
export const GLOSSARY_MAX_CSV_BYTES = 1024 * 1024;

export const DEFAULT_GLOSSARY: GlossarySettings = { builtin: true, entries: [] };

export type GlossaryField = keyof GlossaryEntry;
export type GlossaryRowErrors = Partial<Record<GlossaryField, string>>;

export interface GlossaryErrors {
  /** Problems per row index (only rows with a problem are present). */
  readonly rows: ReadonlyMap<number, GlossaryRowErrors>;
  /** Problem with the list as a whole (too many entries); null when fine. */
  readonly list: string | null;
}

// ---------------------------------------------------------------- reading settings

type Obj = Record<string, unknown>;

function isEntry(v: unknown): v is GlossaryEntry {
  if (typeof v !== "object" || v === null || Array.isArray(v)) return false;
  const o = v as Obj;
  return (
    typeof o.source === "string" &&
    o.source.length <= 1000 &&
    typeof o.target === "string" &&
    o.target.length <= 1000 &&
    (o.target_language === null || o.target_language === undefined || isLanguageCode(o.target_language))
  );
}

/**
 * `settings.translation.glossary` as the editor shows it. Absent (an engine
 * before the glossary) means the defaults without a warning; malformed parts
 * are reported through `warn` and only the well-formed entries are kept, so
 * the rest of the list is not lost on the next save.
 */
export function glossaryFromSettings(raw: unknown, warn: (field: string) => void): GlossarySettings {
  if (raw === undefined) return DEFAULT_GLOSSARY;
  if (typeof raw !== "object" || raw === null || Array.isArray(raw)) {
    warn("translation.glossary");
    return DEFAULT_GLOSSARY;
  }
  const o = raw as Obj;
  let builtin = DEFAULT_GLOSSARY.builtin;
  if (typeof o.builtin === "boolean") builtin = o.builtin;
  else if (o.builtin !== undefined) warn("translation.glossary.builtin");
  let entries: GlossaryEntry[] = [];
  if (Array.isArray(o.entries)) {
    entries = o.entries.filter(isEntry).map((e) => ({
      source: e.source,
      target: e.target,
      target_language: e.target_language ?? null,
    }));
    if (entries.length !== o.entries.length) warn("translation.glossary.entries");
  } else if (o.entries !== undefined) {
    warn("translation.glossary.entries");
  }
  return { builtin, entries };
}

// ---------------------------------------------------------------- checking

/**
 * The engine's matching key: NFKC, case folded, trimmed (`glossary.match_key`).
 * `toUpperCase().toLowerCase()` folds like Python's `casefold()` for the cases
 * that matter here (ß → ss), and the final sigma is folded to σ as casefold does.
 */
export function matchKey(text: string): string {
  return text.normalize("NFKC").toUpperCase().toLowerCase().replaceAll("ς", "σ").trim();
}

/** Characters Python's `str.isprintable()` rejects: controls, format, separators other than the ASCII space. */
const NOT_PRINTABLE = /[\p{Cc}\p{Cf}\p{Cs}\p{Co}\p{Cn}\p{Zl}\p{Zp}]|(?! )\p{Zs}/u;

/** Code points, like Python's `len()` (the limits are counted that way). */
function length(text: string): number {
  return Array.from(text).length;
}

/** The engine's `_check_text` for one field; null when acceptable. */
function textError(value: string, max: number): string | null {
  if (value.trim() === "") return t("glossary.error.empty");
  if (value !== value.trim()) return t("glossary.error.spaces");
  if (length(value) > max) return t("glossary.error.tooLong", { max });
  if (NOT_PRINTABLE.test(value)) return t("glossary.error.control");
  return null;
}

/**
 * Checks every entry like the engine would; `languages` is the engine's
 * language list (null while unknown: then only the code format is checked).
 * A duplicate (same matching key and same subtitle language as an earlier
 * row) is reported on the later row, naming the earlier one (1-based).
 */
export function validateGlossary(entries: readonly GlossaryEntry[], languages: readonly string[] | null): GlossaryErrors {
  const rows = new Map<number, GlossaryRowErrors>();
  const seen = new Map<string, number>();
  entries.forEach((entry, i) => {
    const errors: GlossaryRowErrors = {};
    const source = textError(entry.source, GLOSSARY_MAX_SOURCE_CHARS);
    if (source) errors.source = source;
    const target = textError(entry.target, GLOSSARY_MAX_TARGET_CHARS);
    if (target) errors.target = target;
    const lang = entry.target_language;
    if (lang !== null && !(languages ? languages.includes(lang) : isLanguageCode(lang))) {
      errors.target_language = t("glossary.error.language");
    }
    if (!source) {
      const key = matchKey(entry.source);
      if (key === "") {
        errors.source = t("glossary.error.empty");
      } else {
        const dupKey = `${lang ?? ""}\u0000${key}`;
        const first = seen.get(dupKey);
        if (first === undefined) seen.set(dupKey, i);
        else errors.source = t("glossary.error.duplicate", { row: first + 1 });
      }
    }
    if (Object.keys(errors).length > 0) rows.set(i, errors);
  });
  const list =
    entries.length > GLOSSARY_MAX_ENTRIES
      ? t("glossary.error.tooMany", { max: GLOSSARY_MAX_ENTRIES, count: entries.length })
      : null;
  return { rows, list };
}

export function hasGlossaryErrors(errors: GlossaryErrors): boolean {
  return errors.list !== null || errors.rows.size > 0;
}

/**
 * Partial settings that save the glossary. `settings.set` deep-merges objects
 * but replaces lists, so the whole entry list is always sent.
 */
export function glossaryPatch(glossary: GlossarySettings): Record<string, unknown> {
  return {
    translation: {
      glossary: {
        builtin: glossary.builtin,
        entries: glossary.entries.map((e) => ({ source: e.source, target: e.target, target_language: e.target_language })),
      },
    },
  };
}

/** Where an engine `invalid_settings` message about the glossary belongs. */
export type GlossaryErrorTarget =
  | { kind: "entry"; index: number; field: GlossaryField | null }
  | { kind: "list" }
  | { kind: "builtin" };

const ENTRY_PATH = /^translation\.glossary\.entries\[(\d{1,6})\](?:\.([A-Za-z_]+))?$/;

/**
 * Maps `"<path>: <problem>"` from the engine to a row and field, a list-level
 * or a switch-level problem; null when the message is about something else.
 * Unknown keys inside an entry (`entries[3].note`) land on the row as a whole.
 */
export function glossaryErrorForEnginePath(message: string): GlossaryErrorTarget | null {
  const path = message.split(":", 1)[0]?.trim() ?? "";
  const m = ENTRY_PATH.exec(path);
  if (m) {
    const name = m[2] ?? null;
    const field = name === "source" || name === "target" || name === "target_language" ? name : null;
    return { kind: "entry", index: Number.parseInt(m[1]!, 10), field };
  }
  if (path === "translation.glossary.entries" || path === "translation.glossary") return { kind: "list" };
  if (path === "translation.glossary.builtin") return { kind: "builtin" };
  return null;
}

// ---------------------------------------------------------------- CSV

const BOM = "﻿";
const CRLF = "\r\n";
export const GLOSSARY_CSV_HEADER = ["source", "target", "target_language"] as const;
/** Language cell values meaning "every subtitle language". */
const ALL_LANGUAGES = new Set(["", "*", "all"]);
/** Same rule as `csvField`'s formula guard, so an exported cell reads back unchanged. */
const FORMULA_START = /^(?:[=+@\t\r]|-(?!\s|$))/;

/** UTF-8 CSV with BOM (Excel), CRLF rows, `target_language` empty for "every language". */
export function glossaryToCsv(entries: readonly GlossaryEntry[]): string {
  const rows = entries.map((e) => [e.source, e.target, e.target_language ?? ""].map(csvField).join(","));
  return BOM + [GLOSSARY_CSV_HEADER.join(","), ...rows].join(CRLF) + CRLF;
}

interface CsvRecord {
  /** 1-based line where the record starts (for messages). */
  line: number;
  cells: string[];
}

/**
 * The delimiter of a spreadsheet's CSV: Excel writes `;` (or a tab) where the
 * decimal separator is a comma, so the first line decides.
 */
function detectDelimiter(text: string): string {
  const firstLine = text.split(/\r\n|\n|\r/, 1)[0] ?? "";
  const outsideQuotes = firstLine.replace(/"(?:[^"]|"")*"/g, "");
  const count = (d: string) => outsideQuotes.split(d).length - 1;
  const candidates = [",", ";", "\t"];
  return candidates.reduce((best, d) => (count(d) > count(best) ? d : best), ",");
}

/** RFC 4180 records: quoted fields may hold the delimiter, `""` and line breaks. */
function parseCsvRecords(text: string): CsvRecord[] {
  const delimiter = detectDelimiter(text);
  const records: CsvRecord[] = [];
  let cells: string[] = [];
  let cell = "";
  let quoted = false;
  let wasQuoted = false;
  /** Spaces after a closing quote: padding if the field ends there, kept if text follows. */
  let pending = "";
  let line = 1;
  let recordLine = 1;
  const endCell = () => {
    cells.push(cell);
    cell = "";
    wasQuoted = false;
    pending = "";
  };
  const endRecord = () => {
    endCell();
    records.push({ line: recordLine, cells });
    cells = [];
  };
  for (let i = 0; i < text.length; i++) {
    const c = text[i]!;
    if (quoted) {
      if (c === '"') {
        if (text[i + 1] === '"') {
          cell += '"';
          i++;
        } else {
          quoted = false;
        }
      } else {
        if (c === "\n" || (c === "\r" && text[i + 1] !== "\n")) line++;
        cell += c;
      }
      continue;
    }
    if (c === '"' && cell.trim() === "") {
      quoted = true;
      wasQuoted = true;
      cell = "";
    } else if (c === delimiter) {
      endCell();
    } else if (c === "\r" || c === "\n") {
      if (c === "\r" && text[i + 1] === "\n") i++;
      endRecord();
      line++;
      recordLine = line;
    } else if (wasQuoted && (c === " " || c === "\t")) {
      pending += c;
    } else {
      cell += pending + c;
      pending = "";
    }
  }
  if (cell !== "" || cells.length > 0 || wasQuoted) endRecord();
  return records;
}

/** Undoes the export's formula guard (`'=x` → `=x`) and trims spreadsheet padding. */
function cleanCell(raw: string | undefined): string {
  const v = (raw ?? "").trim();
  return v.startsWith("'") && FORMULA_START.test(v.slice(1)) ? v.slice(1) : v;
}

export interface GlossaryImport {
  entries: GlossaryEntry[];
  /** 1-based lines that are not `source,target[,language]` (missing target). */
  badLines: number[];
}

/**
 * Reads a glossary CSV: with or without the BOM, comma / semicolon / tab
 * separated, with a header row (columns found by name, any order) or without
 * one (source, target, language). Blank lines are skipped; an empty, `*` or
 * `all` language means every subtitle language. Content is not checked here —
 * imported rows go through `validateGlossary` like typed ones.
 */
export function parseGlossaryCsv(text: string): GlossaryImport {
  const records = parseCsvRecords(text.startsWith(BOM) ? text.slice(1) : text).filter((r) =>
    r.cells.some((c) => c.trim() !== ""),
  );
  let columns = { source: 0, target: 1, target_language: 2 as number | null };
  const header = records[0]?.cells.map((c) => c.trim().toLowerCase());
  if (header && header.includes("source") && header.includes("target")) {
    const lang = header.findIndex((c) => c === "target_language" || c === "language");
    columns = { source: header.indexOf("source"), target: header.indexOf("target"), target_language: lang < 0 ? null : lang };
    records.shift();
  }
  const entries: GlossaryEntry[] = [];
  const badLines: number[] = [];
  for (const r of records) {
    const source = cleanCell(r.cells[columns.source]);
    const target = cleanCell(r.cells[columns.target]);
    if (source === "" || target === "") {
      badLines.push(r.line);
      continue;
    }
    const rawLang = columns.target_language === null ? "" : cleanCell(r.cells[columns.target_language]);
    const target_language = ALL_LANGUAGES.has(rawLang.toLowerCase()) ? null : rawLang;
    entries.push({ source, target, target_language });
  }
  return { entries, badLines };
}

export interface GlossaryMerge {
  entries: GlossaryEntry[];
  added: number;
  /** Imported rows identical to one already in the table (same key, language and target). */
  skipped: number;
}

/**
 * Appends imported entries. Exact repeats are skipped; a different target for
 * the same term is kept so validation shows the conflict and the user picks.
 */
export function mergeGlossary(existing: readonly GlossaryEntry[], imported: readonly GlossaryEntry[]): GlossaryMerge {
  const id = (e: GlossaryEntry) => `${e.target_language ?? ""}\u0000${matchKey(e.source)}\u0000${e.target}`;
  const known = new Set(existing.map(id));
  const entries = existing.slice();
  let skipped = 0;
  for (const e of imported) {
    const key = id(e);
    if (known.has(key)) {
      skipped++;
      continue;
    }
    known.add(key);
    entries.push(e);
  }
  return { entries, added: entries.length - existing.length, skipped };
}

/** File name for the export (Rust `validate_transcript_name`: ASCII, `.csv`). */
export function glossaryFileName(now: Date): string {
  const p = (n: number) => String(n).padStart(2, "0");
  const date = `${now.getFullYear()}-${p(now.getMonth() + 1)}-${p(now.getDate())}`;
  const time = `${p(now.getHours())}${p(now.getMinutes())}${p(now.getSeconds())}`;
  return `glossary_${date}_${time}.csv`;
}
