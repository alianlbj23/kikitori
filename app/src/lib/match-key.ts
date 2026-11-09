/**
 * The engine's matching key for glossary sources and correction rules
 * (`glossary.match_key`): NFKC, case folded, trimmed.
 *
 * Role: shared by `glossary.ts` and `corrections.ts` (its own module so the two
 * do not import each other). `toUpperCase().toLowerCase()` folds like Python's
 * `casefold()` for the cases that matter here (ß → ss), and the final sigma is
 * folded to σ as casefold does.
 */
export function matchKey(text: string): string {
  return text.normalize("NFKC").toUpperCase().toLowerCase().replaceAll("ς", "σ").trim();
}
