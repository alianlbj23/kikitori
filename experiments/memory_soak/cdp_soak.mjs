// GUI memory soak through the Chrome DevTools Protocol (WebView2), standard library only.
//
// Role: the diagnostic counterpart of gui_soak.py (scenario 7). gui_soak.py drives the
// release build through WebDriver and only sees process totals; this script launches a
// build of the app with WebView2 remote debugging, feeds it the dev fake engine
// (app/dev/fake_engine.py) at the real engine's event rate, and records per sample:
//
// - private bytes of the main-window and overlay renderer processes (identified by a
//   one-off 192 MiB allocation probe, since CDP targets carry no process id),
//   before and after a forced GC;
// - JS heap after GC (Runtime.getHeapUsage), DOM counters (Memory.getDOMCounters),
//   Performance.getMetrics (Nodes, JSEventListeners, LayoutCount, RecalcStyleCount...);
// - optional heap snapshots (start / end) aggregated by constructor name, with the
//   detached DOM nodes counted, so "what grows" has an answer.
//
// It deliberately never enables the Runtime, Debugger, Console or Log domains (unless
// `webdriver_like=true`, which imitates the msedgedriver session gui_soak.py measures
// through): an attached client with those enabled makes the renderer keep console
// messages and remote objects for it, which would be a leak of the measurement, not of the app.
//
// Usage (Node 24, from the repository root):
//   node experiments/memory_soak/cdp_soak.mjs                       interactive menu
//   node experiments/memory_soak/cdp_soak.mjs --batch minutes=20 gc=true toggles=true
// Results: experiments/memory_soak/results/<time>-cdp-<label>/ (git-ignored).

import { execFile, spawn } from "node:child_process";
import { appendFileSync, existsSync, mkdirSync, rmSync, writeFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import process from "node:process";
import { createInterface } from "node:readline/promises";
import { setTimeout as sleep } from "node:timers/promises";
import { fileURLToPath, pathToFileURL } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const repoRoot = resolve(here, "..", "..");
const MIB = 1024 * 1024;

/** Parameters, their defaults and how to read them (the menu asks for each one). */
const PARAMS = [
  { key: "label", def: "run", help: "results folder suffix", parse: (v) => (/^[\w-]{1,40}$/.test(v) ? v : null) },
  { key: "minutes", def: "20", help: "minutes of running session", parse: num(0.5, 600) },
  { key: "sample_secs", def: "15", help: "seconds between samples", parse: num(2, 600) },
  { key: "gc", def: "true", help: "force a GC before each sample's heap/after-GC numbers", parse: bool },
  { key: "toggles", def: "true", help: "toggle 顯示翻譯 /2 min, overlay /3 min, locale /5 min", parse: bool },
  { key: "snapshots", def: "true", help: "heap snapshots after warm-up and at the end", parse: bool },
  { key: "partial_steps", def: "20", help: "fake engine partials per sentence", parse: num(1, 100) },
  { key: "partial_interval", def: "0.2", help: "fake engine seconds between partials", parse: num(0.05, 5) },
  {
    key: "webdriver_like",
    def: "false",
    help: "also enable Runtime/Page/Log like msedgedriver does (to compare with gui_soak.py)",
    parse: bool,
  },
  { key: "window_min", def: "10", help: "plateau verdict: slope over the last N minutes", parse: num(1, 600) },
  {
    key: "app",
    def: join(repoRoot, "app", "src-tauri", "target", "debug", "kikitori-app.exe"),
    help: "app executable (tauri build --debug --no-bundle)",
    parse: (v) => (existsSync(v) ? v : null),
  },
  { key: "port", def: "9222", help: "remote debugging port", parse: num(1024, 65535) },
];

function num(lo, hi) {
  return (v) => {
    const n = Number(v);
    return Number.isFinite(n) && n >= lo && n <= hi ? n : null;
  };
}

function bool(v) {
  if (/^(true|yes|1|y)$/i.test(v)) return true;
  if (/^(false|no|0|n)$/i.test(v)) return false;
  return null;
}

async function readParams(argv) {
  const out = {};
  if (argv[0] === "--batch") {
    const given = Object.fromEntries(
      argv.slice(1).map((a) => {
        const i = a.indexOf("=");
        if (i < 1) throw new Error(`expected key=value, got ${a}`);
        return [a.slice(0, i), a.slice(i + 1)];
      }),
    );
    for (const k of Object.keys(given)) {
      if (!PARAMS.some((p) => p.key === k)) throw new Error(`unknown parameter ${k}`);
    }
    for (const p of PARAMS) {
      const v = p.parse(given[p.key] ?? p.def);
      if (v === null) throw new Error(`invalid ${p.key}=${given[p.key]}`);
      out[p.key] = v;
    }
    return out;
  }
  const rl = createInterface({ input: process.stdin, output: process.stdout });
  try {
    console.log("\nkikitori CDP memory soak (Enter = default)\n");
    for (const p of PARAMS) {
      for (;;) {
        const answer = (await rl.question(`${p.key} — ${p.help} [${p.def}]: `)).trim();
        const v = p.parse(answer === "" ? p.def : answer);
        if (v !== null) {
          out[p.key] = v;
          break;
        }
        console.log("  invalid value, try again");
      }
    }
  } finally {
    rl.close();
  }
  return out;
}

// ---------------------------------------------------------------- CDP client

class Cdp {
  /** @param {string} url */
  static async connect(url, timeoutMs = 10_000) {
    const ws = new WebSocket(url);
    await new Promise((ok, fail) => {
      const timer = setTimeout(() => {
        ws.close();
        fail(new Error(`connecting to ${url} timed out after ${timeoutMs} ms`));
      }, timeoutMs);
      ws.addEventListener("open", () => (clearTimeout(timer), ok()), { once: true });
      ws.addEventListener("error", () => (clearTimeout(timer), fail(new Error(`cannot connect to ${url}`))), { once: true });
    });
    return new Cdp(ws);
  }

  constructor(ws) {
    this.ws = ws;
    this.nextId = 1;
    this.pending = new Map();
    this.handlers = new Map();
    ws.addEventListener("message", (ev) => {
      const msg = JSON.parse(typeof ev.data === "string" ? ev.data : Buffer.from(ev.data).toString("utf8"));
      if (msg.id !== undefined) {
        const p = this.pending.get(msg.id);
        if (!p) return;
        this.pending.delete(msg.id);
        clearTimeout(p.timer);
        if (msg.error) p.fail(new Error(`${p.method}: ${msg.error.message}`));
        else p.ok(msg.result);
      } else {
        this.handlers.get(msg.method)?.(msg.params);
      }
    });
    ws.addEventListener("close", () => {
      for (const p of this.pending.values()) {
        clearTimeout(p.timer);
        p.fail(new Error(`${p.method}: connection closed`));
      }
      this.pending.clear();
    });
  }

  send(method, params = {}, timeoutMs = 60_000) {
    const id = this.nextId++;
    return new Promise((ok, fail) => {
      const timer = setTimeout(() => {
        this.pending.delete(id);
        fail(new Error(`${method}: timed out after ${timeoutMs} ms`));
      }, timeoutMs);
      this.pending.set(id, { ok, fail, timer, method });
      this.ws.send(JSON.stringify({ id, method, params }));
    });
  }

  on(method, handler) {
    this.handlers.set(method, handler);
  }

  /** Runtime.evaluate without Runtime.enable (see the module comment). */
  async eval(expression) {
    const r = await this.send("Runtime.evaluate", { expression, returnByValue: true, awaitPromise: true });
    if (r.exceptionDetails) throw new Error(`evaluate failed: ${r.exceptionDetails.text} ${expression.slice(0, 80)}`);
    return r.result.value;
  }

  close() {
    this.ws.close();
  }
}

async function fetchJson(url, timeoutMs = 3000) {
  const res = await fetch(url, { signal: AbortSignal.timeout(timeoutMs) });
  if (!res.ok) throw new Error(`${url}: HTTP ${res.status}`);
  return res.json();
}

async function findTargets(port, deadlineMs) {
  const end = Date.now() + deadlineMs;
  let last = null;
  while (Date.now() < end) {
    try {
      const list = await fetchJson(`http://127.0.0.1:${port}/json/list`);
      const pages = list.filter((t) => t.type === "page");
      const overlay = pages.find((t) => t.url.includes("overlay.html"));
      const main = pages.find((t) => t !== overlay && /\/(index\.html)?$/.test(t.url.split("?")[0]));
      if (main && overlay) return { main, overlay };
      last = pages.map((p) => p.url);
    } catch (e) {
      last = String(e);
    }
    await sleep(500);
  }
  throw new Error(`no main/overlay targets on port ${port}: ${JSON.stringify(last)}`);
}

// ---------------------------------------------------------------- processes

/** Private bytes (MiB) of every WebView2 / app process, with parent and command line. */
function processTable() {
  const script =
    "Get-CimInstance Win32_Process -Filter \"Name='msedgewebview2.exe' OR Name='kikitori-app.exe'\" | " +
    "Select-Object ProcessId,ParentProcessId,PrivatePageCount,WorkingSetSize,CommandLine | ConvertTo-Json -Compress";
  return new Promise((ok, fail) => {
    execFile(
      "powershell.exe",
      ["-NoProfile", "-NonInteractive", "-Command", script],
      { timeout: 30_000, windowsHide: true, maxBuffer: 16 * MIB },
      (err, stdout) => {
        if (err) return fail(new Error(`process query failed: ${err.message}`));
        const text = stdout.trim();
        const rows = text === "" ? [] : JSON.parse(text);
        ok((Array.isArray(rows) ? rows : [rows]).map((r) => ({
          pid: r.ProcessId,
          ppid: r.ParentProcessId,
          privateMiB: Number(r.PrivatePageCount) / MIB,
          wsMiB: Number(r.WorkingSetSize) / MIB,
          cmd: r.CommandLine ?? "",
        })));
      },
    );
  });
}

/** Ends `pid` and every process it started (Windows `taskkill /T`). */
function killTree(pid) {
  return new Promise((ok) => {
    execFile("taskkill.exe", ["/PID", String(pid), "/T", "/F"], { timeout: 30_000, windowsHide: true }, (err) => {
      if (err) console.error(`taskkill ${pid} failed: ${err.message}`);
      ok();
    });
  });
}

/** Processes descending from `rootPid` (the app). */
function treeOf(rows, rootPid) {
  const keep = new Set([rootPid]);
  for (let changed = true; changed; ) {
    changed = false;
    for (const r of rows) {
      if (!keep.has(r.pid) && keep.has(r.ppid)) {
        keep.add(r.pid);
        changed = true;
      }
    }
  }
  return rows.filter((r) => keep.has(r.pid));
}

/** Which renderer belongs to `cdp`'s page: the one whose private bytes jump by a probe allocation. */
async function rendererOf(cdp, appPid) {
  const renderers = () => processTable().then((rows) => treeOf(rows, appPid).filter((r) => r.cmd.includes("--type=renderer")));
  const before = await renderers();
  await cdp.eval("globalThis.__soakProbe = new Uint8Array(192 * 1024 * 1024).fill(1); 1");
  const during = await renderers();
  await cdp.eval("delete globalThis.__soakProbe; 1");
  await cdp.send("HeapProfiler.collectGarbage");
  let best = null;
  for (const r of during) {
    const b = before.find((x) => x.pid === r.pid);
    if (!b) continue;
    const delta = r.privateMiB - b.privateMiB;
    if (!best || delta > best.delta) best = { pid: r.pid, delta };
  }
  if (!best || best.delta < 150) throw new Error(`renderer probe inconclusive: ${JSON.stringify(best)}`);
  return best.pid;
}

// ---------------------------------------------------------------- heap snapshots

async function heapSnapshot(cdp, file) {
  const chunks = [];
  cdp.on("HeapProfiler.addHeapSnapshotChunk", (p) => chunks.push(p.chunk));
  await cdp.send("HeapProfiler.enable");
  await cdp.send("HeapProfiler.takeHeapSnapshot", { reportProgress: false, captureNumericValue: false }, 300_000);
  await cdp.send("HeapProfiler.disable");
  const text = chunks.join("");
  writeFileSync(file, text);
  return aggregate(JSON.parse(text));
}

/** Count and self size per "type:name" (strings and code lumped), plus detached DOM nodes. */
function aggregate(snap) {
  const meta = snap.snapshot.meta;
  const f = meta.node_fields;
  const n = f.length;
  const types = meta.node_types[0];
  const iType = f.indexOf("type");
  const iName = f.indexOf("name");
  const iSize = f.indexOf("self_size");
  const iDet = f.indexOf("detachedness");
  const groups = new Map();
  let detached = 0;
  const detachedByName = new Map();
  let total = 0;
  for (let i = 0; i < snap.nodes.length; i += n) {
    const type = types[snap.nodes[i + iType]];
    const rawName = snap.strings[snap.nodes[i + iName]];
    const size = snap.nodes[i + iSize];
    total += size;
    let key;
    if (type === "string" || type === "concatenated string" || type === "sliced string") key = "(strings)";
    else if (type === "code") key = "(code)";
    else if (type === "number") key = "(numbers)";
    else key = `${type}:${String(rawName).slice(0, 80)}`;
    const g = groups.get(key) ?? { count: 0, size: 0 };
    g.count += 1;
    g.size += size;
    groups.set(key, g);
    if (iDet >= 0 && snap.nodes[i + iDet] === 2 && type === "native") {
      detached += 1;
      detachedByName.set(rawName, (detachedByName.get(rawName) ?? 0) + 1);
    }
  }
  return {
    totalMiB: total / MIB,
    nodes: snap.nodes.length / n,
    detached,
    detachedByName: Object.fromEntries([...detachedByName].sort((a, b) => b[1] - a[1]).slice(0, 15)),
    groups: Object.fromEntries(groups),
  };
}

function diffAggregates(a, b, top = 25) {
  const keys = new Set([...Object.keys(a.groups), ...Object.keys(b.groups)]);
  const rows = [];
  for (const k of keys) {
    const x = a.groups[k] ?? { count: 0, size: 0 };
    const y = b.groups[k] ?? { count: 0, size: 0 };
    rows.push({ key: k, dCount: y.count - x.count, dKiB: (y.size - x.size) / 1024, count: y.count });
  }
  rows.sort((p, q) => Math.abs(q.dKiB) - Math.abs(p.dKiB));
  return rows.slice(0, top);
}

// ---------------------------------------------------------------- statistics

/** Least-squares slope (y per minute) of the samples within the last `windowMin` minutes. */
function slope(samples, field, windowMin) {
  const tEnd = samples.at(-1)?.min ?? 0;
  const pts = samples.filter((s) => s.min >= tEnd - windowMin && typeof s[field] === "number");
  if (pts.length < 3) return null;
  const mx = pts.reduce((a, s) => a + s.min, 0) / pts.length;
  const my = pts.reduce((a, s) => a + s[field], 0) / pts.length;
  let sxy = 0;
  let sxx = 0;
  for (const s of pts) {
    sxy += (s.min - mx) * (s[field] - my);
    sxx += (s.min - mx) ** 2;
  }
  return sxx === 0 ? null : sxy / sxx;
}

// ---------------------------------------------------------------- page actions

const CLICK = (id) => `(() => { const e = document.getElementById(${JSON.stringify(id)}); if (!e) return false; e.click(); return true; })()`;
const NAV = (page) => `(() => { const b = document.querySelector('.nav-item[data-page="${page}"]'); if (!b) return false; b.click(); return true; })()`;
const PAGE_STATE = `(() => ({
  start: document.getElementById('start')?.dataset.state ?? null,
  startDisabled: document.getElementById('start')?.disabled ?? null,
  lines: document.querySelectorAll('#transcript > li').length,
  overlayLines: document.querySelectorAll('#lines .line').length,
  notices: document.getElementById('notices')?.children.length ?? null,
}))()`;
const LOCALES = ["en", "ja", "zh-TW"];

async function waitFor(what, fn, timeoutMs) {
  const end = Date.now() + timeoutMs;
  for (;;) {
    const v = await fn();
    if (v) return v;
    if (Date.now() > end) throw new Error(`timed out waiting for ${what}`);
    await sleep(500);
  }
}

// ---------------------------------------------------------------- main

async function main() {
  const p = await readParams(process.argv.slice(2));
  // Local time, like the Python scenarios' result folders.
  const d = new Date();
  const two = (v) => String(v).padStart(2, "0");
  const stamp = `${d.getFullYear()}${two(d.getMonth() + 1)}${two(d.getDate())}-${two(d.getHours())}${two(d.getMinutes())}${two(d.getSeconds())}`;
  const runDir = join(here, "results", `${stamp}-cdp-${p.label}`);
  mkdirSync(runDir, { recursive: true });
  const samplesFile = join(runDir, "samples.ndjson");
  console.log(`results: ${runDir}`);

  const engineCmd = [
    "uv", "run", "--no-project", "python", join(repoRoot, "app", "dev", "fake_engine.py"),
    "--partial-steps", String(p.partial_steps), "--partial-interval", String(p.partial_interval),
  ];
  const app = spawn(p.app, [], {
    cwd: runDir,
    stdio: ["ignore", "ignore", "ignore"],
    env: {
      ...process.env,
      WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS: `--remote-debugging-port=${p.port}`,
      // Keeps the soak's localStorage (cached interface language) out of the user's profile.
      WEBVIEW2_USER_DATA_FOLDER: join(runDir, "webview2"),
      KIKITORI_ENGINE_CMD: JSON.stringify(engineCmd),
      KIKITORI_ENGINE_CWD: repoRoot,
    },
  });
  const appExited = new Promise((ok) => app.on("exit", (code) => ok(code)));
  let mainPage = null;
  let overlay = null;
  const actions = [];
  const samples = [];
  try {
    const targets = await findTargets(p.port, 60_000);
    mainPage = await Cdp.connect(targets.main.webSocketDebuggerUrl);
    overlay = await Cdp.connect(targets.overlay.webSocketDebuggerUrl);
    for (const c of [mainPage, overlay]) await c.send("Performance.enable");
    if (p.webdriver_like) {
      // What an attached WebDriver session keeps enabled (gui_soak.py measured with one).
      for (const c of [mainPage, overlay]) {
        for (const domain of ["Page", "Runtime", "Log"]) await c.send(`${domain}.enable`);
      }
    }
    await waitFor("engine ready", async () => {
      const s = await mainPage.eval(PAGE_STATE);
      return s.start === "idle" && s.startDisabled === false;
    }, 120_000);
    const mainPid = await rendererOf(mainPage, app.pid);
    // The overlay window only renders once shown.
    await mainPage.eval(CLICK("overlay-visible"));
    await sleep(2000);
    const overlayPid = await rendererOf(overlay, app.pid);
    console.log(`app ${app.pid}, main renderer ${mainPid}, overlay renderer ${overlayPid}`);

    await mainPage.eval(CLICK("start"));
    await waitFor("session running", async () => (await mainPage.eval(PAGE_STATE)).start === "running", 120_000);
    const t0 = Date.now();
    const minutes = () => (Date.now() - t0) / 60_000;
    actions.push({ min: 0, action: "running" });

    async function sample(phase) {
      const procs = treeOf(await processTable(), app.pid);
      const byPid = (pid) => procs.find((r) => r.pid === pid)?.privateMiB ?? null;
      const rec = {
        min: Number(minutes().toFixed(3)),
        phase,
        mainPrivate: byPid(mainPid),
        overlayPrivate: byPid(overlayPid),
        webviewTotal: procs.filter((r) => r.pid !== app.pid).reduce((a, r) => a + r.privateMiB, 0),
        appPrivate: byPid(app.pid),
      };
      for (const [name, c] of [["main", mainPage], ["overlay", overlay]]) {
        if (p.gc) {
          await c.send("HeapProfiler.collectGarbage");
          await c.send("HeapProfiler.collectGarbage");
        }
        const heap = await c.send("Runtime.getHeapUsage");
        const dom = await c.send("Memory.getDOMCounters");
        const perf = Object.fromEntries((await c.send("Performance.getMetrics")).metrics.map((m) => [m.name, m.value]));
        rec[`${name}HeapMiB`] = heap.usedSize / MIB;
        rec[`${name}Nodes`] = dom.nodes;
        rec[`${name}Docs`] = dom.documents;
        rec[`${name}Listeners`] = dom.jsEventListeners;
        rec[`${name}Layouts`] = perf.LayoutCount;
        rec[`${name}StyleRecalcs`] = perf.RecalcStyleCount;
      }
      if (p.gc) {
        // Private bytes once the forced GC has returned garbage to the allocator.
        await sleep(1500);
        const after = treeOf(await processTable(), app.pid);
        rec.mainPrivateAfterGc = after.find((r) => r.pid === mainPid)?.privateMiB ?? null;
        rec.overlayPrivateAfterGc = after.find((r) => r.pid === overlayPid)?.privateMiB ?? null;
      }
      Object.assign(rec, await mainPage.eval(PAGE_STATE));
      samples.push(rec);
      appendFileSync(samplesFile, `${JSON.stringify(rec)}\n`);
      return rec;
    }

    const fmt = (v) => (typeof v === "number" ? v.toFixed(1) : String(v));
    const show = (r) =>
      console.log(
        `${fmt(r.min).padStart(6)} min  main ${fmt(r.mainPrivate)} MiB (after GC ${fmt(r.mainPrivateAfterGc)})  ` +
          `heap ${fmt(r.mainHeapMiB)}  nodes ${r.mainNodes}  listeners ${r.mainListeners}  lines ${r.lines}  ` +
          `overlay ${fmt(r.overlayPrivate)} MiB/${r.overlayNodes} nodes  webview ${fmt(r.webviewTotal)}`,
      );

    let snapA = null;
    const warmupMin = Math.min(2, p.minutes / 4);
    let nextToggle = 2;
    let nextOverlay = 3;
    let nextLocale = 5;
    let localeI = 0;
    let showTranslation = true;
    let nextSample = 0;
    while (minutes() < p.minutes) {
      const m = minutes();
      if (p.snapshots && snapA === null && m >= warmupMin) {
        snapA = await heapSnapshot(mainPage, join(runDir, "main-start.heapsnapshot"));
        actions.push({ min: minutes(), action: "snapshot-start", totalMiB: snapA.totalMiB });
      }
      if (p.toggles && m >= nextToggle) {
        nextToggle += 2;
        await mainPage.eval(CLICK("show-translation"));
        showTranslation = !showTranslation;
        actions.push({ min: minutes(), action: "show-translation", value: showTranslation });
      }
      if (p.toggles && m >= nextOverlay) {
        nextOverlay += 3;
        await mainPage.eval(CLICK("overlay-visible"));
        await sleep(5000);
        await mainPage.eval(CLICK("overlay-visible"));
        actions.push({ min: minutes(), action: "overlay hidden+shown" });
      }
      if (p.toggles && m >= nextLocale) {
        nextLocale += 5;
        const loc = LOCALES[localeI++ % LOCALES.length];
        await mainPage.eval(NAV("settings"));
        await mainPage.eval(
          `(() => { const s = document.getElementById('f-ui-locale'); s.value = ${JSON.stringify(loc)}; s.dispatchEvent(new Event('change')); return true; })()`,
        );
        await sleep(2000);
        await mainPage.eval(NAV("transcript"));
        actions.push({ min: minutes(), action: "locale", value: loc });
      }
      if (m >= nextSample) {
        nextSample = m + p.sample_secs / 60;
        show(await sample("running"));
      }
      await sleep(1000);
    }
    const tStop = minutes();
    if (p.snapshots) {
      const snapB = await heapSnapshot(mainPage, join(runDir, "main-end.heapsnapshot"));
      if (snapA) {
        const diff = diffAggregates(snapA, snapB);
        writeFileSync(join(runDir, "snapshot-diff.json"), JSON.stringify({ start: snapA, end: snapB, diff }, null, 1));
        console.log(`heap snapshot ${snapA.totalMiB.toFixed(2)} → ${snapB.totalMiB.toFixed(2)} MiB, detached ${snapA.detached} → ${snapB.detached}`);
        for (const d of diff.slice(0, 15)) console.log(`  ${d.dKiB.toFixed(1).padStart(9)} KiB ${String(d.dCount).padStart(7)}  ${d.key}`);
      }
    }
    const lastRunning = samples.filter((s) => s.phase === "running");
    if (!showTranslation) await mainPage.eval(CLICK("show-translation"));
    await mainPage.eval(CLICK("start"));
    await waitFor("session idle", async () => (await mainPage.eval(PAGE_STATE)).start === "idle", 60_000);
    actions.push({ min: minutes(), action: "stopped" });
    await sleep(20_000);
    const after = await sample("after-stop");
    show(after);

    // Plateau verdict (README「CDP soak」): 1 MiB/min ≈ 480 MiB over an 8-hour stream.
    const fields = ["mainPrivate", "mainPrivateAfterGc", "mainHeapMiB", "mainNodes", "mainListeners", "overlayPrivate", "overlayNodes", "webviewTotal"];
    const slopes = Object.fromEntries(fields.map((f) => [f, slope(lastRunning, f, p.window_min)]));
    const limits = { mainPrivate: 1, mainPrivateAfterGc: 1, mainHeapMiB: 0.1, overlayPrivate: 1, webviewTotal: 2 };
    const verdict = Object.fromEntries(
      Object.entries(limits).map(([f, lim]) => [f, slopes[f] === null ? "n/a" : slopes[f] <= lim ? "PASS" : "FAIL"]),
    );
    const first = lastRunning[0];
    const last = lastRunning.at(-1);
    const summary = { params: p, tStop, mainPid, overlayPid, first, last, after, slopesPerMin: slopes, limits, verdict, actions };
    writeFileSync(join(runDir, "summary.json"), JSON.stringify(summary, null, 1));
    console.log(`\nslopes over the last ${p.window_min} min (per minute):`);
    for (const f of fields) console.log(`  ${f.padEnd(20)} ${slopes[f] === null ? "n/a" : slopes[f].toFixed(3)}  ${verdict[f] ?? ""}`);
    console.log(`main renderer ${fmt(first?.mainPrivate)} → ${fmt(last?.mainPrivate)} MiB, after stop ${fmt(after.mainPrivate)} MiB`);
  } finally {
    mainPage?.close();
    overlay?.close();
    if (app.exitCode === null) {
      // The whole tree: the engine and the WebView2 processes are the app's children.
      await killTree(app.pid);
      await Promise.race([appExited, sleep(10_000)]);
    }
    writeFileSync(join(runDir, "actions.json"), JSON.stringify(actions, null, 1));
    // The soak's own WebView2 profile (~70 MB of caches); the results stay.
    try {
      rmSync(join(runDir, "webview2"), { recursive: true, force: true, maxRetries: 5, retryDelay: 1000 });
    } catch (e) {
      console.error(`cannot remove the soak's WebView2 profile: ${e.message}`);
    }
  }
}

// The helpers are also imported by one-off experiments; only run when executed directly.
if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) {
  main().then(
    () => process.exit(0),
    (e) => {
      console.error(e);
      process.exit(1);
    },
  );
}

export { Cdp, CLICK, NAV, PAGE_STATE, findTargets, heapSnapshot, killTree, processTable, rendererOf, treeOf, waitFor };
