import { describe, expect, it } from "vitest";

import { setAttributeIfChanged, setIfChanged } from "./dom";

/** Counts writes, like a DOM property whose setter always does work (textContent replaces the text node). */
function counting<T>(initial: T): { value: T; writes: number } {
  let value = initial;
  const target = {
    writes: 0,
    get value(): T {
      return value;
    },
    set value(next: T) {
      target.writes += 1;
      value = next;
    },
  };
  return target;
}

describe("setIfChanged", () => {
  it("does not write an unchanged value", () => {
    // The transcript re-renders every line on every subtitle event (several per second);
    // assigning the same textContent still creates a new text node each time, and with a few
    // hundred lines that garbage grew the WebView2 renderer by ~8 MiB/min (v0.2.0 GUI soak).
    const node = counting("same");
    setIfChanged(node, "value", "same");
    expect(node.writes).toBe(0);
  });

  it("writes a changed value", () => {
    const node = counting("old");
    setIfChanged(node, "value", "new");
    expect(node.writes).toBe(1);
    expect(node.value).toBe("new");
  });

  it("treats null and the empty string as different", () => {
    const node = counting<string | null>(null);
    setIfChanged(node, "value", "");
    expect(node.writes).toBe(1);
  });
});

describe("setAttributeIfChanged", () => {
  /** The two Element methods it uses, counting writes. */
  function element(initial: Record<string, string>): {
    getAttribute(name: string): string | null;
    setAttribute(name: string, value: string): void;
    writes: number;
  } {
    const attrs = new Map(Object.entries(initial));
    return {
      writes: 0,
      getAttribute: (name) => attrs.get(name) ?? null,
      setAttribute(name, value) {
        this.writes += 1;
        attrs.set(name, value);
      },
    };
  }

  it("skips an attribute that already has the value (the meter is updated every second)", () => {
    const meter = element({ "aria-valuenow": "-20" });
    setAttributeIfChanged(meter, "aria-valuenow", "-20");
    expect(meter.writes).toBe(0);
  });

  it("writes a changed or missing attribute", () => {
    const meter = element({ "aria-valuenow": "-20" });
    setAttributeIfChanged(meter, "aria-valuenow", "-21");
    setAttributeIfChanged(meter, "aria-valuetext", "");
    expect(meter.writes).toBe(2);
    expect(meter.getAttribute("aria-valuenow")).toBe("-21");
    expect(meter.getAttribute("aria-valuetext")).toBe("");
  });
});
