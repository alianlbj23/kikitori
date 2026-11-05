/**
 * Minimal DOM helpers.
 *
 * Role: builds elements without `innerHTML`, so text that came from the engine
 * (transcripts, model names, error messages) can never be interpreted as markup.
 */

type Child = Node | string | null | undefined | false;
type Attrs = Record<string, string | boolean | number | null | undefined>;

export function h<K extends keyof HTMLElementTagNameMap>(
  tag: K,
  attrs: Attrs = {},
  ...children: Child[]
): HTMLElementTagNameMap[K] {
  const el = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (value === null || value === undefined || value === false) continue;
    if (key === "class") el.className = String(value);
    else el.setAttribute(key, value === true ? "" : String(value));
  }
  append(el, ...children);
  return el;
}

export function append(parent: Node, ...children: Child[]): void {
  for (const child of children) {
    if (child === null || child === undefined || child === false) continue;
    parent.appendChild(typeof child === "string" ? document.createTextNode(child) : child);
  }
}

/** Replaces all children of `parent`. */
export function replace(parent: Node, ...children: Child[]): void {
  while (parent.firstChild) parent.removeChild(parent.firstChild);
  append(parent, ...children);
}

/**
 * Assigns `target[key] = value` only when it differs. Writing the same `textContent`
 * still replaces the text node (garbage for the renderer to collect), so redraws that
 * touch every row on every event must skip unchanged values.
 */
export function setIfChanged<T extends object, K extends keyof T>(target: T, key: K, value: T[K]): void {
  if (target[key] !== value) target[key] = value;
}

/** `document.getElementById` that fails loudly when the page and code disagree. */
export function byId<T extends HTMLElement = HTMLElement>(id: string): T {
  const el = document.getElementById(id);
  if (!el) throw new Error(`missing element #${id}`);
  return el as T;
}
