import { describe, expect, it } from "vitest";

import { freshParts, HeightLatch } from "./fresh";

describe("freshParts", () => {
  it("marks only the text added since the previous frame", () => {
    expect(freshParts({ stable: "今日は", unstable: "寒い" }, "今日は寒")).toEqual({
      stable: { kept: "今日は", fresh: "" },
      unstable: { kept: "寒", fresh: "い" },
    });
  });

  it("treats a rewritten tail as fresh from the first difference", () => {
    // Previous frame "今日は暑い" → the rewrite starts at "寒", inside the stable part.
    expect(freshParts({ stable: "今日は寒", unstable: "いね" }, "今日は暑い")).toEqual({
      stable: { kept: "今日は", fresh: "寒" },
      unstable: { kept: "", fresh: "いね" },
    });
  });

  it("does not animate a line's first appearance or an unchanged frame", () => {
    const parts = { stable: "abc", unstable: "de" };
    const none = { stable: { kept: "abc", fresh: "" }, unstable: { kept: "de", fresh: "" } };
    expect(freshParts(parts, null)).toEqual(none);
    expect(freshParts(parts, "abcde")).toEqual(none);
    // A shorter text than before: nothing new appeared.
    expect(freshParts({ stable: "ab", unstable: "" }, "abcde")).toEqual({
      stable: { kept: "ab", fresh: "" },
      unstable: { kept: "", fresh: "" },
    });
  });

  it("never splits a surrogate pair between kept and fresh", () => {
    // "𠮷" is two UTF-16 units; the previous frame had a different astral character.
    const result = freshParts({ stable: "", unstable: "a𠮷" }, "a😀");
    expect(result.unstable).toEqual({ kept: "a", fresh: "𠮷" });
  });
});

describe("HeightLatch", () => {
  it("keeps a live line at the tallest height it reached", () => {
    const latch = new HeightLatch();
    expect(latch.minHeight(1, false)).toBeNull();
    latch.record(1, false, 60);
    latch.record(1, false, 40); // the tail got shorter: the block must not jump
    expect(latch.minHeight(1, false)).toBe(60);
    latch.record(1, false, 90);
    expect(latch.minHeight(1, false)).toBe(90);
  });

  it("lets go when the final arrives and ignores bad heights", () => {
    const latch = new HeightLatch();
    latch.record(1, false, 60);
    expect(latch.minHeight(1, true)).toBeNull();
    expect(latch.minHeight(1, false)).toBeNull(); // forgotten, not just hidden
    latch.record(2, true, 50);
    latch.record(2, false, Number.NaN);
    latch.record(2, false, 0);
    expect(latch.minHeight(2, false)).toBeNull();
  });

  it("forgets lines that left the screen", () => {
    const latch = new HeightLatch();
    latch.record(1, false, 10);
    latch.record(2, false, 20);
    latch.keepOnly([2]);
    expect(latch.minHeight(1, false)).toBeNull();
    expect(latch.minHeight(2, false)).toBe(20);
    latch.clear();
    expect(latch.minHeight(2, false)).toBeNull();
  });
});
