// 遠端運算的 GUI 實機驗收（docs/specs/remote-compute.md「驗收」T15），暫時性實驗工具。
//
// A＝這台 Windows 上建置好的 kikitori-app.exe（真的引擎，聲音來源換成即時播放的 WAV：wav_engine.py），
// B＝另一台電腦上的無介面運算主機（`kikitori` 選單 →「運算主機」→ 提供運算中，按 p 開配對）。
// 以 WebView2 的 DevTools 協定操作：設定頁填運算主機 → 配對（A 顯示的 6 位數寫到 results/gui_digits.txt，
// 由操作者在 B 輸入）→ 確認 → 左側欄改成遠端 → 開始 → 等字幕 → 截圖與結果寫到 results/。
//
//   node experiments/remote_compute/gui_cdp.mjs <kikitori-app.exe> <運算主機位址> [CDP 埠]
import { spawn, spawnSync } from "node:child_process";
import { mkdirSync, writeFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import process from "node:process";
import { setTimeout as sleep } from "node:timers/promises";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const repoRoot = resolve(here, "..", "..");
const app = process.argv[2] ? resolve(process.argv[2]) : undefined;
const remoteHost = process.argv[3];
const port = Number(process.argv[4] ?? 9337);
if (!app || !remoteHost || !Number.isInteger(port) || port < 1024 || port > 65535) {
  console.error("usage: node gui_cdp.mjs <kikitori-app.exe> <host> [port]");
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


const WAV = join(repoRoot, "experiments", "e2e_acceptance", "data", "final_ja.wav");
const DIGITS_FILE = join(here, "results", "gui_digits.txt");
const OPEN_DIALOGS = `[...document.querySelectorAll('dialog[open]')].map((d) => d.id)`;

async function main() {
  const d = new Date();
  const two = (v) => String(v).padStart(2, "0");
  const stamp = `${d.getFullYear()}${two(d.getMonth() + 1)}${two(d.getDate())}-${two(d.getHours())}${two(d.getMinutes())}${two(d.getSeconds())}`;
  const runDir = join(here, "results", `${stamp}-gui`);
  mkdirSync(runDir, { recursive: true });
  const engineCmd = ["uv", "run", "python", "-X", "utf8", join(here, "wav_engine.py")];
  const child = spawn(app, [], {
    cwd: runDir,
    stdio: ["ignore", "ignore", "ignore"],
    env: {
      ...process.env,
      KIKITORI_CONFIG_DIR: join(runDir, "config"),
      KIKITORI_TEST_WAV: WAV,
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
  const shot = (page, name) => page.screenshot(join(runDir, `${name}.png`));
  let page = null;
  try {
    const targets = await findTargets(60_000);
    page = await Cdp.connect(targets.main.webSocketDebuggerUrl);
    await waitFor("main window loaded", async () => page.eval(`!!document.getElementById('f-ui-locale')`).catch(() => false), 30_000);
    await page.eval(`document.getElementById('f-ui-locale').value = 'zh-TW'; document.getElementById('f-ui-locale').dispatchEvent(new Event('change'))`);
    await waitFor(
      "engine ready",
      async () => (await page.eval(START_STATE)) === "idle" && !(await page.eval(`document.getElementById('start').disabled`)),
      90_000,
    );
    // 第一次啟動可能有提醒對話框（GPU 加速元件等）：記下來再關掉
    const firstDialogs = await page.eval(OPEN_DIALOGS);
    if (firstDialogs.length) {
      await shot(page, "00-first-run-dialog");
      await page.eval(`document.querySelectorAll('dialog[open]').forEach((d) => d.close())`);
    }

    // --- 設定頁：填運算主機、配對
    await page.eval(CLICK('.nav-item[data-page="settings"]'));
    check("remote form exists", await page.eval(SET("#f-remote-host", remoteHost)));
    await page.eval(SET("#f-remote-port", "47100"));
    await page.eval(CLICK("#remote-save"));
    await sleep(1500);
    await page.eval(CLICK("#remote-pair"));
    const digits = await waitFor(
      "pairing digits",
      async () =>
        page.eval(
          `(() => { const e = document.getElementById('pair-digits'); return e && !e.hidden && /^[0-9]{3} [0-9]{3}$/.test(e.textContent) ? e.textContent : null; })()`,
        ),
      30_000,
    );
    check("A shows six digits as 123 456", /^[0-9]{3} [0-9]{3}$/.test(digits), digits);
    await shot(page, "01-pair-digits");
    writeFileSync(DIGITS_FILE, digits.replace(" ", ""));
    console.log(`DIGITS ${digits} (type them on the compute host)`);
    // B 接受後 A 問「運算主機上顯示配對成功了嗎？」：按「是」
    await waitFor(
      "confirm question",
      async () =>
        page.eval(`document.querySelectorAll('#pair-actions button').length === 2`),  // 「是」「否」
      180_000,
    );
    const confirmText = await page.eval(`document.getElementById('pair-text').textContent`);
    await shot(page, "02-pair-confirm");
    await page.eval(CLICK("#pair-actions button.primary"));
    await waitFor("pairing done", async () => page.eval(`document.querySelectorAll('#pair-actions button').length === 1`), 30_000);
    const doneText = await page.eval(`document.getElementById('pair-text').textContent`);
    await shot(page, "03-pair-done");
    await page.eval(CLICK("#pair-actions button.primary"));
    const servers = await waitFor(
      "paired host listed",
      async () => {
        const n = await page.eval(`document.querySelectorAll('#remote-servers li').length`);
        return n > 0 ? await page.eval(`document.getElementById('remote-servers').innerText`) : null;
      },
      15_000,
    );
    check("pairing finished and the host is listed", servers.length > 0, { confirmText, doneText, servers });

    // --- 左側欄改成遠端，開始
    await page.eval(CLICK('.compute-choice[data-compute="remote"]'));
    await waitFor(
      "remote chosen",
      async () => page.eval(`document.querySelector('.compute-choice[data-compute="remote"]').getAttribute('aria-checked') === 'true'`),
      15_000,
    );
    await page.eval(CLICK('.nav-item[data-page="transcript"]'));
    const t0 = Date.now();
    await page.eval(CLICK("#start"));
    await waitFor("running", async () => (await page.eval(START_STATE)) === "running", 90_000);
    const blocking = await page.eval(OPEN_DIALOGS);
    check("starting in remote mode asks for no local downloads", !blocking.length, blocking);
    await sleep(20_000);
    const rtt = await page.eval(`(() => { const e = document.getElementById('compute-rtt'); return e && !e.hidden ? e.textContent : null; })()`);
    check("the rail shows the compute host's round-trip time", Boolean(rtt), rtt);
    await shot(page, "04-running");
    // 音檔 142 秒：等它播完、運算主機處理完（idle）
    await waitFor("session ends", async () => (await page.eval(START_STATE)) === "idle", 240_000);
    const lines = await page.eval(`[...document.querySelectorAll('#transcript > li')].map((li) => li.innerText.replace(/\\s+/g, ' ').trim())`);
    await shot(page, "05-finished");
    check("all 13 sentences arrived as subtitles", lines.length >= 13, { count: lines.length, seconds: Math.round((Date.now() - t0) / 1000) });
    writeFileSync(join(runDir, "transcript.json"), JSON.stringify(lines, null, 2));
  } catch (err) {
    check("script ran to the end", false, String(err?.stack ?? err));
    if (page) await shot(page, "99-error").catch(() => {});
  } finally {
    writeFileSync(join(runDir, "results.json"), JSON.stringify(results, null, 2));
    page?.close();
    child.kill();
    await Promise.race([exited, sleep(5000)]);
  }
  const failed = Object.entries(results)
    .filter(([, r]) => !r.ok)
    .map(([k]) => k);
  console.log(failed.length ? `FAILED: ${failed.join(", ")}` : "ALL PASS");
  console.log(`results: ${runDir}`);
  process.exit(failed.length ? 1 : 0);
}

main();
