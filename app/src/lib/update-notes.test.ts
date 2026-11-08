import { describe, expect, it } from "vitest";

import { updateNotesBlocks } from "./update-notes";

describe("updateNotesBlocks", () => {
  it("shows nothing for missing, blank or default notes", () => {
    expect(updateNotesBlocks(null, "1.2.3")).toEqual([]);
    expect(updateNotesBlocks("", "1.2.3")).toEqual([]);
    expect(updateNotesBlocks(" \n\t\n", "1.2.3")).toEqual([]);
    // release.py 沒有說明檔時的預設值，與以前 release.yml 寫死的 latest.json。
    expect(updateNotesBlocks("kikitori v1.2.3", "1.2.3")).toEqual([]);
    expect(updateNotesBlocks("  kikitori v1.2.3\n", "1.2.3")).toEqual([]);
  });

  it("keeps a title that only looks like the default for another version", () => {
    expect(updateNotesBlocks("kikitori v1.2.4", "1.2.3")).toEqual([{ kind: "paragraph", text: "kikitori v1.2.4" }]);
  });

  it("splits headings, paragraphs and lists", () => {
    const notes = [
      "This release adds 20 languages.",
      "And more.",
      "",
      "## New",
      "",
      "### Languages",
      "- **20 languages**: pick any pair",
      "* auto detect",
      "",
      "+ plus item",
      "Trailing paragraph",
    ].join("\n");
    expect(updateNotesBlocks(notes, "1.2.3")).toEqual([
      { kind: "paragraph", text: "This release adds 20 languages.\nAnd more." },
      { kind: "heading", text: "New" },
      { kind: "heading", text: "Languages" },
      { kind: "list", items: ["20 languages: pick any pair", "auto detect"] },
      { kind: "list", items: ["plus item"] },
      { kind: "paragraph", text: "Trailing paragraph" },
    ]);
  });

  it("normalizes CRLF and indented list items", () => {
    expect(updateNotesBlocks("- a\r\n  - nested\r\n- b\r\n", "1.2.3")).toEqual([
      { kind: "list", items: ["a", "nested", "b"] },
    ]);
  });

  it("keeps markup that is not a heading or list as plain text", () => {
    const notes = '#hashtag\n-5 dB\n<img src=x onerror="alert(1)">\n`code` and **unclosed';
    expect(updateNotesBlocks(notes, "1.2.3")).toEqual([
      { kind: "paragraph", text: '#hashtag\n-5 dB\n<img src=x onerror="alert(1)">\n`code` and **unclosed' },
    ]);
  });

  it("drops empty headings and list items", () => {
    expect(updateNotesBlocks("#\n## \n- \ntext", "1.2.3")).toEqual([{ kind: "paragraph", text: "text" }]);
  });

  it("keeps the truncation marker from release.py as the last paragraph", () => {
    const notes = "- a\n- b\n\n…(see https://github.com/alianlbj23/kikitori/releases/tag/v1.2.3)";
    expect(updateNotesBlocks(notes, "1.2.3")).toEqual([
      { kind: "list", items: ["a", "b"] },
      { kind: "paragraph", text: "…(see https://github.com/alianlbj23/kikitori/releases/tag/v1.2.3)" },
    ]);
  });
});
