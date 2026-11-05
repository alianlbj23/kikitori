/**
 * Guards the "every user-visible string is in a dictionary" rule: no Chinese,
 * Japanese or Korean text may appear in code or markup outside `src/locales/`.
 * Comments are allowed (the project writes them in Traditional Chinese); only
 * string, template and regular-expression literals are checked, using the
 * TypeScript parser so comments and code are told apart reliably.
 */

import ts from "typescript";
import { describe, expect, it } from "vitest";

import indexHtml from "../../index.html?raw";
import overlayHtml from "../../overlay.html?raw";

const CJK = /[\p{Script=Han}\p{Script=Hiragana}\p{Script=Katakana}\p{Script=Hangul}]/u;
/** The brand mark is Japanese by design, in every interface language. */
const ALLOWED = ["聞き取り"];

function withoutAllowed(text: string): string {
  return ALLOWED.reduce((s, a) => s.replaceAll(a, ""), text);
}

/** CJK-containing literals in a TypeScript source, as "line: text". */
export function cjkLiterals(fileName: string, source: string): string[] {
  const sf = ts.createSourceFile(fileName, source, ts.ScriptTarget.Latest, true);
  const found: string[] = [];
  const visit = (node: ts.Node): void => {
    if (
      ts.isStringLiteral(node) ||
      ts.isNoSubstitutionTemplateLiteral(node) ||
      ts.isTemplateHead(node) ||
      ts.isTemplateMiddle(node) ||
      ts.isTemplateTail(node) ||
      ts.isRegularExpressionLiteral(node)
    ) {
      // Both the source text and the cooked value: "日" must not slip through.
      const raw = node.getText(sf);
      if (CJK.test(withoutAllowed(raw)) || CJK.test(withoutAllowed(node.text))) {
        const line = sf.getLineAndCharacterOfPosition(node.getStart(sf)).line + 1;
        found.push(`${fileName}:${line}: ${raw}`);
      }
    }
    ts.forEachChild(node, visit);
  };
  visit(sf);
  return found;
}

/** CJK text in markup outside comments and the allowed brand. */
export function cjkInHtml(html: string): string[] {
  return withoutAllowed(html.replace(/<!--[\s\S]*?-->/g, ""))
    .split("\n")
    .filter((line) => CJK.test(line));
}

const sources = import.meta.glob<string>(["../**/*.ts", "!../**/*.test.ts", "!../locales/**", "!../**/testdata/**"], {
  query: "?raw",
  import: "default",
  eager: true,
});

describe("no hard-coded CJK text outside src/locales", () => {
  it("scans the app's TypeScript sources", () => {
    const names = Object.keys(sources);
    expect(names).toEqual(expect.arrayContaining(["../main/main.ts", "../overlay/overlay.ts", "./format.ts"]));
    expect(names.some((n) => n.includes("/locales/") || n.endsWith(".test.ts"))).toBe(false);
  });

  it("finds none in string, template or regex literals", () => {
    const found = Object.entries(sources).flatMap(([name, source]) => cjkLiterals(name, source));
    expect(found).toEqual([]);
  });

  it.each([
    ["index.html", indexHtml],
    ["overlay.html", overlayHtml],
  ])("finds none in %s", (_name, html) => {
    expect(cjkInHtml(html)).toEqual([]);
  });

  it("would catch literals (but not comments) — the scanner itself works", () => {
    const code = [
      "// 註解可以寫中文",
      "/** ドキュメント */",
      'const a = "開始";',
      "const b = `x ${a} 한국어`;",
      'const c = "\\u958b";',
      "const d = /日本/;",
      'const brand = "聞き取り";',
    ].join("\n");
    expect(cjkLiterals("sample.ts", code).map((f) => f.split(": ")[0])).toEqual([
      "sample.ts:3",
      "sample.ts:4",
      "sample.ts:5",
      "sample.ts:6",
    ]);
    expect(cjkInHtml('<p lang="ja">聞き取り</p>\n<!-- 註解 -->\n<p>開始</p>')).toEqual(["<p>開始</p>"]);
  });
});
