// v0.3 GUI acceptance (docs/specs/v0.3-features.md) through the Chrome DevTools Protocol, standard library only.
//
// Role: temporary experiment, not product code. Starts a built kikitori-app.exe with
// app/dev/fake_engine.py (metrics profile "bgm") as the engine and WebView2's remote debugging
// port (as furigana_cdp.mjs does), then checks what the user sees for each v0.3 item:
//   G7 reading layout fields + G6 reset button (settings page), G5 stream info (rail),
//   G4 correction rules with line errors + "add to glossary" from a transcript selection,
//   G1/G2 live translation stable prefix and fade-in spans on the overlay, G8 BGM warning,
//   G3 speaker labels in interpret mode and the "say again" notice (overlay_notice),
//   G6 pause / recent-lines shortcuts (real Ctrl+Alt+P / H key presses via SendKeys).
// Screenshots of the kikitori windows only (the fake engine's own sentences) go to screenshots/.
//
// Usage (Node 24, Windows): node experiments/e2e_acceptance/v03_cdp.mjs <kikitori-app.exe> [port]

import { spawn, spawnSync } from "node:child_process";
import { mkdirSync, writeFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import process from "node:process";
import { setTimeout as sleep } from "node:timers/promises";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const repoRoot = resolve(here, "..", "..");
const app = process.argv[2] ? resolve(process.argv[2]) : undefined;
const port = Number(process.argv[3] ?? 9334);
if (!app || !Number.isInteger(port) || port < 1024 || port > 65535) {
  console.error("usage: node v03_cdp.mjs <kikitori-app.exe> [port]");
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
  const runDir = join(here, "results", `${stamp}-v03`);
  const shots = join(here, "screenshots");
  mkdirSync(runDir, { recursive: true });
  const engineCmd = ["uv", "run", "--no-project", "python", join(repoRoot, "app", "dev", "fake_engine.py"), "--metrics", "bgm"];
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
  let page = null;
  let overlay = null;
  try {
    const targets = await findTargets(60_000);
    page = await Cdp.connect(targets.main.webSocketDebuggerUrl);
    overlay = await Cdp.connect(targets.overlay.webSocketDebuggerUrl);
    await waitFor("main window loaded", async () => page.eval(`!!document.getElementById('f-ui-locale')`).catch(() => false), 30_000);
    await page.eval(`document.getElementById('f-ui-locale').value = 'zh-TW'; document.getElementById('f-ui-locale').dispatchEvent(new Event('change'))`);
    await waitFor("engine ready", async () => (await page.eval(START_STATE)) === "idle" && !(await page.eval(`document.getElementById('start').disabled`)));

    // --- G7 reading layout + G6 reset button (settings page)
    await page.eval(CLICK('.nav-item[data-page="settings"]'));
    for (const [sel, v] of [
      ["#f-orig-scale", "120"],
      ["#f-orig-position", "below"],
      ["#f-max-line", "20"],
      ["#f-pace", true],
      ["#f-furigana-first", true],
    ]) {
      check(`set ${sel}`, await page.eval(SET(sel, v)));
    }
    await page.eval(CLICK("#settings-save"));
    await sleep(1500);
    const style = await overlay.eval(`(() => { const f = document.getElementById('frame');
      return { scale: f.style.getPropertyValue('--orig-scale'), original: f.dataset.original, maxLine: f.classList.contains('has-max-line'),
               line: f.style.getPropertyValue('--max-line') }; })()`);
    check("G7 overlay got the reading layout", style.scale === "1.2" && style.original === "below" && style.maxLine, style);
    await page.eval(`document.getElementById('f-orig-scale').scrollIntoView({ block: 'center' })`);
    await page.screenshot(join(shots, "40-v03-reading-layout.png"));
    await page.eval(CLICK("#reset-overlay"));
    const visible = await waitFor("overlay visible after reset", async () => page.eval(`document.getElementById('overlay-visible').checked`), 10_000);
    check("G6 reset shows the overlay", visible);

    // --- G4 correction rules: one good rule, one bad line
    await page.eval(`document.getElementById('f-corrections').scrollIntoView({ block: 'center' })`);
    await page.eval(SET("#f-corrections", "ホロ ライブ => ホロライブ\nこの行は区切りがない"));
    const fixErr = await page.eval(`document.getElementById('corrections-error').textContent`);
    check("G4 bad correction line is pointed at", fixErr.includes("2"), fixErr);
    await page.screenshot(join(shots, "41-v03-corrections.png"));
    await page.eval(SET("#f-corrections", "ホロ ライブ => ホロライブ"));
    await page.eval(CLICK("#glossary-save"));
    await sleep(1000);

    // --- G5 stream info in the rail
    await page.eval(`document.getElementById('session-info-block').open = true`);
    await page.eval(SET("#session-info", "主播：テスト配信者、遊戲：Minecraft"));
    const saved = await waitFor("stream info saved", async () => page.eval(`document.getElementById('session-info-status').textContent`), 10_000);
    check("G5 stream info is saved", saved !== "", saved);
    await page.screenshot(join(shots, "42-v03-session-info.png"));

    // --- Media session: G1/G2 on the overlay, G8 BGM warning
    await page.eval(CLICK('.nav-item[data-page="transcript"]'));
    await page.eval(CLICK("#start"));
    await waitFor("session running", async () => (await page.eval(START_STATE)) === "running");
    const live = await waitFor("a live line with a settled translation prefix", async () => {
      const r = await overlay.eval(`(() => { const l = document.querySelector('#lines .line.is-partial .tr');
        if (!l) return null; const s = l.querySelector('.stable')?.textContent ?? ''; const u = l.querySelector('.unstable')?.textContent ?? '';
        return s && u ? { stable: s, unstable: u, minHeight: l.closest('.line').style.minHeight } : null; })()`);
      return r;
    }, 60_000);
    check("G1 live translation is split into settled prefix and tail", true, live);
    const fresh = await waitFor("fade-in span", async () => overlay.eval(`document.querySelectorAll('#lines .fresh').length`), 30_000);
    check("G2 newly appeared characters fade in", fresh > 0, fresh);
    await overlay.screenshot(join(shots, "43-v03-overlay-live.png"));
    const bgm = await waitFor(
      "BGM warning",
      async () => page.eval(`[...document.querySelectorAll('#notices [data-key="metrics-bgm"]')].map((n) => n.textContent).join('')`),
      40_000,
    );
    check("G8 background music warning", bgm.includes("背景"), bgm);
    await page.screenshot(join(shots, "44-v03-bgm-warning.png"));

    // --- G6 pause and recent lines (real hotkeys)
    pressHotkey("p");
    const paused = await waitFor("paused badge", async () => overlay.eval(`!document.getElementById('paused').hidden`), 5_000);
    const frozen = await overlay.eval(`document.getElementById('lines').innerText`);
    await sleep(3000);
    const still = await overlay.eval(`document.getElementById('lines').innerText`);
    check("G6 pause freezes the overlay", paused && frozen === still, { frozen, still });
    pressHotkey("p");
    await waitFor("unpaused", async () => overlay.eval(`document.getElementById('paused').hidden`), 5_000);
    pressHotkey("h");
    const recentCount = await waitFor("recent panel", async () => overlay.eval(`document.getElementById('history').hidden ? 0 : document.querySelectorAll('#history-lines .history-line').length`), 5_000);
    check("G6 recent subtitles panel", recentCount > 0, recentCount);
    await overlay.screenshot(join(shots, "45-v03-overlay-recent.png"));
    pressHotkey("h");

    // --- G4 add to glossary from a transcript selection
    const selected = await page.eval(`(() => {
      const orig = document.querySelector('#transcript > li:not(.is-partial) .utt-orig .utt-stable');
      if (!orig || !orig.firstChild) return null;
      const r = document.createRange(); const node = orig.firstChild; r.setStart(node, 0); r.setEnd(node, Math.min(3, node.length));
      const s = document.getSelection(); s.removeAllRanges(); s.addRange(r); return s.toString(); })()`);
    await sleep(300);
    const enabled = await page.eval(`!document.getElementById('add-to-glossary').disabled`);
    await page.eval(CLICK("#add-to-glossary"));
    await sleep(800);
    const added = await page.eval(`(() => { const rows = [...document.querySelectorAll('#glossary-rows [data-field="source"]')]; return rows.map((i) => i.value); })()`);
    check("G4 selection becomes a glossary row", enabled && added.includes(selected), { selected, added });
    await page.screenshot(join(shots, "46-v03-add-to-glossary.png"));
    await page.eval(CLICK("#glossary-reset"));
    await page.eval(CLICK('.nav-item[data-page="transcript"]'));
    await page.eval(CLICK("#start"));
    await waitFor("idle", async () => (await page.eval(START_STATE)) === "idle", 30_000);

    // --- G3 interpret mode: speaker labels, "say again" notice
    await page.eval(CLICK('.mode[data-mode="interpret"]'));
    await sleep(800);
    await page.eval(CLICK("#start"));
    await waitFor("interpret running", async () => (await page.eval(START_STATE)) === "running");
    const labels = await waitFor("speaker labels in the transcript", async () => {
      const who = await page.eval(`[...document.querySelectorAll('#transcript .utt-orig[data-who]')].map((e) => e.dataset.who)`);
      return new Set(who).size >= 2 ? who : null;
    }, 90_000);
    check("G3 transcript labels both speakers", true, labels);
    const chips = await overlay.eval(`[...document.querySelectorAll('#lines .who')].map((e) => e.textContent)`);
    check("G3 overlay names the speaker", chips.length > 0, chips);
    await page.screenshot(join(shots, "47-v03-interpret-transcript.png"));
    pressHotkey("r");
    const notice = await waitFor("say-again notice", async () => overlay.eval(`document.getElementById('notice').hidden ? '' : document.getElementById('notice').textContent`), 5_000);
    check("G3 say-again shows the partner-language phrase", notice !== "", notice);
    await overlay.screenshot(join(shots, "48-v03-interpret-overlay.png"));
    await page.eval(CLICK("#start"));
    await waitFor("idle again", async () => (await page.eval(START_STATE)) === "idle", 30_000);
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
