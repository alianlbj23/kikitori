import { describe, expect, it } from "vitest";

import mainCss from "../styles/main.css?raw";

/**
 * Guards for the main window's renderer memory (v0.2.0 GUI soak: +25–31 MiB/min while a
 * session runs; experiments/memory_soak/README.md「CDP soak」). The memory was held by
 * layout and text shaping of every transcript row, not by JavaScript objects, so the
 * guards are on the CSS that keeps that work proportional to what is on screen.
 */
const css = mainCss.split("\r\n").join("\n");

/** The declarations of the top-level rule whose selector is exactly `selector`. */
function rule(selector: string): string {
  const head = `\n${selector} {`;
  const at = css.indexOf(head);
  if (at < 0) throw new Error(`no rule for ${selector} in main.css`);
  return css.slice(at + head.length, css.indexOf("}", at));
}

describe("main window rendering cost", () => {
  it("skips layout and paint of transcript rows that are off screen", () => {
    const utt = rule(".utt");
    expect(utt).toMatch(/content-visibility:\s*auto/);
    // Remembered size once rendered, so the scroll height stays stable.
    expect(utt).toMatch(/contain-intrinsic-size:\s*auto\s+\d+px/);
    // ...except the newest rows, which auto-scroll needs at their real height.
    expect(rule(".utt:nth-last-child(-n + 3)")).toMatch(/content-visibility:\s*visible/);
  });

  it("animates the level meter with a transform, never its width (no layout per frame)", () => {
    const fill = rule(".meter-fill");
    expect(fill).toMatch(/transition:\s*transform\b/);
    expect(fill).not.toMatch(/transition:[^;]*\bwidth\b/);
  });
});
