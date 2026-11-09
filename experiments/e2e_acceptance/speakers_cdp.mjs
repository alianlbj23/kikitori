// Speaker-label GUI acceptance (docs/specs/speaker-labels.md) through the Chrome DevTools Protocol, standard library only.
//
// Role: temporary experiment, not product code. Starts a built kikitori-app.exe with
// app/dev/fake_engine.py as the engine and WebView2's remote debugging port (as v03_cdp.mjs does),
// then checks what the user sees: choosing "guess how many" downloads the speaker model and saves
// the setting; a media session labels the transcript and the overlay; renaming a chip renames it
// in both windows; renaming a second label to the same name merges them (one name, one colour);
// the next session starts from "Speaker 1" again; deleting the model turns the labels off.
// The fake engine takes turns 1, 2, 1, 2... (it has no real voices).
//
// Usage (Node 24, Windows): node experiments/e2e_acceptance/speakers_cdp.mjs <kikitori-app.exe> [port]

import { spawn, spawnSync } from "node:child_process";
import { mkdirSync, writeFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import process from "node:process";
import { setTimeout as sleep } from "node:timers/promises";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const repoRoot = resolve(here, "..", "..");
const app = process.argv[2] ? resolve(process.argv[2]) : undefined;
const port = Number(process.argv[3] ?? 9335);
if (!app || !Number.isInteger(port) || port < 1024 || port > 65535) {
  console.error("usage: node speakers_cdp.mjs <kikitori-app.exe> [port]");
  process.exit(2);
}

class Cdp {
  static async connect(url) {
    const ws = new WebSocket(url);
    await new Promise((ok, fail) => {
      const timer = setTimeout(() => fail(new Error(`connecting to ${url} timed out`)), 10_000);
      ws.addEventListener("open", () => (clearTimeout(timer), ok()), { once: true });
      ws.addEventListener("error", () => (clearTimeout(timer), fail(new Error(`cannot connect to ${url}`))), { once: true });
    });
    return new Cdp(ws);
  }

  constructor(ws) {
    this.ws = ws;
    this.nextId = 1;
    this.pending = new Map();
    ws.addEventListener("message", (ev) => {
      const msg = JSON.parse(String(ev.data));
      const p = msg.id === undefined ? undefined : this.pending.get(msg.id);
      if (!p) return;
      this.pending.delete(msg.id);
      clearTimeout(p.timer);
      if (msg.error) p.fail(new Error(`${p.method}: ${msg.error.message}`));
      else p.ok(msg.result);
    });
  }

  send(method, params = {}) {
    const id = this.nextId++;
    return new Promise((ok, fail) => {
      const timer = setTimeout(() => (this.pending.delete(id), fail(new Error(`${method} timed out`))), 60_000);
      this.pending.set(id, { ok, fail, timer, method });
      this.ws.send(JSON.stringify({ id, method, params }));
    });
  }

  async eval(expression) {
    const r = await this.send("Runtime.evaluate", { expression, returnByValue: true, awaitPromise: true });
    if (r.exceptionDetails) throw new Error(`evaluate: ${r.exceptionDetails.exception?.description ?? r.exceptionDetails.text}`);
    return r.result.value;
  }

  async screenshot(file) {
    const r = await this.send("Page.captureScreenshot", { format: "png" });
    writeFileSync(file, Buffer.from(r.data, "base64"));
    console.log(`screenshot ${file}`);
  }

  close() {
    this.ws.close();
  }
}

async function findTargets(deadlineMs) {
  const end = Date.now() + deadlineMs;
  while (Date.now() < end) {
    try {
      const list = await (await fetch(`http://127.0.0.1:${port}/json/list`, { signal: AbortSignal.timeout(3000) })).json();
      const pages = list.filter((t) => t.type === "page");
      const overlay = pages.find((t) => t.url.includes("overlay.html"));
      const main = pages.find((t) => t !== overlay && /\/(index\.html)?$/.test(t.url.split("?")[0]));
      if (main && overlay) return { main, overlay };
    } catch {
      // The app is still starting; try again until the deadline.
    }
    await sleep(500);
  }
  throw new Error(`no main/overlay targets on port ${port}`);
}

async function waitFor(what, fn, timeoutMs = 60_000) {
  const end = Date.now() + timeoutMs;
  for (;;) {
    const v = await fn();
    if (v) return v;
    if (Date.now() > end) throw new Error(`timed out waiting for ${what}`);
    await sleep(300);
  }
}

/** A real global hotkey press (RegisterHotKey sees SendKeys input). */
function pressHotkey(letter) {
  const script = `Add-Type -AssemblyName System.Windows.Forms; [System.Windows.Forms.SendKeys]::SendWait('^%${letter}')`;
  const r = spawnSync("powershell.exe", ["-NoProfile", "-NonInteractive", "-Command", script], { timeout: 15_000 });
  if (r.status !== 0) throw new Error(`SendKeys ^%${letter} failed: ${r.stderr}`);
}

const CLICK = (selector) =>
  `(() => { const e = document.querySelector(${JSON.stringify(selector)}); if (!e) return false; e.click(); return true; })()`;
const SET = (selector, value) =>
  `(() => { const e = document.querySelector(${JSON.stringify(selector)}); if (!e) return false;
     if (e.type === 'checkbox') e.checked = ${JSON.stringify(value)}; else e.value = ${JSON.stringify(value)};
     e.dispatchEvent(new Event('input', { bubbles: true })); e.dispatchEvent(new Event('change', { bubbles: true })); return true; })()`;
const START_STATE = `document.getElementById('start')?.dataset.state ?? null`;

async function main() {
  const d = new Date();
  const two = (v) => String(v).padStart(2, "0");
  const stamp = `${d.getFullYear()}${two(d.getMonth() + 1)}${two(d.getDate())}-${two(d.getHours())}${two(d.getMinutes())}${two(d.getSeconds())}`;
  const runDir = join(here, "results", `${stamp}-speakers`);
  const shots = join(here, "screenshots");
  mkdirSync(runDir, { recursive: true });
  const engineCmd = ["uv", "run", "--no-project", "python", join(repoRoot, "app", "dev", "fake_engine.py")];
  const child = spawn(app, [], {
    cwd: runDir,
    stdio: ["ignore", "ignore", "ignore"],
    env: {
      ...process.env,
      KIKITORI_CONFIG_DIR: join(runDir, "config"),
      WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS: `--remote-debugging-port=${port}`,
      WEBVIEW2_USER_DATA_FOLDER: join(runDir, "webview2"),
      KIKITORI_ENGINE_CMD: JSON.stringify(engineCmd),
      KIKITORI_ENGINE_CWD: repoRoot,
    },
  });
  const exited = new Promise((ok) => child.on("exit", ok));
  const results = {};
  const check = (name, ok, detail) => {
    results[name] = { ok: Boolean(ok), detail };
    console.log(`${ok ? "PASS" : "FAIL"} ${name}`, detail === undefined ? "" : JSON.stringify(detail).slice(0, 300));
  };
  const transcriptChips = `[...document.querySelectorAll('#transcript .utt-voice:not([hidden])')].map((e) => ({ text: e.textContent, cls: e.className, voice: e.dataset.voice }))`;
  const overlayChips = `[...document.querySelectorAll('#lines .who.voice')].map((e) => ({ text: e.textContent, cls: e.className }))`;
  let page = null;
  let overlay = null;
  try {
    const targets = await findTargets(60_000);
    page = await Cdp.connect(targets.main.webSocketDebuggerUrl);
    overlay = await Cdp.connect(targets.overlay.webSocketDebuggerUrl);
    await waitFor("main window loaded", async () => page.eval(`!!document.getElementById('f-ui-locale')`).catch(() => false), 30_000);
    await page.eval(`document.getElementById('f-ui-locale').value = 'zh-TW'; document.getElementById('f-ui-locale').dispatchEvent(new Event('change'))`);
    await waitFor("engine ready", async () => (await page.eval(START_STATE)) === "idle" && !(await page.eval(`document.getElementById('start').disabled`)));

    // --- settings: choose "guess how many"; the model downloads first, then the choice is saved
    await page.eval(CLICK('.nav-item[data-page="models"]'));
    await waitFor("models list", async () => page.eval(`document.getElementById('speaker-model').textContent.includes('3D-Speaker')`), 30_000);
    const before = await page.eval(`document.getElementById('speaker-model').textContent`);
    check("models page lists the speaker model as not downloaded", before.includes("還沒下載"), before);
    await page.eval(CLICK('.nav-item[data-page="settings"]'));
    const options = await page.eval(`[...document.querySelectorAll('#f-speakers option')].map((o) => o.textContent)`);
    check("select offers off, auto and 2-6 people", options.length === 7 && options[1] === "自動判斷人數" && options[2] === "2 人", options);
    check("choose auto", await page.eval(SET("#f-speakers", "auto")));
    const progress = await waitFor(
      "download progress",
      async () => page.eval(`(() => { const s = document.getElementById('speakers-status'); return s.hidden ? null : s.textContent; })()`),
      10_000,
    );
    check("choosing labels downloads the model with progress", progress.includes("說話者模型"), progress);
    await page.eval(`document.getElementById('f-speakers').scrollIntoView({ block: 'center' })`);
    await page.screenshot(join(shots, "50-speakers-downloading.png"));
    const saved = await waitFor(
      "saved after download",
      async () =>
        page.eval(`(() => { const s = document.getElementById('f-speakers');
          return !s.disabled && s.value === 'auto' && document.getElementById('speakers-status').hidden; })()`),
      30_000,
    );
    check("the choice is saved once the model is downloaded", saved);
    await page.screenshot(join(shots, "51-speakers-settings.png"));

    // --- media session: chips in the transcript and the overlay
    await page.eval(CLICK('.nav-item[data-page="transcript"]'));
    await page.eval(CLICK("#start"));
    await waitFor("session running", async () => (await page.eval(START_STATE)) === "running");
    const chips = await waitFor(
      "two speakers in the transcript",
      async () => {
        const c = await page.eval(transcriptChips);
        return new Set(c.map((x) => x.text)).size >= 2 ? c : null;
      },
      90_000,
    );
    check("transcript labels the speakers", chips.some((c) => c.text === "說話者 1") && chips.some((c) => c.text === "說話者 2"), chips);
    const onOverlay = await waitFor(
      "overlay chips",
      async () => {
        const c = await overlay.eval(overlayChips);
        return c.length ? c : null;
      },
      30_000,
    );
    check("overlay names the speaker", onOverlay.every((c) => /^說話者 [12]$/.test(c.text)), onOverlay);
    await page.screenshot(join(shots, "52-speakers-transcript.png"));
    await overlay.screenshot(join(shots, "53-speakers-overlay.png"));

    // --- rename speaker 1; the overlay follows
    const rename = async (voice, name) => {
      await page.eval(`document.querySelector('#transcript .utt-voice[data-voice="${voice}"]:not([hidden])').click()`);
      await waitFor("rename box", async () => page.eval(`!document.getElementById('voice-rename').hidden`), 5_000);
      await page.eval(`(() => { const i = document.getElementById('voice-rename-input'); i.value = ${JSON.stringify(name)};
        document.getElementById('voice-rename').requestSubmit(); })()`);
    };
    await rename(1, "ぺこら");
    const renamed = await waitFor(
      "renamed in transcript",
      async () => {
        const c = await page.eval(transcriptChips);
        return c.some((x) => x.voice === "1" && x.text === "ぺこら") && c.every((x) => x.voice !== "1" || x.text === "ぺこら") ? c : null;
      },
      10_000,
    );
    check("renaming changes every row of that speaker", true, renamed.slice(0, 4));
    const overlayRenamed = await waitFor(
      "renamed on overlay",
      async () => {
        const names = await overlay.eval(`[...document.querySelectorAll('#lines .who.voice')].map((e) => e.textContent)`);
        return names.includes("ぺこら") ? names : null;
      },
      30_000,
    );
    check("overlay shows the new name", true, overlayRenamed);

    // --- rename speaker 2 to the same name: merged (one name, one colour)
    await rename(2, "ぺこら");
    const merged = await waitFor(
      "merged",
      async () => {
        const c = await page.eval(transcriptChips);
        const both = c.filter((x) => x.voice === "1" || x.voice === "2");
        return both.length >= 2 && both.every((x) => x.text === "ぺこら") ? both : null;
      },
      10_000,
    );
    check("two labels renamed alike merge (same name and colour)", new Set(merged.map((x) => x.cls)).size === 1, merged.slice(0, 4));
    await page.screenshot(join(shots, "54-speakers-merged.png"));

    // --- next session starts from "說話者 1" again
    await page.eval(CLICK("#start"));
    await waitFor("idle", async () => (await page.eval(START_STATE)) === "idle", 30_000);
    await page.eval(CLICK("#start"));
    await waitFor("running again", async () => (await page.eval(START_STATE)) === "running");
    const fresh = await waitFor(
      "chips of the new session",
      async () => {
        const c = await page.eval(transcriptChips);
        return c.length ? c : null;
      },
      60_000,
    );
    check("a new session forgets the names", fresh.every((c) => /^說話者 \d$/.test(c.text)), fresh);
    await page.eval(CLICK("#start"));
    await waitFor("idle again", async () => (await page.eval(START_STATE)) === "idle", 30_000);

    // --- delete the model: labels are turned off
    await page.eval(CLICK('.nav-item[data-page="models"]'));
    await waitFor("delete button", async () => page.eval(`!!document.querySelector('#speaker-model .gpu-actions .ghost')`), 10_000);
    await page.eval(CLICK("#speaker-model .gpu-actions .ghost"));
    await waitFor("confirm", async () => page.eval(`!!document.querySelector('#speaker-model .delete-confirm .danger')`), 5_000);
    await page.eval(CLICK("#speaker-model .delete-confirm .danger"));
    const after = await waitFor(
      "deleted",
      async () => {
        const text = await page.eval(`document.getElementById('speaker-model').textContent`);
        return text.includes("還沒下載") ? text : null;
      },
      15_000,
    );
    check("deleting the model frees it", true, after);
    await page.eval(CLICK('.nav-item[data-page="settings"]'));
    const off = await waitFor("select back to off", async () => (await page.eval(`document.getElementById('f-speakers').value`)) === "off", 10_000);
    check("deleting turns speaker labels off", off);
  } catch (e) {
    console.error("run aborted:", e);
    results.aborted = String(e);
  } finally {
    writeFileSync(join(runDir, "results.json"), JSON.stringify(results, null, 2));
    page?.close();
    overlay?.close();
    child.kill();
    await Promise.race([exited, sleep(5000)]);
  }
  const failed = Object.entries(results).filter(([k, v]) => k === "aborted" || !v.ok);
  console.log(`\nresults: ${join(runDir, "results.json")}`);
  console.log(failed.length ? `FAILED: ${failed.map(([k]) => k).join(", ")}` : "ALL PASS");
  process.exit(failed.length ? 1 : 0);
}

main();
