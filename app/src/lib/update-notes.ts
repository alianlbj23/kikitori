/**
 * Turns the update notes from `latest.json` into blocks the update notice can show.
 *
 * Role: the notes are the release's Markdown (`docs/release-notes/v<version>.md`,
 * written by `scripts/release.py manifest --notes-from-version`). They arrive from
 * the network, so they are never parsed as HTML: the caller builds elements from
 * these blocks with `textContent` only. Only the line-level structure that makes
 * the notes readable is recognized — `#` headings, `-`／`*`／`+` list items and
 * blank-line paragraphs — and `**bold**` markers are dropped; everything else
 * (links, code, tables, raw HTML) stays as literal text.
 */

export type NotesBlock =
  | { readonly kind: "heading"; readonly text: string }
  | { readonly kind: "paragraph"; readonly text: string }
  | { readonly kind: "list"; readonly items: readonly string[] };

const HEADING = /^#{1,6}(?:\s+(.*))?$/;
const LIST_ITEM = /^\s*[-*+](?:\s+(.*))?$/;
const BOLD = /\*\*(.+?)\*\*/g;

function stripBold(text: string): string {
  return text.replace(BOLD, "$1").trim();
}

/**
 * Whether the notes say nothing beyond the version: blank, or the fallback
 * `kikitori v<version>` that release.py writes when a release has no notes file
 * (and that every `latest.json` carried before notes were published).
 */
function isPlaceholder(notes: string, version: string): boolean {
  const s = notes.trim();
  return s === "" || s === `kikitori v${version}`;
}

/**
 * Blocks to show for `notes` of the update to `version`; empty when there is
 * nothing worth a "show what's new" button.
 */
export function updateNotesBlocks(notes: string | null, version: string): NotesBlock[] {
  if (notes === null || isPlaceholder(notes, version)) return [];
  const blocks: NotesBlock[] = [];
  let paragraph: string[] = [];
  let items: string[] = [];
  const flush = (): void => {
    if (paragraph.length) blocks.push({ kind: "paragraph", text: paragraph.join("\n") });
    if (items.length) blocks.push({ kind: "list", items });
    paragraph = [];
    items = [];
  };
  for (const raw of notes.replace(/\r\n?/g, "\n").split("\n")) {
    const line = raw.trimEnd();
    if (line.trim() === "") {
      flush();
      continue;
    }
    const heading = HEADING.exec(line);
    if (heading) {
      flush();
      const text = stripBold(heading[1] ?? "");
      if (text) blocks.push({ kind: "heading", text });
      continue;
    }
    const item = LIST_ITEM.exec(line);
    if (item) {
      if (paragraph.length) flush();
      const text = stripBold(item[1] ?? "");
      if (text) items.push(text);
      continue;
    }
    if (items.length) flush();
    paragraph.push(stripBold(line));
  }
  flush();
  return blocks;
}
