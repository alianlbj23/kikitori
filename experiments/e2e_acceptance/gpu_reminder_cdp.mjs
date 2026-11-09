// GUI check for the startup reminder to download the GPU acceleration component, through the Chrome DevTools Protocol.
//
// Role: temporary experiment, not product code. Starts a built kikitori-app.exe three times with the real
// engine in fake mode (KIKITORI_ENGINE_FAKE=1: an 8 GB NVIDIA card without cuBLAS) and one config folder:
// the first launch shows the reminder, its button opens the Models page at the component, closing hides it
// for that launch; the second launch shows it again and "don't remind me" is saved; the third shows nothing.
//
// Usage (Node 24, Windows): node experiments/e2e_acceptance/gpu_reminder_cdp.mjs <kikitori-app.exe> [port]

import { spawn, spawnSync } from "node:child_process";
import { mkdirSync, writeFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import process from "node:process";
import { setTimeout as sleep } from "node:timers/promises";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const repoRoot = resolve(here, "..", "..");
const app = process.argv[2] ? resolve(process.argv[2]) : undefined;
const port = Number(process.argv[3] ?? 9336);
if (!app || !Number.isInteger(port) || port < 1024 || port > 65535) {
  console.error("usage: node gpu_reminder_cdp.mjs <kikitori-app.exe> [port]");
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

/** One launch of the app against the real engine in fake mode (8 GB NVIDIA, no cuBLAS); returns its results. */
async function launch(configDir, runDir, label, steps) {
  const child = spawn(app, [], {
    cwd: runDir,
    stdio: ["ignore", "ignore", "ignore"],
    env: {
      ...process.env,
      KIKITORI_CONFIG_DIR: configDir,
      KIKITORI_ENGINE_FAKE: "1",
      WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS: `--remote-debugging-port=${port}`,
      WEBVIEW2_USER_DATA_FOLDER: join(runDir, "webview2"),
      KIKITORI_ENGINE_CMD: JSON.stringify(["uv", "run", "--project", repoRoot, "kikitori", "engine"]),
      KIKITORI_ENGINE_CWD: repoRoot,
    },
  });
  const exited = new Promise((ok) => child.on("exit", ok));
  let page = null;
  try {
    const targets = await findTargets(60_000);
    page = await Cdp.connect(targets.main.webSocketDebuggerUrl);
    await waitFor("main window loaded", async () => page.eval(`!!document.getElementById('f-ui-locale')`).catch(() => false), 30_000);
    await page.eval(`document.getElementById('f-ui-locale').value = 'zh-TW'; document.getElementById('f-ui-locale').dispatchEvent(new Event('change'))`);
    await waitFor("engine ready", async () => (await page.eval(START_STATE)) === "idle" && !(await page.eval(`document.getElementById('start').disabled`)), 90_000);
    await steps(page, label);
  } finally {
    page?.close();
    child.kill();
    await Promise.race([exited, sleep(5000)]);
    await sleep(1500);
  }
}

async function main() {
  const d = new Date();
  const two = (v) => String(v).padStart(2, "0");
  const stamp = `${d.getFullYear()}${two(d.getMonth() + 1)}${two(d.getDate())}-${two(d.getHours())}${two(d.getMinutes())}${two(d.getSeconds())}`;
  const runDir = join(here, "results", `${stamp}-gpu-reminder`);
  const configDir = join(runDir, "config");
  const shots = join(here, "screenshots");
  mkdirSync(runDir, { recursive: true });
  const results = {};
  const check = (name, ok, detail) => {
    results[name] = { ok: Boolean(ok), detail };
    console.log(`${ok ? "PASS" : "FAIL"} ${name}`, detail === undefined ? "" : JSON.stringify(detail).slice(0, 300));
  };
  const NOTICE = `(() => { const n = document.querySelector('#notices [data-key="gpu-reminder"]'); return n ? n.querySelector('p').textContent : null; })()`;
  const BUTTON = (label) =>
    `(() => { const b = [...document.querySelectorAll('#notices [data-key="gpu-reminder"] button')].find((x) => x.textContent === ${JSON.stringify(label)}); if (!b) return false; b.click(); return true; })()`;
  try {
    // 1st launch (first run): the reminder appears; "前往模型頁" opens the GPU block; × hides it for this launch.
    await launch(configDir, runDir, "first", async (page) => {
      const text = await waitFor("reminder", async () => page.eval(NOTICE), 60_000);
      check("first launch reminds about the GPU acceleration component", text.includes("GPU 加速元件") && text.includes("模型"), text);
      await page.screenshot(join(shots, "60-gpu-reminder.png"));
      check("go-to-models button", await page.eval(BUTTON("前往模型頁")));
      const onModels = await waitFor("models page", async () => page.eval(`!document.getElementById('page-models').hidden`), 5_000);
      const block = await waitFor("consent box", async () => page.eval(`!!document.getElementById('gpu-eula-consent')`), 15_000);
      // The page re-renders while the models list loads; the button scrolls again once it is drawn.
      const inView = await waitFor(
        "GPU block in view",
        async () => page.eval(`(() => { const r = document.getElementById('gpu-runtime').getBoundingClientRect(); return r.top >= 0 && r.bottom <= innerHeight; })()`),
        15_000,
      ).catch(() => false);
      check("it opens the Models page at the GPU component", onModels && block && inView, { onModels, block, inView });
      await page.screenshot(join(shots, "61-gpu-reminder-models.png"));
      await page.eval(`document.querySelector('#notices [data-key="gpu-reminder"] button[aria-label]').click()`);
      const gone = await waitFor("closed", async () => (await page.eval(NOTICE)) === null, 5_000);
      await sleep(3000);
      check("closing hides it for this launch", gone && (await page.eval(NOTICE)) === null);
    });
    // 2nd launch: closing was only for that launch, so it is back; "不要再提醒" saves the choice.
    await launch(configDir, runDir, "second", async (page) => {
      const text = await waitFor("reminder again", async () => page.eval(NOTICE), 60_000);
      check("next launch reminds again", !!text);
      check("dont-remind button", await page.eval(BUTTON("不要再提醒")));
      await waitFor("gone", async () => (await page.eval(NOTICE)) === null, 5_000);
      const saved = await waitFor(
        "saved",
        async () => {
          const s = await page.eval(`(async () => (await window.__TAURI_INTERNALS__.invoke('settings_get')).settings.ui.gpu_reminder_dismissed)()`).catch(() => null);
          return s === true ? s : null;
        },
        10_000,
      );
      check("dont-remind is saved in settings", saved === true);
    });
    // 3rd launch: no reminder any more.
    await launch(configDir, runDir, "third", async (page) => {
      await sleep(15_000);
      check("after dont-remind it stays away", (await page.eval(NOTICE)) === null);
    });
  } catch (e) {
    console.error("run aborted:", e);
    results.aborted = String(e);
  } finally {
    writeFileSync(join(runDir, "results.json"), JSON.stringify(results, null, 2));
  }
  const failed = Object.entries(results).filter(([k, v]) => k === "aborted" || !v.ok);
  console.log(`\nresults: ${join(runDir, "results.json")}`);
  console.log(failed.length ? `FAILED: ${failed.map(([k]) => k).join(", ")}` : "ALL PASS");
  process.exit(failed.length ? 1 : 0);
}

main();
