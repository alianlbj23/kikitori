// Release-build GUI acceptance for furigana + real engine (B) and the real-engine CDP memory soak (D).
//
// Role: temporary experiment, not product code (tasks/acceptance-report-v0.2.0.md section 7).
// Starts the release kikitori-app.exe with its bundled engine (no KIKITORI_ENGINE_CMD), WebView2
// remote debugging, an isolated LOCALAPPDATA (the user's ASR model files hard-linked in, so the
// engine reads them but never writes the user's cache) and its own KIKITORI_CONFIG_DIR. Japanese
// audio (FLEURS, CC-BY 4.0, data/final_ja.wav) is looped to the Steam Streaming Speakers virtual
// output by `final_followup.py --batch play`, and the engine captures its loopback.
//
// Scenarios:
//   furigana  dictionary not downloaded → turn furigana on in Settings (downloads it) → models page →
//             start → <ruby> in the transcript and the overlay (readings collected for a manual check)
//             → turn it off while running → later lines carry no readings. Screenshots in the results.
//   soak      furigana on, N minutes running with real subtitles; toggles 顯示翻譯 every 2 min,
//             furigana every 3 min, interface language every 5 min; main/overlay renderer private
//             bytes (same probe as memory_soak/cdp_soak.mjs) and the engine's private bytes per sample.
//
// Usage (Node 24, repository root):
//   node experiments/e2e_acceptance/final_gui_cdp.mjs                     interactive menu
//   node experiments/e2e_acceptance/final_gui_cdp.mjs --batch furigana app=<kikitori-app.exe>
//   node experiments/e2e_acceptance/final_gui_cdp.mjs --batch soak app=<exe> minutes=15

import { spawn } from "node:child_process";
import { appendFileSync, existsSync, linkSync, lstatSync, mkdirSync, readdirSync, rmSync, statSync, writeFileSync } from "node:fs";
import { basename, dirname, join, resolve } from "node:path";
import process from "node:process";
import { createInterface } from "node:readline/promises";
import { setTimeout as sleep } from "node:timers/promises";
import { fileURLToPath } from "node:url";

import { Cdp, NAV, findTargets, killTree, processTable, rendererOf, treeOf } from "../memory_soak/cdp_soak.mjs";

const here = dirname(fileURLToPath(import.meta.url));
const repoRoot = resolve(here, "..", "..");
const MIB = 1024 * 1024;
const USER_KIKITORI = join(process.env.LOCALAPPDATA, "kikitori");
// Hard links only work within one volume, so the isolated LOCALAPPDATA lives next to the user's cache.
const LAD_ROOT = process.env.KIKITORI_FINAL_LAD_ROOT ?? join(process.env.TEMP ?? process.env.LOCALAPPDATA, "kikitori-final-lad");
const MODELS = ["large-v3-turbo", "large-v3"];
const LISTENING = "正在聆聽";
const TARGET_OUTPUT = "Steam Streaming Speakers";

/** Clicks the first element matching a CSS selector (cdp_soak's CLICK takes an id). */
const CLICK = (selector) =>
  `(() => { const e = document.querySelector(${JSON.stringify(selector)}); if (!e) return false; e.click(); return true; })()`;

// ---------------------------------------------------------------- parameters

const SCENARIOS = {
  furigana: { help: "B. furigana download + real engine + Japanese audio", params: {} },
  soak: {
    help: "D. real-engine CDP soak with furigana on",
    params: { minutes: "15", sample_secs: "15", window_min: "10", gc: "false" },
  },
};
const COMMON = {
  app: process.env.KIKITORI_E2E_APP ?? "",
  port: "9233",
};

async function readArgs(argv) {
  if (argv[0] === "--batch") {
    const name = argv[1];
    if (!SCENARIOS[name]) throw new Error(`unknown scenario ${name}`);
    const given = Object.fromEntries(argv.slice(2).map((a) => [a.slice(0, a.indexOf("=")), a.slice(a.indexOf("=") + 1)]));
    const allowed = { ...COMMON, ...SCENARIOS[name].params };
    for (const k of Object.keys(given)) if (!(k in allowed)) throw new Error(`unknown parameter ${k}`);
    return { name, p: { ...allowed, ...given } };
  }
  const rl = createInterface({ input: process.stdin, output: process.stdout });
  try {
    const names = Object.keys(SCENARIOS);
    names.forEach((n, i) => console.log(`  ${i + 1}) ${n}: ${SCENARIOS[n].help}`));
    let name;
    for (;;) {
      const a = (await rl.question("scenario number: ")).trim();
      name = names[Number(a) - 1];
      if (name) break;
      console.log("  invalid choice");
    }
    const p = {};
    for (const [k, def] of Object.entries({ ...COMMON, ...SCENARIOS[name].params })) {
      const a = (await rl.question(`${k} [${def}]: `)).trim();
      p[k] = a === "" ? def : a;
    }
    return { name, p };
  } finally {
    rl.close();
  }
}

// ---------------------------------------------------------------- isolated cache

/** Hard-links every regular file under `src` into `dst` (same volume). */
function linkTree(src, dst) {
  const st = lstatSync(src);
  if (st.isSymbolicLink()) throw new Error(`refusing to link a symlink: ${src}`);
  if (st.isFile()) {
    mkdirSync(dirname(dst), { recursive: true });
    linkSync(src, dst);
    return 1;
  }
  let n = 0;
  for (const name of readdirSync(src)) n += linkTree(join(src, name), join(dst, name));
  return n;
}

function isolatedCache(lad, { withFurigana }) {
  const dst = join(lad, "kikitori");
  const linked = {};
  for (const m of MODELS) linked[`models/${m}`] = linkTree(join(USER_KIKITORI, "models", m), join(dst, "models", m));
  linked["gpu-runtime"] = linkTree(join(USER_KIKITORI, "gpu-runtime"), join(dst, "gpu-runtime"));
  if (withFurigana) linked.furigana = linkTree(join(USER_KIKITORI, "furigana"), join(dst, "furigana"));
  return linked;
}

/** Size + mtime of every file of the user's cache (models, GPU runtime, dictionary), compared before/after. */
function fingerprint(root = USER_KIKITORI) {
  const out = {};
  const walk = (dir) => {
    for (const name of readdirSync(dir)) {
      const p = join(dir, name);
      const st = lstatSync(p);
      if (st.isDirectory()) walk(p);
      else out[p.slice(root.length)] = `${st.size}:${st.mtimeMs}`;
    }
  };
  for (const sub of ["models", "gpu-runtime", "furigana"]) if (existsSync(join(root, sub))) walk(join(root, sub));
  return JSON.stringify(out);
}

function settings({ furigana }) {
  return {
    schema_version: 3,
    mode: "media",
    source_language: "ja",
    target_language: "zh-TW",
    asr: {
      fast_model: "large-v3-turbo",
      final_model: "large-v3",
      device: "cuda",
      fast_compute_type: "int8_float16",
      final_compute_type: "int8_float16",
    },
    translation: {
      enabled: true,
      base_url: "http://localhost:11434/v1",
      fast_model: "qwen3.5:9b",
      final_model: "qwen3.5:9b",
    },
    ui: { locale: "zh-TW" },
    furigana: { enabled: furigana },
  };
}

// ---------------------------------------------------------------- app + player

function launchApp(app, runDir, port, lad, configDir) {
  const env = Object.fromEntries(Object.entries(process.env).filter(([k]) => !k.startsWith("KIKITORI_ENGINE")));
  Object.assign(env, {
    LOCALAPPDATA: lad,
    KIKITORI_CONFIG_DIR: configDir,
    WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS: `--remote-debugging-port=${port}`,
    WEBVIEW2_USER_DATA_FOLDER: join(runDir, "webview2"),
  });
  const child = spawn(app, [], { cwd: runDir, stdio: ["ignore", "ignore", "ignore"], env });
  const exited = new Promise((ok) => child.on("exit", ok));
  return { child, exited };
}

/** Loops data/final_ja.wav to the virtual speakers (final_followup.py play) until stop(). */
function startPlayer(runDir, seconds) {
  const log = join(runDir, "player.log");
  const child = spawn(
    "uv",
    ["run", "python", "-X", "utf8", join(here, "final_followup.py"), "--batch", "play", `seconds=${Math.ceil(seconds)}`],
    { cwd: repoRoot, stdio: ["pipe", "pipe", "pipe"] },
  );
  child.stdout.on("data", (d) => appendFileSync(log, d));
  child.stderr.on("data", (d) => appendFileSync(log, d));
  const exited = new Promise((ok) => child.on("exit", ok));
  return {
    async stop() {
      child.stdin.end();
      await Promise.race([exited, sleep(10_000)]);
      if (child.exitCode === null) await killTree(child.pid);
    },
  };
}

async function waitFor(what, fn, timeoutMs = 60_000, stepMs = 300) {
  const end = Date.now() + timeoutMs;
  for (;;) {
    const v = await fn();
    if (v) return v;
    if (Date.now() > end) throw new Error(`timed out waiting for ${what}`);
    await sleep(stepMs);
  }
}

const STATE = `(() => ({
  start: document.getElementById('start')?.dataset.state ?? null,
  startDisabled: document.getElementById('start')?.disabled ?? null,
  label: document.getElementById('status-label')?.textContent ?? '',
  furigana: document.getElementById('f-furigana')?.checked ?? null,
  furiganaDisabled: document.getElementById('f-furigana')?.disabled ?? null,
  furiganaStatus: document.getElementById('furigana-status')?.hidden ? '' : (document.getElementById('furigana-status')?.textContent ?? ''),
  showTranslation: document.getElementById('show-translation')?.checked ?? null,
  lines: document.querySelectorAll('#transcript > li').length,
  finals: document.querySelectorAll('#transcript > li:not(.is-partial)').length,
  rubies: document.querySelectorAll('#transcript ruby').length,
  notices: [...document.querySelectorAll('#notices .notice')].map((n) => n.textContent.trim()).slice(0, 5),
}))()`;

/** Every final transcript line: original text, its <ruby> words and readings, translation. */
const FINAL_LINES = `(() => [...document.querySelectorAll('#transcript > li:not(.is-partial)')].map((li) => {
  const orig = li.querySelector('.utt-orig');
  const rubies = [...(orig?.querySelectorAll('ruby') ?? [])].map((r) => ({
    base: [...r.childNodes].filter((n) => n.nodeType === 3).map((n) => n.data).join(''),
    rt: r.querySelector('rt')?.textContent ?? '',
  }));
  const plain = orig ? [...orig.childNodes].map(function text(n) {
    if (n.nodeType === 3) return n.data;
    if (n.nodeName === 'RT') return '';
    return [...n.childNodes].map(text).join('');
  }).join('') : '';
  return { lang: orig?.lang ?? null, original: plain, rubies, translation: li.querySelector('.utt-tr')?.textContent ?? '' };
}))()`;

async function selectLoopback(page) {
  await page.eval(CLICK('.mode[data-mode="media"]'));
  return waitFor(
    "virtual loopback option",
    () =>
      page.eval(`(() => {
        const s = document.getElementById('device');
        const o = [...s.options].find((x) => x.textContent.includes(${JSON.stringify(TARGET_OUTPUT)}));
        if (!o) return null;
        if (s.value !== o.value) { s.value = o.value; s.dispatchEvent(new Event('change', { bubbles: true })); }
        return o.textContent;
      })()`),
    30_000,
  );
}

function stamp() {
  const d = new Date();
  const two = (v) => String(v).padStart(2, "0");
  return `${d.getFullYear()}${two(d.getMonth() + 1)}${two(d.getDate())}-${two(d.getHours())}${two(d.getMinutes())}${two(d.getSeconds())}`;
}

/** kikitori-app / kikitori-engine (incl. --prequantize) / WebDriver processes still running. */
function leftovers() {
  return new Promise((ok) => {
    const ps = spawn("tasklist.exe", ["/FO", "CSV", "/NH"], { stdio: ["ignore", "pipe", "ignore"] });
    let out = "";
    ps.stdout.on("data", (d) => (out += d));
    ps.on("exit", () => {
      const names = ["kikitori-app.exe", "kikitori-engine.exe", "msedgedriver.exe", "tauri-driver.exe"];
      ok(out.split(/\r?\n/).filter((l) => names.some((n) => l.toLowerCase().startsWith(`"${n}"`))));
    });
  });
}

async function screenshot(cdp, file) {
  const r = await cdp.send("Page.captureScreenshot", { format: "png" });
  writeFileSync(file, Buffer.from(r.data, "base64"));
  console.log(`screenshot ${file}`);
}

// ---------------------------------------------------------------- B. furigana

async function scenarioFurigana(p, runDir) {
  const shots = join(runDir, "shots");
  mkdirSync(shots, { recursive: true });
  const lad = join(LAD_ROOT, basename(runDir));
  const configDir = join(runDir, "config");
  mkdirSync(configDir, { recursive: true });
  writeFileSync(join(configDir, "settings.json"), JSON.stringify(settings({ furigana: false }), null, 2));
  const before = fingerprint();
  const r = { linked: isolatedCache(lad, { withFurigana: false }), checks: {} };
  const check = (name, ok, detail) => {
    r.checks[name] = { passed: !!ok, detail };
    console.log(`  [${ok ? "PASS" : "FAIL"}] ${name}: ${JSON.stringify(detail).slice(0, 400)}`);
  };
  const { child, exited } = launchApp(p.app, runDir, Number(p.port), lad, configDir);
  let page = null;
  let overlay = null;
  let player = null;
  try {
    const targets = await findTargets(Number(p.port), 60_000);
    page = await Cdp.connect(targets.main.webSocketDebuggerUrl);
    overlay = await Cdp.connect(targets.overlay.webSocketDebuggerUrl);
    await waitFor("engine ready", async () => {
      const s = await page.eval(STATE);
      return s.start === "idle" && s.startDisabled === false && s.furiganaDisabled === false ? s : null;
    }, 120_000);
    // Models page before: not downloaded.
    await page.eval(NAV("models"));
    await sleep(2500);
    r.modelsBefore = await page.eval(`document.getElementById('furigana-dictionary')?.innerText ?? ''`);
    await page.eval(`document.getElementById('furigana-dictionary').scrollIntoView({ block: 'center' })`);
    await screenshot(page, join(shots, "b1-models-before.png"));
    check("dictionary not present in the isolated cache before", !existsSync(join(lad, "kikitori", "furigana")), r.modelsBefore);

    // Settings: turn on → download progress → on.
    await page.eval(NAV("settings"));
    await page.eval(`document.getElementById('f-furigana').scrollIntoView({ block: 'center' })`);
    const t0 = Date.now();
    await page.eval(CLICK("#f-furigana"));
    const progress = [];
    let shot = false;
    const done = await waitFor("furigana on after download", async () => {
      const s = await page.eval(STATE);
      if (s.furiganaStatus && progress.at(-1) !== s.furiganaStatus) progress.push(s.furiganaStatus);
      if (s.furiganaStatus && !shot && /\d+%/.test(s.furiganaStatus)) {
        shot = true;
        await screenshot(page, join(shots, "b2-download-progress.png"));
      }
      return s.furigana === true && s.furiganaDisabled === false && !s.furiganaStatus ? s : null;
    }, 300_000, 200);
    r.downloadSecs = (Date.now() - t0) / 1000;
    r.progress = progress.length > 12 ? [...progress.slice(0, 6), "…", ...progress.slice(-6)] : progress;
    const dictDir = join(lad, "kikitori", "furigana");
    const files = existsSync(dictDir) ? readdirSync(dictDir, { recursive: true }) : [];
    check("turning furigana on downloads the dictionary, shows progress, then it is on", progress.length > 0 && files.some((f) => String(f).endsWith("system_core.dic")), {
      secs: r.downloadSecs,
      progress: r.progress,
      files,
      notices: done.notices,
    });
    await screenshot(page, join(shots, "b3-settings-on.png"));
    await page.eval(NAV("models"));
    await sleep(2500);
    r.modelsAfter = await page.eval(`document.getElementById('furigana-dictionary')?.innerText ?? ''`);
    await page.eval(`document.getElementById('furigana-dictionary').scrollIntoView({ block: 'center' })`);
    await screenshot(page, join(shots, "b4-models-after.png"));
    check("models page shows the downloaded dictionary", r.modelsAfter !== r.modelsBefore && r.modelsAfter.length > 0, r.modelsAfter);

    // Start a session with the Japanese audio.
    await page.eval(NAV("transcript"));
    r.device = await selectLoopback(page);
    await page.eval(CLICK("#overlay-visible"));
    const tStart = Date.now();
    await page.eval(CLICK("#start"));
    await waitFor("listening", async () => (await page.eval(STATE)).label === LISTENING, 180_000);
    r.startToListeningSecs = (Date.now() - tStart) / 1000;
    player = startPlayer(runDir, 400);
    // A live line with readings.
    const live = await waitFor("a live line with <ruby>", () =>
      page.eval(`(() => {
        const li = document.querySelector('#transcript > li.is-partial');
        return li && li.querySelector('ruby') ? li.querySelector('.utt-orig').innerHTML : null;
      })()`), 90_000, 150);
    r.liveExample = live;
    await screenshot(page, join(shots, "b5-live.png"));
    // Ten final Japanese lines with readings.
    const finals = await waitFor("10 final Japanese lines", async () => {
      const lines = await page.eval(FINAL_LINES);
      return lines.filter((l) => l.lang === "ja").length >= 10 ? lines : null;
    }, 300_000, 1000);
    r.finalsOn = finals;
    await screenshot(page, join(shots, "b6-transcript-on.png"));
    const ovRubies = await overlay.eval(`document.querySelectorAll('#lines ruby').length`);
    r.overlayOn = await overlay.eval(`[...document.querySelectorAll('#lines .line .orig')].map((e) => e.innerHTML)`);
    await screenshot(overlay, join(shots, "b7-overlay-on.png"));
    const ja = finals.filter((l) => l.lang === "ja");
    check("transcript: Japanese final lines carry <ruby>; overlay too", ja.filter((l) => l.rubies.length > 0).length >= 8 && ovRubies > 0, {
      jaFinals: ja.length,
      withRuby: ja.filter((l) => l.rubies.length > 0).length,
      overlayRubies: ovRubies,
    });

    // Off while running.
    await page.eval(NAV("settings"));
    await page.eval(CLICK("#f-furigana"));
    await waitFor("furigana off", async () => (await page.eval(STATE)).furigana === false, 30_000);
    const tOff = Date.now();
    await page.eval(NAV("transcript"));
    const countAtOff = (await page.eval(FINAL_LINES)).length;
    // Lines that started after the switch: skip the one in flight, then need three more.
    const after = await waitFor("3 new final lines after turning furigana off", async () => {
      const lines = await page.eval(FINAL_LINES);
      return lines.length >= countAtOff + 4 ? lines.slice(countAtOff + 1) : null;
    }, 240_000, 1000);
    r.finalsOff = after;
    r.offSecs = (Date.now() - tOff) / 1000;
    await screenshot(page, join(shots, "b8-transcript-off.png"));
    const ovAfter = await overlay.eval(`[...document.querySelectorAll('#lines .line')].slice(-1).map((l) => l.querySelector('ruby') !== null)`);
    await screenshot(overlay, join(shots, "b9-overlay-off.png"));
    check("after turning furigana off mid-session, new lines have no readings (transcript and overlay)", after.every((l) => l.rubies.length === 0) && ovAfter.every((x) => !x), {
      lines: after.map((l) => ({ lang: l.lang, original: l.original, rubies: l.rubies.length })),
      overlayLastHasRuby: ovAfter,
    });
    // Earlier lines keep their readings (UI keeps what was received).
    const earlier = (await page.eval(FINAL_LINES)).slice(0, countAtOff).filter((l) => l.lang === "ja");
    r.earlierKeepRuby = earlier.filter((l) => l.rubies.length > 0).length;
    await player.stop();
    player = null;
    await page.eval(CLICK("#start"));
    await waitFor("idle", async () => (await page.eval(STATE)).start === "idle", 30_000);
  } finally {
    if (player) await player.stop();
    page?.close();
    overlay?.close();
    if (child.exitCode === null) {
      await killTree(child.pid);
      await Promise.race([exited, sleep(10_000)]);
    }
    rmSync(join(runDir, "webview2"), { recursive: true, force: true, maxRetries: 5, retryDelay: 1000 });
    await sleep(2000);
    r.leftovers = await leftovers();
    rmSync(lad, { recursive: true, force: true, maxRetries: 5, retryDelay: 1000 });
    r.userCacheUnchanged = fingerprint() === before;
    check("no leftover processes; user cache unchanged", r.leftovers.length === 0 && r.userCacheUnchanged, { leftovers: r.leftovers, unchanged: r.userCacheUnchanged });
    writeFileSync(join(runDir, "result.json"), JSON.stringify(r, null, 1));
  }
  return Object.values(r.checks).every((c) => c.passed);
}

// ---------------------------------------------------------------- D. soak

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

async function scenarioSoak(p, runDir) {
  const minutes = Number(p.minutes);
  const sampleSecs = Number(p.sample_secs);
  const windowMin = Number(p.window_min);
  const gc = p.gc === "true";
  const lad = join(LAD_ROOT, basename(runDir));
  const configDir = join(runDir, "config");
  mkdirSync(configDir, { recursive: true });
  writeFileSync(join(configDir, "settings.json"), JSON.stringify(settings({ furigana: true }), null, 2));
  const before = fingerprint();
  const linked = isolatedCache(lad, { withFurigana: true });
  const samplesFile = join(runDir, "samples.ndjson");
  const { child, exited } = launchApp(p.app, runDir, Number(p.port), lad, configDir);
  let page = null;
  let overlay = null;
  let player = null;
  const actions = [];
  const samples = [];
  const summary = { params: p, linked };
  try {
    const targets = await findTargets(Number(p.port), 60_000);
    page = await Cdp.connect(targets.main.webSocketDebuggerUrl);
    overlay = await Cdp.connect(targets.overlay.webSocketDebuggerUrl);
    for (const c of [page, overlay]) await c.send("Performance.enable");
    await waitFor("engine ready", async () => {
      const s = await page.eval(STATE);
      return s.start === "idle" && s.startDisabled === false && s.furigana === true ? s : null;
    }, 120_000);
    const mainPid = await rendererOf(page, child.pid);
    await page.eval(CLICK("#overlay-visible"));
    await sleep(2000);
    const overlayPid = await rendererOf(overlay, child.pid);
    summary.device = await selectLoopback(page);
    await page.eval(CLICK("#start"));
    await waitFor("listening", async () => (await page.eval(STATE)).label === LISTENING, 180_000);
    player = startPlayer(runDir, minutes * 60 + 120);
    const t0 = Date.now();
    const mins = () => (Date.now() - t0) / 60_000;
    console.log(`app ${child.pid}, main renderer ${mainPid}, overlay renderer ${overlayPid}`);

    async function sample(phase) {
      const tree = treeOf(await processTable(), child.pid);
      const byPid = (pid) => tree.find((x) => x.pid === pid)?.privateMiB ?? null;
      const engine = tree.find((x) => /kikitori-engine\.exe/i.test(x.cmd) && !/--prequantize/.test(x.cmd));
      const rec = {
        min: Number(mins().toFixed(3)),
        phase,
        mainPrivate: byPid(mainPid),
        overlayPrivate: byPid(overlayPid),
        webviewTotal: tree.filter((x) => /msedgewebview2/i.test(x.cmd)).reduce((a, x) => a + x.privateMiB, 0),
        appPrivate: byPid(child.pid),
        enginePrivate: engine ? engine.privateMiB : null,
        prequantizeChildren: tree.filter((x) => /--prequantize/.test(x.cmd)).length,
      };
      for (const [name, c] of [["main", page], ["overlay", overlay]]) {
        if (gc) {
          await c.send("HeapProfiler.collectGarbage");
          await c.send("HeapProfiler.collectGarbage");
        }
        const heap = await c.send("Runtime.getHeapUsage");
        const dom = await c.send("Memory.getDOMCounters");
        rec[`${name}HeapMiB`] = heap.usedSize / MIB;
        rec[`${name}Nodes`] = dom.nodes;
        rec[`${name}Listeners`] = dom.jsEventListeners;
      }
      Object.assign(rec, await page.eval(`(() => ({
        lines: document.querySelectorAll('#transcript > li').length,
        rubies: document.querySelectorAll('#transcript ruby').length,
        overlayLines: 0,
        label: document.getElementById('status-label')?.textContent ?? '',
      }))()`));
      rec.overlayRubies = await overlay.eval(`document.querySelectorAll('#lines ruby').length`);
      samples.push(rec);
      appendFileSync(samplesFile, `${JSON.stringify(rec)}\n`);
      const f = (v) => (typeof v === "number" ? v.toFixed(1) : String(v));
      console.log(`${f(rec.min).padStart(6)} min  main ${f(rec.mainPrivate)}  overlay ${f(rec.overlayPrivate)}  webview ${f(rec.webviewTotal)}  engine ${f(rec.enginePrivate)}  lines ${rec.lines} ruby ${rec.rubies}  ${rec.label}`);
      return rec;
    }

    let nextToggle = 2;
    let nextFurigana = 3;
    let nextLocale = 5;
    let localeI = 0;
    let showTranslation = true;
    let furiganaOn = true;
    let nextSample = 0;
    const LOCALES = ["en", "ja", "zh-TW"];
    while (mins() < minutes) {
      const m = mins();
      if (m >= nextToggle) {
        nextToggle += 2;
        await page.eval(CLICK("#show-translation"));
        showTranslation = !showTranslation;
        actions.push({ min: mins(), action: "show-translation", value: showTranslation });
      }
      if (m >= nextFurigana) {
        nextFurigana += 3;
        await page.eval(NAV("settings"));
        await page.eval(CLICK("#f-furigana"));
        furiganaOn = !furiganaOn;
        const want = furiganaOn;
        await waitFor("furigana toggled", async () => (await page.eval(STATE)).furigana === want, 20_000);
        await sleep(1000);
        await page.eval(NAV("transcript"));
        actions.push({ min: mins(), action: "furigana", value: furiganaOn });
      }
      if (m >= nextLocale) {
        nextLocale += 5;
        const loc = LOCALES[localeI++ % LOCALES.length];
        await page.eval(NAV("settings"));
        await page.eval(`(() => { const s = document.getElementById('f-ui-locale'); s.value = ${JSON.stringify(loc)}; s.dispatchEvent(new Event('change')); return true; })()`);
        await sleep(2000);
        await page.eval(NAV("transcript"));
        actions.push({ min: mins(), action: "locale", value: loc });
      }
      if (m >= nextSample) {
        nextSample = m + sampleSecs / 60;
        await sample("running");
      }
      await sleep(1000);
    }
    const running = samples.filter((s) => s.phase === "running");
    // Back to the starting state, then stop.
    if (!showTranslation) await page.eval(CLICK("#show-translation"));
    await player.stop();
    player = null;
    await page.eval(CLICK("#start"));
    await waitFor("idle", async () => (await page.eval(STATE)).start === "idle", 60_000);
    actions.push({ min: mins(), action: "stopped" });
    await sleep(20_000);
    const after = await sample("after-stop");
    const fields = ["mainPrivate", "overlayPrivate", "webviewTotal", "appPrivate", "enginePrivate", "mainHeapMiB", "mainNodes", "mainListeners"];
    const slopes = Object.fromEntries(fields.map((f) => [f, slope(running, f, windowMin)]));
    const limits = { mainPrivate: 1, overlayPrivate: 1, webviewTotal: 2, mainHeapMiB: 0.1 };
    const verdict = Object.fromEntries(Object.entries(limits).map(([f, lim]) => [f, slopes[f] === null ? "n/a" : slopes[f] <= lim ? "PASS" : "FAIL"]));
    Object.assign(summary, { mainPid, overlayPid, first: running[0], last: running.at(-1), after, slopesPerMin: slopes, limits, verdict, actions });
    console.log(`slopes over the last ${windowMin} min:`, JSON.stringify(slopes), JSON.stringify(verdict));
  } finally {
    if (player) await player.stop();
    page?.close();
    overlay?.close();
    if (child.exitCode === null) {
      await killTree(child.pid);
      await Promise.race([exited, sleep(10_000)]);
    }
    rmSync(join(runDir, "webview2"), { recursive: true, force: true, maxRetries: 5, retryDelay: 1000 });
    await sleep(2000);
    summary.leftovers = await leftovers();
    rmSync(lad, { recursive: true, force: true, maxRetries: 5, retryDelay: 1000 });
    summary.userCacheUnchanged = fingerprint() === before;
    writeFileSync(join(runDir, "summary.json"), JSON.stringify(summary, null, 1));
  }
  return summary.verdict?.mainPrivate === "PASS" && summary.leftovers.length === 0 && summary.userCacheUnchanged;
}

// ---------------------------------------------------------------- main

async function main() {
  const { name, p } = await readArgs(process.argv.slice(2));
  if (!p.app || !existsSync(p.app) || !statSync(p.app).isFile()) throw new Error(`app=${p.app} is not a file (set KIKITORI_E2E_APP or app=)`);
  const runDir = join(here, "results", `${stamp()}-final-gui-${name}`);
  mkdirSync(runDir, { recursive: true });
  console.log(`results: ${runDir}`);
  const ok = name === "furigana" ? await scenarioFurigana(p, runDir) : await scenarioSoak(p, runDir);
  console.log(`${name}: ${ok ? "PASS" : "FAIL"}`);
  return ok ? 0 : 1;
}

main().then(
  (code) => process.exit(code),
  (e) => {
    console.error(e);
    process.exit(1);
  },
);
