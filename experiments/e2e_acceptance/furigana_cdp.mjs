// Furigana GUI acceptance (docs/specs/furigana.md) through the Chrome DevTools Protocol, standard library only.
//
// Role: temporary experiment, not product code. Starts a built kikitori-app.exe with
// app/dev/fake_engine.py as the engine and WebView2's remote debugging port (the same way
// experiments/memory_soak/cdp_soak.mjs does), then: settings page → turn furigana on (the
// fake engine "downloads" the dictionary first) → start a session → check that the transcript
// and the overlay draw <ruby> over the Japanese lines, that the faded tail of a live line never
// splits a word, and that turning furigana off removes the readings. Screenshots of the
// kikitori windows only (the fake engine's own sentences) go to screenshots/.
//
// Usage (Node 24): node experiments/e2e_acceptance/furigana_cdp.mjs <kikitori-app.exe> [port]

import { spawn } from "node:child_process";
import { mkdirSync, writeFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import process from "node:process";
import { setTimeout as sleep } from "node:timers/promises";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const repoRoot = resolve(here, "..", "..");
const app = process.argv[2] ? resolve(process.argv[2]) : undefined;
const port = Number(process.argv[3] ?? 9333);
if (!app || !Number.isInteger(port) || port < 1024 || port > 65535) {
  console.error("usage: node furigana_cdp.mjs <kikitori-app.exe> [port]");
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
    if (r.exceptionDetails) throw new Error(`evaluate: ${r.exceptionDetails.text}`);
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

const CLICK = (selector) =>
  `(() => { const e = document.querySelector(${JSON.stringify(selector)}); if (!e) return false; e.click(); return true; })()`;
const STATE = `(() => ({
  start: document.getElementById('start')?.dataset.state ?? null,
  startDisabled: document.getElementById('start')?.disabled ?? null,
  furigana: document.getElementById('f-furigana')?.checked ?? null,
  furiganaDisabled: document.getElementById('f-furigana')?.disabled ?? null,
  furiganaStatus: document.getElementById('furigana-status')?.textContent ?? '',
  rubies: document.querySelectorAll('#transcript ruby').length,
  lines: document.querySelectorAll('#transcript > li').length,
}))()`;
// Every <ruby> in the transcript: whole word in one span (never split across stable/unstable).
const RUBY_CHECK = `(() => {
  const out = [];
  for (const r of document.querySelectorAll('#transcript ruby')) {
    const base = [...r.childNodes].filter((n) => n.nodeType === 3).map((n) => n.data).join('');
    out.push({ base, rt: r.querySelector('rt')?.textContent ?? '', span: r.parentElement?.className ?? '' });
  }
  return out;
})()`;

async function main() {
  const d = new Date();
  const two = (v) => String(v).padStart(2, "0");
  const stamp = `${d.getFullYear()}${two(d.getMonth() + 1)}${two(d.getDate())}-${two(d.getHours())}${two(d.getMinutes())}${two(d.getSeconds())}`;
  const runDir = join(here, "results", `${stamp}-furigana`);
  const shots = join(here, "screenshots");
  mkdirSync(runDir, { recursive: true });
  const engineCmd = ["uv", "run", "--no-project", "python", join(repoRoot, "app", "dev", "fake_engine.py")];
  const child = spawn(app, [], {
    cwd: runDir,
    stdio: ["ignore", "ignore", "ignore"],
    env: {
      ...process.env,
      WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS: `--remote-debugging-port=${port}`,
      WEBVIEW2_USER_DATA_FOLDER: join(runDir, "webview2"),
      KIKITORI_ENGINE_CMD: JSON.stringify(engineCmd),
      KIKITORI_ENGINE_CWD: repoRoot,
    },
  });
  const exited = new Promise((ok) => child.on("exit", ok));
  let page = null;
  let overlay = null;
  const results = {};
  try {
    const targets = await findTargets(60_000);
    page = await Cdp.connect(targets.main.webSocketDebuggerUrl);
    overlay = await Cdp.connect(targets.overlay.webSocketDebuggerUrl);
    // The fixed interface language makes the screenshots comparable.
    await page.eval(`document.getElementById('f-ui-locale').value = 'zh-TW'; document.getElementById('f-ui-locale').dispatchEvent(new Event('change'))`);
    await waitFor("engine ready", async () => {
      const s = await page.eval(STATE);
      return s.start === "idle" && s.startDisabled === false && s.furiganaDisabled === false;
    });

    // 1. Settings: turn furigana on → the dictionary downloads first, then it is on.
    await page.eval(CLICK('.nav-item[data-page="settings"]'));
    await page.eval(`document.getElementById('f-furigana').scrollIntoView({ block: 'center' })`);
    await page.eval(CLICK("#f-furigana"));
    const downloading = await waitFor("download progress", async () => {
      const s = await page.eval(STATE);
      return s.furiganaStatus ? s : null;
    }, 10_000);
    results.downloadStatus = downloading.furiganaStatus;
    await page.screenshot(join(shots, "29-furigana-download.png"));
    await waitFor("furigana on", async () => {
      const s = await page.eval(STATE);
      return s.furigana === true && s.furiganaDisabled === false && !s.furiganaStatus;
    });
    results.enabledAfterDownload = true;

    // 2. Models page: the dictionary block with its disk use and Delete.
    await page.eval(CLICK('.nav-item[data-page="models"]'));
    await sleep(1500);
    results.modelsBlock = await page.eval(`document.getElementById('furigana-dictionary')?.innerText ?? ''`);
    await page.eval(`document.getElementById('furigana-dictionary').scrollIntoView({ block: 'center' })`);
    await page.screenshot(join(shots, "30-furigana-models.png"));

    // 3. Start a session: Japanese lines carry <ruby>, in the transcript and in the overlay.
    await page.eval(CLICK('.nav-item[data-page="transcript"]'));
    await page.eval(CLICK("#overlay-visible"));
    await page.eval(CLICK("#start"));
    await waitFor("session running", async () => (await page.eval(STATE)).start === "running");
    // A live line with ruby in the faded tail (the stable prefix grows each partial).
    const live = await waitFor("a live Japanese line with furigana", async () => {
      const r = await page.eval(`(() => {
        const li = document.querySelector('#transcript > li.is-partial');
        if (!li || !li.querySelector('ruby')) return null;
        return { stable: li.querySelector('.utt-stable').innerHTML, unstable: li.querySelector('.utt-unstable').innerHTML };
      })()`);
      return r && r.unstable.includes("<ruby>") ? r : null;
    }, 60_000);
    results.liveLine = live;
    await page.screenshot(join(shots, "31-furigana-live.png"));
    await waitFor("three final lines", async () => (await page.eval(`document.querySelectorAll('#transcript > li:not(.is-partial)').length`)) >= 3, 60_000);
    results.rubies = await page.eval(RUBY_CHECK);
    await page.screenshot(join(shots, "32-furigana-transcript.png"));
    results.overlayRubies = await overlay.eval(`document.querySelectorAll('#lines ruby').length`);
    await waitFor("overlay ruby", async () => (await overlay.eval(`document.querySelectorAll('#lines ruby').length`)) > 0, 30_000);
    await overlay.screenshot(join(shots, "33-furigana-overlay.png"));

    // 4. Off while running: new lines have no readings.
    await page.eval(CLICK('.nav-item[data-page="settings"]'));
    await page.eval(CLICK("#f-furigana"));
    await waitFor("furigana off", async () => (await page.eval(STATE)).furigana === false);
    await page.eval(CLICK('.nav-item[data-page="transcript"]'));
    const before = await page.eval(`document.querySelectorAll('#transcript > li:not(.is-partial)').length`);
    await waitFor("a new line after turning furigana off", async () => {
      const r = await page.eval(`(() => {
        const items = [...document.querySelectorAll('#transcript > li:not(.is-partial)')];
        const last = items[items.length - 1];
        return items.length > ${before} + 1 && last && last.querySelector('.utt-orig').lang === 'ja' ? !last.querySelector('ruby') : null;
      })()`);
      return r === true;
    }, 60_000);
    results.offRemovesReadings = true;
    await page.eval(CLICK("#start"));
    await waitFor("session stopped", async () => (await page.eval(STATE)).start === "idle");
    results.ok = true;
  } finally {
    writeFileSync(join(runDir, "result.json"), JSON.stringify(results, null, 2));
    console.log(JSON.stringify(results, null, 2));
    page?.close();
    overlay?.close();
    child.kill();
    await Promise.race([exited, sleep(5000)]);
  }
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
