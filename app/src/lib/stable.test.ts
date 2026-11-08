import { describe, expect, it } from "vitest";

import { splitStable } from "./stable";

describe("splitStable", () => {
  it("splits a partial at the stable prefix", () => {
    expect(splitStable("今日はちょっと寒い", 3, false)).toEqual({ stable: "今日は", unstable: "ちょっと寒い" });
    expect(splitStable("hello world", 5, false)).toEqual({ stable: "hello", unstable: " world" });
  });

  it("shows finals as stable whatever the count says", () => {
    expect(splitStable("done", null, true)).toEqual({ stable: "done", unstable: "" });
    expect(splitStable("done", 1, true)).toEqual({ stable: "done", unstable: "" });
  });

  it("treats a missing or non-positive count as nothing settled (the old look)", () => {
    expect(splitStable("abc", null, false)).toEqual({ stable: "", unstable: "abc" });
    expect(splitStable("abc", 0, false)).toEqual({ stable: "", unstable: "abc" });
    expect(splitStable("abc", -4, false)).toEqual({ stable: "", unstable: "abc" });
    expect(splitStable("abc", Number.NaN, false)).toEqual({ stable: "", unstable: "abc" });
  });

  it("clamps a count beyond the text to all of it", () => {
    expect(splitStable("abc", 3, false)).toEqual({ stable: "abc", unstable: "" });
    expect(splitStable("abc", 99, false)).toEqual({ stable: "abc", unstable: "" });
    expect(splitStable("", 5, false)).toEqual({ stable: "", unstable: "" });
  });

  it("rounds fractional counts down", () => {
    expect(splitStable("abcd", 2.9, false)).toEqual({ stable: "ab", unstable: "cd" });
  });

  it("counts code points like the engine, not UTF-16 units", () => {
    // 𠮷 (U+20BB7) is one code point but two UTF-16 units.
    expect(splitStable("𠮷野家です", 3, false)).toEqual({ stable: "𠮷野家", unstable: "です" });
    expect(splitStable("😀😀x", 1, false)).toEqual({ stable: "😀", unstable: "😀x" });
  });

  it("never splits a grapheme cluster across the two parts", () => {
    // "é" written as e + combining acute: two code points, one cluster.
    const decomposed = "café noir";
    expect(splitStable(decomposed, 4, false)).toEqual({ stable: "caf", unstable: "é noir" });
    expect(splitStable(decomposed, 5, false)).toEqual({ stable: "café", unstable: " noir" });
    // A family emoji is several code points joined by ZWJ.
    const family = "👨‍👩‍👧 ok";
    expect(splitStable(family, 2, false)).toEqual({ stable: "", unstable: family });
    expect(splitStable(family, 5, false).stable).toBe("👨‍👩‍👧");
  });

  it("keeps logical order for right-to-left text", () => {
    // Arabic "مرحبا بالعالم": the prefix is the first letters in reading order.
    const parts = splitStable("مرحبا بالعالم", 5, false);
    expect(parts).toEqual({ stable: "مرحبا", unstable: " بالعالم" });
    expect(parts.stable + parts.unstable).toBe("مرحبا بالعالم");
  });
});
