import { describe, expect, it } from "vitest";

import { SESSION_END_GRACE_MS, SessionEndSave, type Timers } from "./session-end";

/** Manual clock: `advance` runs the timers that fall due. */
function fakeTimers(): Timers & { advance(ms: number): void; pending(): number } {
  let now = 0;
  let next = 1;
  const timers = new Map<number, { at: number; run: () => void }>();
  return {
    set(run, ms) {
      const id = next++;
      timers.set(id, { at: now + ms, run });
      return id;
    },
    clear(id) {
      timers.delete(id);
    },
    advance(ms) {
      now += ms;
      for (const [id, t] of [...timers].sort((a, b) => a[1].at - b[1].at)) {
        if (t.at > now) continue;
        timers.delete(id);
        t.run();
      }
    },
    pending: () => timers.size,
  };
}

function setup() {
  const timers = fakeTimers();
  const saved: string[] = [];
  let log = ["final 1"];
  const end = new SessionEndSave(() => saved.push(log.join("|")), timers);
  return { timers, saved, end, addFinal: (s: string) => (log = [...log, s]) };
}

describe("SessionEndSave", () => {
  it("waits for the engine's idle so a final that arrives after Stop is saved", () => {
    const { timers, saved, end, addFinal } = setup();
    end.ended(false);
    expect(end.waiting).toBe(true);
    timers.advance(500);
    addFinal("final 2");
    expect(saved).toEqual([]);
    end.engineIdle();
    expect(saved).toEqual(["final 1|final 2"]);
    expect(end.waiting).toBe(false);
    expect(timers.pending()).toBe(0);
    // The timer was cancelled: no second save.
    timers.advance(SESSION_END_GRACE_MS);
    expect(saved).toHaveLength(1);
  });

  it("saves after the grace period when the engine never reports idle", () => {
    const { timers, saved, end, addFinal } = setup();
    end.ended(false);
    timers.advance(SESSION_END_GRACE_MS - 1);
    expect(saved).toEqual([]);
    addFinal("late");
    timers.advance(1);
    expect(saved).toEqual(["final 1|late"]);
    expect(end.waiting).toBe(false);
    end.engineIdle();
    expect(saved).toHaveLength(1);
  });

  it("saves at once when the end is already final", () => {
    const { timers, saved, end } = setup();
    end.ended(true);
    expect(saved).toEqual(["final 1"]);
    expect(timers.pending()).toBe(0);
  });

  it("does not start a second wait or save twice for one end", () => {
    const { timers, saved, end } = setup();
    end.ended(false);
    end.ended(false);
    expect(timers.pending()).toBe(1);
    timers.advance(SESSION_END_GRACE_MS);
    expect(saved).toHaveLength(1);
  });

  it("an engine idle with no wait pending does nothing", () => {
    const { saved, end } = setup();
    end.engineIdle();
    end.flush();
    expect(saved).toEqual([]);
  });

  it("saves the ended session before a new one starts", () => {
    const { timers, saved, end } = setup();
    end.ended(false);
    end.flush();
    expect(saved).toEqual(["final 1"]);
    timers.advance(SESSION_END_GRACE_MS);
    expect(saved).toHaveLength(1);
  });
});
