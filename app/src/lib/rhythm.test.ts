import { describe, expect, it } from "vitest";

import { emptyRhythm, stepRhythm, type RhythmOptions, type RhythmState } from "./rhythm";
import type { SubtitleLine } from "./subtitles";

function line(id: number, original: string, translation = "", isFinal = true): SubtitleLine {
  return {
    utteranceId: id,
    original,
    translation,
    sourceLanguage: "ja",
    targetLanguage: "zh-TW",
    isFinal,
    startSample: id * 16_000,
    endSample: id * 16_000 + 8000,
  };
}

const OPTS: RhythmOptions = { maxLines: 2, minDisplayMs: 1500, idleClearMs: 8000 };

/** Runs the steps in order and returns the ids on screen after each. */
function run(steps: [number, SubtitleLine[]][], opts = OPTS): { ids: number[][]; wake: (number | null)[]; state: RhythmState } {
  let state = emptyRhythm();
  const ids: number[][] = [];
  const wake: (number | null)[] = [];
  for (const [now, lines] of steps) {
    const r = stepRhythm(state, lines, now, opts);
    state = r.state;
    ids.push(r.lines.map((l) => l.utteranceId));
    wake.push(r.wakeAt);
  }
  return { ids, wake, state };
}

describe("minimum display time", () => {
  it("lets a new line wait until the oldest has been shown long enough", () => {
    const a = line(1, "a");
    const b = line(2, "b");
    const c = line(3, "c");
    const { ids, wake } = run([
      [0, [a]],
      [100, [a, b]],
      [500, [a, b, c]], // a has been up 500 ms: c waits until 1500
      [1499, [a, b, c]],
      [1500, [a, b, c]],
    ]);
    expect(ids).toEqual([[1], [1, 2], [1, 2], [1, 2], [2, 3]]);
    expect(wake[2]).toBe(1500);
  });

  it("does not wait when the setting is 0", () => {
    const { ids } = run(
      [
        [0, [line(1, "a")]],
        [10, [line(1, "a"), line(2, "b")]],
        [20, [line(1, "a"), line(2, "b"), line(3, "c")]],
      ],
      { ...OPTS, minDisplayMs: 0 },
    );
    expect(ids[2]).toEqual([2, 3]);
  });

  it("never builds up a backlog: more than a screenful waiting shows the newest at once", () => {
    const lines = [1, 2, 3, 4, 5].map((i) => line(i, `l${i}`));
    const { ids } = run([
      [0, lines.slice(0, 2)],
      [100, lines], // three waiting > two on screen
    ]);
    expect(ids[1]).toEqual([4, 5]);
  });

  it("restarts a line's clock when its complete translation arrives", () => {
    const partial = line(1, "a", "", false);
    const final = line(1, "a", "A", true);
    const b = line(2, "b");
    const c = line(3, "c");
    const { ids, wake } = run([
      [0, [partial]],
      [1000, [final]],
      [1100, [final, b]],
      [2000, [final, b, c]], // final shown since 1000: c waits until 2500
      [2500, [final, b, c]],
    ]);
    expect(ids).toEqual([[1], [1], [1, 2], [1, 2], [2, 3]]);
    expect(wake[3]).toBe(2500);
  });

  it("shows in-place updates and removals immediately", () => {
    const { ids } = run([
      [0, [line(1, "a", "", false), line(2, "b", "", false)]],
      [10, [line(1, "ab", "", false), line(2, "b", "", false)]],
      [20, [line(2, "b", "", false)]], // the engine removed 1 (not speech)
    ]);
    expect(ids).toEqual([[1, 2], [1, 2], [2]]);
  });
});

describe("idle fade", () => {
  it("hides the lines after the idle time and asks to be woken for it", () => {
    const a = line(1, "a");
    const r1 = stepRhythm(emptyRhythm(), [a], 0, OPTS);
    expect(r1.wakeAt).toBe(8000);
    const r2 = stepRhythm(r1.state, [a], 7999, OPTS);
    expect(r2.lines).toHaveLength(1);
    expect(r2.fadedNow).toBe(false);
    const r3 = stepRhythm(r2.state, [a], 8000, OPTS);
    expect(r3.lines).toEqual([]);
    expect(r3.fadedNow).toBe(true);
    expect(r3.wakeAt).toBeNull();
    // Nothing new: stays hidden, and no second fade.
    const r4 = stepRhythm(r3.state, [a], 9000, OPTS);
    expect(r4.lines).toEqual([]);
    expect(r4.fadedNow).toBe(false);
  });

  it("brings a faded line back when its text changes, and shows new lines", () => {
    const partial = line(1, "a", "", false);
    let r = stepRhythm(emptyRhythm(), [partial], 0, OPTS);
    r = stepRhythm(r.state, [partial], 8000, OPTS);
    expect(r.lines).toEqual([]);
    r = stepRhythm(r.state, [line(1, "a", "A", true)], 9000, OPTS);
    expect(r.lines.map((l) => l.translation)).toEqual(["A"]);
    r = stepRhythm(r.state, [line(1, "a", "A", true), line(2, "b")], 9100, OPTS);
    expect(r.lines.map((l) => l.utteranceId)).toEqual([1, 2]);
  });

  it("never fades when the setting is 0", () => {
    const opts = { ...OPTS, idleClearMs: 0 };
    let r = stepRhythm(emptyRhythm(), [line(1, "a")], 0, opts);
    expect(r.wakeAt).toBeNull();
    r = stepRhythm(r.state, [line(1, "a")], 3_600_000, opts);
    expect(r.lines).toHaveLength(1);
  });

  it("counts idle time from the last change on screen", () => {
    const { ids } = run([
      [0, [line(1, "a", "", false)]],
      [5000, [line(1, "ab", "", false)]],
      [12_000, [line(1, "ab", "", false)]], // 7 s since the change: still shown
      [13_000, [line(1, "ab", "", false)]],
    ]);
    expect(ids).toEqual([[1], [1], [1], []]);
  });
});

describe("skipping", () => {
  it("does not show lines older than ones already shown", () => {
    const { ids } = run([
      [0, [line(5, "e")]],
      [10, [line(3, "c"), line(5, "e")]],
    ]);
    expect(ids).toEqual([[5], [5]]);
  });
});
