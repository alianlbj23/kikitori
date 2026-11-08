import { describe, expect, it } from "vitest";

import { CUSTOM_TRANSLATION_MODEL, translationPicker } from "./translation-picker";
import type { TranslationModel } from "./types";

function model(over: Partial<TranslationModel> & { name: string }): TranslationModel {
  return {
    label: over.name,
    installed: true,
    known: true,
    recommended_for: [],
    license: "Apache-2.0",
    license_url: null,
    commercial_use: true,
    excluded_regions: [],
    approx_vram_gb: null,
    ...over,
  };
}

const QWEN = model({ name: "qwen3.5:9b", label: "Qwen3.5 9B" });
const SAKURA = model({
  name: "hf.co/SakuraLLM/Sakura-GalTransl-7B-v3:IQ4_XS",
  label: "Sakura-GalTransl 7B v3",
  installed: false,
  commercial_use: false,
});

describe("translationPicker", () => {
  it("offers every model plus a custom entry, whatever the current value is", () => {
    const p = translationPicker([QWEN, SAKURA], "qwen3.5:9b", false);
    expect(p.options.map((o) => o.value)).toEqual([QWEN.name, SAKURA.name, CUSTOM_TRANSLATION_MODEL]);
    expect(p.selected).toBe("qwen3.5:9b");
    expect(p.showTextField).toBe(false);
  });

  it("labels each option with its name, install state and a non-commercial warning", () => {
    const [qwen, sakura] = translationPicker([QWEN, SAKURA], "qwen3.5:9b", false).options;
    expect(qwen.label).toContain("Qwen3.5 9B");
    expect(qwen.label).toContain("qwen3.5:9b");
    expect(sakura.label).toContain("Sakura-GalTransl 7B v3");
    expect(sakura.label).toMatch(/未安裝|Not installed/);
    expect(sakura.label).toMatch(/不可商用|Non-commercial/);
  });

  it("matches the saved value after trimming", () => {
    expect(translationPicker([QWEN], "  qwen3.5:9b ", false).selected).toBe("qwen3.5:9b");
  });

  it("selects the custom entry and shows the text field for a name not in the list", () => {
    const p = translationPicker([QWEN], "my-own-model:3b", false);
    expect(p.selected).toBe(CUSTOM_TRANSLATION_MODEL);
    expect(p.showTextField).toBe(true);
  });

  it("keeps the text field open once the user picked the custom entry, even for a listed name", () => {
    const p = translationPicker([QWEN], "qwen3.5:9b", true);
    expect(p.selected).toBe(CUSTOM_TRANSLATION_MODEL);
    expect(p.showTextField).toBe(true);
  });

  it("falls back to typing when the server listed no models", () => {
    const p = translationPicker([], "qwen3.5:9b", false);
    expect(p.options.map((o) => o.value)).toEqual([CUSTOM_TRANSLATION_MODEL]);
    expect(p.showTextField).toBe(true);
  });

  it("selects nothing for an empty value", () => {
    const p = translationPicker([QWEN], "", false);
    expect(p.selected).toBe("");
    expect(p.showTextField).toBe(false);
  });

  it("keeps the custom entry apart from a model named like it", () => {
    const p = translationPicker([model({ name: "custom" })], "custom", false);
    expect(p.options.filter((o) => o.value === CUSTOM_TRANSLATION_MODEL)).toHaveLength(1);
    expect(p.selected).toBe("custom");
    expect(p.showTextField).toBe(false);
  });

  it("says when the install state is unknown and when the license excludes regions", () => {
    const [unknown] = translationPicker([model({ name: "a:1b", installed: null })], "", false).options;
    expect(unknown.label).toMatch(/安裝狀態未知|Install state unknown/);
    const [restricted] = translationPicker([model({ name: "b:1b", excluded_regions: ["EU"] })], "", false).options;
    expect(restricted.label).toMatch(/歐盟|EU/);
  });

  it("shows the name once when the label is the name", () => {
    const [only] = translationPicker([model({ name: "c:1b" })], "", false).options;
    expect(only.label.startsWith("c:1b · ")).toBe(true);
  });
});
