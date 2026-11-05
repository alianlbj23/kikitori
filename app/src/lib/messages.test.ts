import { describe, expect, it } from "vitest";

import { formatEngineMessage, parseEngineMessage, parseEngineMessages, type Translate } from "./messages";

/** A dictionary with one engine code, standing in for the real one (codes arrive in phase B). */
const fakeTranslate: Translate = (key, params) =>
  key === "engine.gpu_runtime_missing" ? `GPU ${String(params.name)} needs the runtime` : null;

describe("formatEngineMessage", () => {
  it("shows plain strings from older engines as-is", () => {
    expect(formatEngineMessage("顯示卡缺少 cuBLAS", fakeTranslate)).toBe("顯示卡缺少 cuBLAS");
  });

  it("translates a known code with its params", () => {
    expect(
      formatEngineMessage(
        { code: "gpu_runtime_missing", params: { name: "RTX 5070" }, message: "fallback" },
        fakeTranslate,
      ),
    ).toBe("GPU RTX 5070 needs the runtime");
  });

  it("falls back to the engine's English message for an unknown code", () => {
    expect(formatEngineMessage({ code: "brand_new", params: {}, message: "Something new." }, fakeTranslate)).toBe(
      "Something new.",
    );
  });

  it("uses the real dictionary by default (no engine.* codes yet → message)", () => {
    expect(formatEngineMessage({ code: "gpu_runtime_missing", params: {}, message: "English text" })).toBe(
      "English text",
    );
  });
});

describe("parseEngineMessage", () => {
  it("accepts strings and coded objects, keeping only scalar params", () => {
    expect(parseEngineMessage("plain")).toBe("plain");
    expect(
      parseEngineMessage({ code: "low_ram", params: { gb: 8, name: "x", nested: { a: 1 }, bad: Number.NaN }, message: "Low RAM" }),
    ).toEqual({ code: "low_ram", params: { gb: 8, name: "x" }, message: "Low RAM" });
    expect(parseEngineMessage({ code: "no_params", message: "ok" })).toEqual({ code: "no_params", params: {}, message: "ok" });
  });

  it("rejects malformed messages", () => {
    expect(parseEngineMessage(3)).toBeNull();
    expect(parseEngineMessage(null)).toBeNull();
    expect(parseEngineMessage({ code: "x" })).toBeNull();
    expect(parseEngineMessage({ code: "Bad Code!", message: "m" })).toBeNull();
    expect(parseEngineMessage({ message: "m" })).toBeNull();
    expect(parseEngineMessage("x".repeat(2001))).toBeNull();
  });

  it("drops bad list items instead of failing the whole payload", () => {
    expect(parseEngineMessages(["a", 1, { code: "c", message: "m" }, { nope: true }])).toEqual([
      "a",
      { code: "c", params: {}, message: "m" },
    ]);
    expect(parseEngineMessages("not a list")).toEqual([]);
  });
});
