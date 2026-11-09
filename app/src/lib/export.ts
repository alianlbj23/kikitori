/**
 * Transcript export: SRT, WebVTT, plain text and CSV from the subtitle log.
 *
 * Role: pure functions from `SubtitleLine`s to file contents and names
 * (docs/specs/competitive-features.md F1). Rust only validates the name and
 * writes the file (`transcript_save`), so every format rule is here and tested.
 *
 * - Only complete lines (`isFinal`) are exported: partial text is still
 *   changing and would leave half-recognised sentences in the file.
 * - Timecodes come from `start_sample` / `end_sample` at 16 kHz, counted from
 *   the start of the session; a cue always ends after it starts.
 * - Bilingual cues hold two lines (original, then translation). Without a
 *   translation (not translated, or "顯示翻譯" off) the original stands in.
 * - Right-to-left text (Arabic, Hebrew…) is wrapped in RLM marks in the
 *   subtitle and text formats, so players that lay lines out left to right
 *   still put trailing punctuation on the correct side.
 * - Who said it: with a namer given, each cue's first line starts with
 *   "<name>: " and CSV gets a `speaker` column, so the file still says who said
 *   what — "You"/"Them" in interpret mode (docs/specs/v0.3-features.md G3), the
 *   speaker labels in media mode (docs/specs/speaker-labels.md).
 */

import type { Mode, Speaker } from "./types";
import type { SubtitleLine } from "./subtitles";

export type ExportFormat = "srt" | "vtt" | "txt" | "csv";
/** What each cue contains. */
export type ExportContent = "both" | "original" | "translation";

export const EXPORT_FORMATS: readonly ExportFormat[] = ["srt", "vtt", "txt", "csv"];
export const EXPORT_CONTENTS: readonly ExportContent[] = ["both", "original", "translation"];
/** Engine audio sample rate (pipeline-engine.md): samples → milliseconds. */
export const SAMPLE_RATE = 16_000;
/** Length given to a cue whose end is not after its start. */
export const MIN_CUE_MS = 1000;
/**
 * Lines kept for export in the main window. The visible log keeps 300; a
 * whole evening of speech (≈ 1 line every 2–3 s) fits well within this.
 */
export const EXPORT_MAX_LINES = 20_000;

const RLM = "\u200F";
const BOM = "\uFEFF";
const CRLF = "\r\n";

/** One exported cue: time range and the text lines to show. */
export interface Cue {
  startMs: number;
  endMs: number;
  /** One or two lines (original and/or translation), never empty. */
  text: string[];
  original: string;
  translation: string;
  sourceLanguage: string;
  targetLanguage: string;
  /** Who said it, as named by the export's `LineNamer`; null = unnamed. */
  who: string | null;
}

/** The name to put in front of a line in an export, or null to leave it unnamed. */
export type LineNamer = (line: SubtitleLine) => string | null;

/** Interpret sessions: the localised "You" / "Them" (G3). */
export function interpretNamer(names: Readonly<Record<Speaker, string>>): LineNamer {
  return (line) => (line.speaker ? names[line.speaker] : null);
}

/** Collapses line breaks, tabs and other control characters into single spaces. */
export function oneLine(text: string): string {
  return text.replace(/[\u0000-\u001F\u007F\u2028\u2029]+/g, " ").replace(/\s+/g, " ").trim();
}

const RTL_CHAR = /[\u0590-\u08FF\uFB1D-\uFDFF\uFE70-\uFEFF]/;
const STRONG_CHAR = /[\p{L}]/u;

/** True when the first letter of `text` belongs to a right-to-left script. */
export function startsRtl(text: string): boolean {
  for (const ch of text) {
    if (RTL_CHAR.test(ch)) return true;
    if (STRONG_CHAR.test(ch)) return false;
  }
  return false;
}

/** RLM on both ends of right-to-left text; other text unchanged. */
export function bidiSafe(text: string): string {
  return startsRtl(text) ? `${RLM}${text}${RLM}` : text;
}

function samplesToMs(samples: number): number {
  return Math.round((samples * 1000) / SAMPLE_RATE);
}

/**
 * Final lines as cues for `content`, in utterance order; empty ones are left
 * out. With `nameOf`, a line it names starts with "<name>: ".
 */
export function cuesFromLines(
  lines: readonly SubtitleLine[],
  content: ExportContent,
  nameOf: LineNamer | null = null,
): Cue[] {
  const cues: Cue[] = [];
  for (const line of lines) {
    if (!line.isFinal) continue;
    const original = oneLine(line.original);
    const translation = oneLine(line.translation);
    const text =
      content === "original"
        ? [original]
        : content === "translation"
          ? [translation || original]
          : [original, translation];
    const shown = text.filter((s) => s !== "");
    if (shown.length === 0) continue;
    const who = nameOf ? nameOf(line) : null;
    if (who) shown[0] = `${oneLine(who)}: ${shown[0]}`;
    const startMs = samplesToMs(line.startSample);
    const endMs = samplesToMs(line.endSample);
    cues.push({
      startMs,
      endMs: endMs > startMs ? endMs : startMs + MIN_CUE_MS,
      text: shown,
      original,
      translation,
      sourceLanguage: line.sourceLanguage,
      targetLanguage: line.targetLanguage,
      who: who ? oneLine(who) : null,
    });
  }
  return cues;
}

function pad(n: number, width: number): string {
  return String(n).padStart(width, "0");
}

/** `HH:MM:SS<sep>mmm` (hours grow past 99 instead of wrapping). */
export function formatTimestamp(ms: number, sep: "," | "."): string {
  const total = Math.max(0, Math.round(ms));
  const h = Math.floor(total / 3_600_000);
  const m = Math.floor(total / 60_000) % 60;
  const s = Math.floor(total / 1000) % 60;
  return `${pad(h, 2)}:${pad(m, 2)}:${pad(s, 2)}${sep}${pad(total % 1000, 3)}`;
}

/** SubRip: numbered cues, `,` before milliseconds, blank line between cues. */
export function toSrt(cues: readonly Cue[]): string {
  return cues
    .map((cue, i) =>
      [
        String(i + 1),
        `${formatTimestamp(cue.startMs, ",")} --> ${formatTimestamp(cue.endMs, ",")}`,
        // SRT has no escaping; an arrow in the text could be read as a timing line.
        ...cue.text.map((s) => bidiSafe(s.replaceAll("-->", "->"))),
        "",
      ].join(CRLF),
    )
    .join(CRLF);
}

/** WebVTT cue text: `&`, `<` and `>` are entities (which also rules out `-->`). */
export function escapeVtt(text: string): string {
  return text.replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;");
}

/** WebVTT: `WEBVTT` header, `.` before milliseconds, numbered cue identifiers. */
export function toVtt(cues: readonly Cue[]): string {
  const blocks = cues.map((cue, i) =>
    [
      String(i + 1),
      `${formatTimestamp(cue.startMs, ".")} --> ${formatTimestamp(cue.endMs, ".")}`,
      ...cue.text.map((s) => bidiSafe(escapeVtt(s))),
    ].join("\n"),
  );
  return ["WEBVTT", ...blocks].join("\n\n") + "\n";
}

/** Plain text: `[HH:MM:SS] original`, the translation on the next line, a blank line between. */
export function toTxt(cues: readonly Cue[]): string {
  return cues
    .map((cue) => {
      const time = formatTimestamp(cue.startMs, ".").slice(0, 8);
      const [first, ...rest] = cue.text.map(bidiSafe);
      return [`[${time}] ${first}`, ...rest, ""].join(CRLF);
    })
    .join(CRLF);
}

/**
 * One CSV field (RFC 4180): quoted when it holds a comma, quote or line break,
 * quotes doubled. Text that a spreadsheet would run as a formula (`=`, `+`,
 * `@`, tab, CR, or `-` not followed by a space) gets a leading apostrophe —
 * subtitles come from audio anyone can speak into. "- dialogue" stays as is.
 */
export function csvField(value: string): string {
  const guarded = /^(?:[=+@\t\r]|-(?!\s|$))/.test(value) ? `'${value}` : value;
  return /[",\r\n]/.test(guarded) ? `"${guarded.replaceAll('"', '""')}"` : guarded;
}

/** CSV for spreadsheets: UTF-8 BOM (Excel otherwise guesses the code page), CRLF rows. */
export function toCsv(cues: readonly Cue[], content: ExportContent): string {
  const header = ["index", "start", "end", "source_language", "target_language"];
  // Who said each line, as its own column (the text stays unprefixed).
  const withSpeaker = cues.some((cue) => cue.who !== null);
  if (withSpeaker) header.push("speaker");
  if (content !== "translation") header.push("original");
  if (content !== "original") header.push("translation");
  const rows = cues.map((cue, i) => {
    const row = [
      String(i + 1),
      formatTimestamp(cue.startMs, "."),
      formatTimestamp(cue.endMs, "."),
      cue.sourceLanguage,
      cue.targetLanguage,
    ];
    if (withSpeaker) row.push(cue.who ?? "");
    if (content !== "translation") row.push(cue.original);
    if (content !== "original") row.push(content === "translation" ? cue.translation || cue.original : cue.translation);
    return row.map(csvField).join(",");
  });
  return BOM + [header.join(","), ...rows].join(CRLF) + CRLF;
}

/** File contents of `lines` in `format`; `nameOf` says who said each line. */
export function exportTranscript(
  lines: readonly SubtitleLine[],
  format: ExportFormat,
  content: ExportContent,
  nameOf: LineNamer | null = null,
): string {
  switch (format) {
    case "srt":
      return toSrt(cuesFromLines(lines, content, nameOf));
    case "vtt":
      return toVtt(cuesFromLines(lines, content, nameOf));
    case "txt":
      return toTxt(cuesFromLines(lines, content, nameOf));
    case "csv": {
      // CSV keeps the text unprefixed and puts the speaker in its own column.
      const named = cuesFromLines(lines, content, nameOf);
      const plain = cuesFromLines(lines, content);
      return toCsv(
        plain.map((cue, i) => ({ ...cue, who: named[i]?.who ?? null })),
        content,
      );
    }
  }
}

/** Whether there is anything to export (at least one complete line with text). */
export function hasExportableLines(lines: readonly SubtitleLine[]): boolean {
  return cuesFromLines(lines, "both").length > 0;
}

/**
 * `YYYY-MM-DD_HHMMSS_<mode>.<ext>` in local time, from when the session
 * started. Matches what Rust's `validate_transcript_name` accepts.
 */
export function transcriptFileName(startedAt: Date, mode: Mode, format: ExportFormat): string {
  const d = startedAt;
  const date = `${d.getFullYear()}-${pad(d.getMonth() + 1, 2)}-${pad(d.getDate(), 2)}`;
  const time = `${pad(d.getHours(), 2)}${pad(d.getMinutes(), 2)}${pad(d.getSeconds(), 2)}`;
  return `${date}_${time}_${mode}.${format}`;
}
