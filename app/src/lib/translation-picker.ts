/**
 * Choices for the two translation-model fields of the combination form.
 *
 * The fields used to be text inputs with a datalist, which WebView2 filters by the
 * current text: with `qwen3.5:9b` filled in, the other models never showed up. The
 * form now shows a real dropdown of `models.list.translation.models`, and a free-text
 * field only for names the engine does not list (any OpenAI-compatible server may
 * serve models kikitori has never heard of). main.ts keeps the text field as the
 * form value, so validation and saving are unchanged.
 */

import { regionNames } from "./format";
import { t } from "./i18n";
import type { TranslationModel } from "./types";

/** Value of the "other (type a name)" entry; model names never contain a NUL. */
export const CUSTOM_TRANSLATION_MODEL = "\0custom";

export interface TranslationPicker {
  options: { value: string; label: string }[];
  /** A model name, `CUSTOM_TRANSLATION_MODEL`, or "" before the settings are loaded. */
  selected: string;
  /** True when the free-text field should be shown ("other" is picked). */
  showTextField: boolean;
}

function optionLabel(m: TranslationModel): string {
  const name = m.label && m.label !== m.name ? `${m.label} (${m.name})` : m.name;
  const notes = [
    m.installed === null ? t("tr.stateUnknown") : m.installed ? t("tr.stateInstalled") : t("tr.stateMissing"),
  ];
  if (m.commercial_use === false) notes.push(t("tr.nonCommercial"));
  if (m.excluded_regions.length) notes.push(t("tr.regionExcluded", { regions: regionNames(m.excluded_regions) }));
  return `${name} · ${notes.join(" · ")}`;
}

/**
 * Builds the dropdown for one field.
 *
 * @param value the field's current text (saved or edited).
 * @param customOpen true after the user picked the custom entry; the text field stays
 *   open while they type, even when the text happens to match a listed model.
 */
export function translationPicker(
  models: readonly TranslationModel[],
  value: string,
  customOpen: boolean,
): TranslationPicker {
  const options = [
    ...models.map((m) => ({ value: m.name, label: optionLabel(m) })),
    { value: CUSTOM_TRANSLATION_MODEL, label: t("combo.customModel") },
  ];
  const name = value.trim();
  const listed = models.some((m) => m.name === name);
  if (customOpen || (name !== "" && !listed)) {
    return { options, selected: CUSTOM_TRANSLATION_MODEL, showTextField: true };
  }
  return { options, selected: listed ? name : "", showTextField: false };
}
