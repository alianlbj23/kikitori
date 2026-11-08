import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AUTO_DISMISS_MS, autoDismiss } from "./auto-dismiss";

describe("autoDismiss", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it("dismisses once the delay has passed", () => {
    const dismiss = vi.fn();
    autoDismiss({ isBeingRead: () => false, dismiss });
    vi.advanceTimersByTime(AUTO_DISMISS_MS - 1);
    expect(dismiss).not.toHaveBeenCalled();
    vi.advanceTimersByTime(1);
    expect(dismiss).toHaveBeenCalledTimes(1);
  });

  it("waits while the notice is being read (hovered or focused)", () => {
    let reading = true;
    const dismiss = vi.fn();
    autoDismiss({ isBeingRead: () => reading, dismiss }, 1000);
    vi.advanceTimersByTime(3000);
    expect(dismiss).not.toHaveBeenCalled();
    reading = false;
    vi.advanceTimersByTime(1000);
    expect(dismiss).toHaveBeenCalledTimes(1);
  });

  it("waits a full delay after the reader lets go between two checks", () => {
    let reading = true;
    const dismiss = vi.fn();
    const timer = autoDismiss({ isBeingRead: () => reading, dismiss }, 1000);
    // The check at 1000 ms re-arms for 2000 ms; the pointer leaves at 1900 ms.
    vi.advanceTimersByTime(1900);
    reading = false;
    timer.restart();
    vi.advanceTimersByTime(999);
    expect(dismiss).not.toHaveBeenCalled();
    vi.advanceTimersByTime(1);
    expect(dismiss).toHaveBeenCalledTimes(1);
  });

  it("never dismisses after it is cancelled (the user closed it)", () => {
    const dismiss = vi.fn();
    const timer = autoDismiss({ isBeingRead: () => false, dismiss }, 1000);
    timer.cancel();
    vi.advanceTimersByTime(5000);
    expect(dismiss).not.toHaveBeenCalled();
  });

  it("cancels a re-armed wait too, and a restart after cancel does nothing", () => {
    let reading = true;
    const dismiss = vi.fn();
    const timer = autoDismiss({ isBeingRead: () => reading, dismiss }, 1000);
    vi.advanceTimersByTime(1500);
    timer.cancel();
    reading = false;
    timer.restart();
    vi.advanceTimersByTime(5000);
    expect(dismiss).not.toHaveBeenCalled();
  });

  it("rejects a delay that is not a positive finite number", () => {
    for (const ms of [0, -1, Number.NaN, Number.POSITIVE_INFINITY]) {
      expect(() => autoDismiss({ isBeingRead: () => false, dismiss: () => {} }, ms)).toThrow(RangeError);
    }
  });
});
