import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { firstReadingsOnly, rubyKey, rubyParts, segmentNodes, validRuby, type RubySpan } from "./ruby";

const TEXT = "今日は食べる";
const RUBY: RubySpan[] = [
  [0, 2, "きょう"],
  [3, 4, "た"],
];

describe("rubyParts", () => {
  it("splits a final into annotated words and plain runs", () => {
    expect(rubyParts(TEXT, RUBY, null, true)).toEqual({
      stable: [
        { text: "今日", reading: "きょう" },
        { text: "は", reading: null },
        { text: "食", reading: "た" },
        { text: "べる", reading: null },
      ],
      unstable: [],
    });
  });

  it("returns null without furigana, so the plain-text path is used", () => {
    expect(rubyParts(TEXT, null, null, true)).toBeNull();
    expect(rubyParts(TEXT, [], null, true)).toBeNull();
  });

  it("keeps the stable prefix: a word ending inside it is stable", () => {
    // stable_chars 3 = "今日は"
    expect(rubyParts(TEXT, RUBY, 3, false)).toEqual({
      stable: [
        { text: "今日", reading: "きょう" },
        { text: "は", reading: null },
      ],
      unstable: [
        { text: "食", reading: "た" },
        { text: "べる", reading: null },
      ],
    });
  });

  it("never fades half a word: a word crossing the cut goes to the tail whole", () => {
    // stable_chars 1 cuts "今日" in the middle.
    expect(rubyParts(TEXT, RUBY, 1, false)).toEqual({
      stable: [],
      unstable: [
        { text: "今日", reading: "きょう" },
        { text: "は", reading: null },
        { text: "食", reading: "た" },
        { text: "べる", reading: null },
      ],
    });
    // Plain text before the cut stays stable even when the next word crosses it.
    expect(rubyParts("あの今日", [[2, 4, "きょう"]], 3, false)).toEqual({
      stable: [{ text: "あの", reading: null }],
      unstable: [{ text: "今日", reading: "きょう" }],
    });
  });

  it("joins the parts back into the original text", () => {
    for (const stableChars of [0, 1, 2, 3, 4, 5, 6, 99]) {
      const parts = rubyParts(TEXT, RUBY, stableChars, false)!;
      const all = [...parts.stable, ...parts.unstable].map((s) => s.text).join("");
      expect(all).toBe(TEXT);
    }
  });

  it("matches splitStable for plain text around the words", () => {
    // stable_chars 5 = "今日は食べ": the plain tail is cut where splitStable cuts.
    expect(rubyParts(TEXT, RUBY, 5, false)).toEqual({
      stable: [
        { text: "今日", reading: "きょう" },
        { text: "は", reading: null },
        { text: "食", reading: "た" },
        { text: "べ", reading: null },
      ],
      unstable: [{ text: "る", reading: null }],
    });
  });

  it("counts code points like the engine, not UTF-16 units", () => {
    // 𠮷 (U+20BB7) is one code point but two UTF-16 units.
    expect(rubyParts("𠮷野家です", [[0, 3, "よしのや"]], null, true)!.stable).toEqual([
      { text: "𠮷野家", reading: "よしのや" },
      { text: "です", reading: null },
    ]);
  });

  it("ignores invalid annotations instead of drawing them", () => {
    for (const bad of [
      [[0, 9, "きょう"]],
      [[2, 2, "き"]],
      [
        [3, 4, "た"],
        [0, 2, "きょう"],
      ],
      [[0, 2, "キョウ"]],
      [[0, 2, ""]],
      [[0, 2, "<b>x</b>"]],
      [[0.5, 2, "きょう"]],
    ] as RubySpan[][]) {
      expect(rubyParts(TEXT, bad, null, true)).toBeNull();
    }
  });
});

describe("validRuby", () => {
  it("accepts the long vowel mark and rejects over-long readings", () => {
    expect(validRuby([[0, 1, "らーめん"]], 1)).toBe(true);
    expect(validRuby([[0, 1, "あ".repeat(65)]], 1)).toBe(false);
  });
});

describe("rubyKey", () => {
  it("changes when the reading or the cut changes", () => {
    const a = rubyKey(rubyParts(TEXT, RUBY, 3, false)!);
    expect(rubyKey(rubyParts(TEXT, RUBY, 3, false)!)).toBe(a);
    expect(rubyKey(rubyParts(TEXT, RUBY, 4, false)!)).not.toBe(a);
    expect(rubyKey(rubyParts(TEXT, [[0, 2, "こんにち"]], 3, false)!)).not.toBe(a);
  });
});

/** Just enough DOM for `h()` and `segmentNodes()` (the tests run in Node, without a browser). */
class FakeText {
  constructor(readonly data: string) {}
  get outerHTML(): string {
    return this.data;
  }
}

class FakeElement {
  readonly childNodes: (FakeElement | FakeText)[] = [];
  readonly attributes = new Map<string, string>();
  className = "";
  constructor(readonly tagName: string) {}
  setAttribute(name: string, value: string): void {
    this.attributes.set(name, value);
  }
  appendChild(child: FakeElement | FakeText): void {
    this.childNodes.push(child);
  }
  get outerHTML(): string {
    return `<${this.tagName}>${this.childNodes.map((c) => c.outerHTML).join("")}</${this.tagName}>`;
  }
}

describe("segmentNodes", () => {
  beforeEach(() => {
    vi.stubGlobal("document", {
      createElement: (tag: string) => new FakeElement(tag),
      createTextNode: (text: string) => new FakeText(text),
    });
  });
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("builds <ruby>base<rt>reading</rt></ruby> and text nodes", () => {
    const parts = rubyParts(TEXT, RUBY, null, true)!;
    const html = segmentNodes(parts.stable)
      .map((n) => (n as unknown as FakeElement).outerHTML)
      .join("");
    expect(html).toBe("<ruby>今日<rt>きょう</rt></ruby>は<ruby>食<rt>た</rt></ruby>べる");
  });

  it("puts engine text into text nodes, never into markup", () => {
    const nodes = segmentNodes([{ text: "<img src=x>", reading: null }]);
    expect(nodes[0]).toBeInstanceOf(FakeText);
    expect((nodes[0] as unknown as FakeText).data).toBe("<img src=x>");
    const ruby = segmentNodes([{ text: "<b>", reading: "<i>" }])[0] as unknown as FakeElement;
    expect(ruby.tagName).toBe("ruby");
    expect(ruby.childNodes[0]).toBeInstanceOf(FakeText);
    expect((ruby.childNodes[1] as FakeElement).childNodes[0]).toBeInstanceOf(FakeText);
  });
});

describe("firstReadingsOnly (G7)", () => {
  const word = (text: string, reading: string | null) => ({ text, reading });

  it("keeps a reading only in the utterance that first showed the word", () => {
    const owners = new Map<string, number>();
    const first = { stable: [word("今日", "きょう"), word("は", null)], unstable: [] };
    expect(firstReadingsOnly(first, 1, owners)).toEqual(first);
    // The same utterance redrawn (a partial growing, then its final) keeps it.
    expect(firstReadingsOnly(first, 1, owners)).toEqual(first);
    // A later utterance shows the word plain; a new word still gets its reading.
    const later = { stable: [word("今日", "きょう")], unstable: [word("天気", "てんき")] };
    expect(firstReadingsOnly(later, 2, owners)).toEqual({
      stable: [word("今日", null)],
      unstable: [word("天気", "てんき")],
    });
  });

  it("treats a different reading of the same kanji as a different word", () => {
    const owners = new Map<string, number>();
    firstReadingsOnly({ stable: [word("今日", "きょう")], unstable: [] }, 1, owners);
    const other = { stable: [word("今日", "こんにち")], unstable: [] };
    expect(firstReadingsOnly(other, 2, owners)).toEqual(other);
  });
});
