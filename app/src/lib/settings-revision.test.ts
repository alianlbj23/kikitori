import { describe, expect, it } from "vitest";

import { SettingsRevision } from "./settings-revision";

describe("SettingsRevision", () => {
  it("accepts the first result and every result that is not older", () => {
    const r = new SettingsRevision();
    expect(r.acceptIfNotOlder(3)).toBe(true);
    expect(r.acceptIfNotOlder(3)).toBe(true);
    expect(r.acceptIfNotOlder(5)).toBe(true);
  });

  it("rejects a result older than one already shown (a late answer)", () => {
    // session.set_translation answered with revision 4, but its caller still waits for the
    // overlay while session.set_furigana's revision 5 is already on screen.
    const r = new SettingsRevision();
    expect(r.acceptIfNotOlder(5)).toBe(true);
    expect(r.acceptIfNotOlder(4)).toBe(false);
    expect(r.acceptIfNotOlder(6)).toBe(true);
  });

  it("accepts results without a revision (an older engine): the last one wins as before", () => {
    const r = new SettingsRevision();
    expect(r.acceptIfNotOlder(5)).toBe(true);
    expect(r.acceptIfNotOlder(null)).toBe(true);
    expect(r.acceptIfNotOlder(2)).toBe(false);
  });

  it("starts over after reset (the engine restarted and counts from the beginning)", () => {
    const r = new SettingsRevision();
    expect(r.acceptIfNotOlder(40)).toBe(true);
    r.reset();
    expect(r.acceptIfNotOlder(1)).toBe(true);
    expect(r.acceptIfNotOlder(0)).toBe(false);
  });
});
