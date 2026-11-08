// Allocator breakdown of the WebView2 processes (Chromium memory-infra), standard library only.
//
// Role: answers "which allocator holds the renderer's private bytes" during a cdp_soak.mjs
// run (or any app started with --remote-debugging-port). It asks the browser target for a
// detailed memory dump through the Tracing domain and prints, per process, the size of the
// top-level allocators: v8, blink_gc (Oilpan), partition_alloc partitions (buffer,
// fast_malloc, layout, array_buffer), malloc, cc, skia, gpu, font_caches, discardable...
//
// Usage (Node 24): node experiments/memory_soak/cdp_memdump.mjs [port] [pid ...]
//   port defaults to 9222; with pids, only those processes are printed.
// It only reads; the trace is not written anywhere.

import process from "node:process";

const MIB = 1024 * 1024;
const port = Number(process.argv[2] ?? 9222);
const onlyPids = new Set(process.argv.slice(3).map(Number));
if (!Number.isInteger(port) || port < 1024 || port > 65535) {
  console.error(`invalid port ${process.argv[2]}`);
  process.exit(2);
}

async function main() {
  const version = await (await fetch(`http://127.0.0.1:${port}/json/version`, { signal: AbortSignal.timeout(3000) })).json();
  const ws = new WebSocket(version.webSocketDebuggerUrl);
  await new Promise((ok, fail) => {
    const timer = setTimeout(() => {
      ws.close();
      fail(new Error("connecting to the browser target timed out"));
    }, 10_000);
    ws.addEventListener("open", () => (clearTimeout(timer), ok()), { once: true });
    ws.addEventListener("error", () => (clearTimeout(timer), fail(new Error("cannot connect to the browser target"))), { once: true });
  });
  let nextId = 1;
  const pending = new Map();
  const events = [];
  let complete = null;
  const done = new Promise((ok) => (complete = ok));
  ws.addEventListener("message", (ev) => {
    const msg = JSON.parse(String(ev.data));
    if (msg.id !== undefined) {
      const p = pending.get(msg.id);
      pending.delete(msg.id);
      if (msg.error) p?.fail(new Error(msg.error.message));
      else p?.ok(msg.result);
    } else if (msg.method === "Tracing.dataCollected") {
      events.push(...msg.params.value);
    } else if (msg.method === "Tracing.tracingComplete") {
      complete();
    }
  });
  const send = (method, params = {}) =>
    new Promise((ok, fail) => {
      const id = nextId++;
      const timer = setTimeout(() => fail(new Error(`${method} timed out`)), 60_000);
      pending.set(id, {
        ok: (v) => (clearTimeout(timer), ok(v)),
        fail: (e) => (clearTimeout(timer), fail(e)),
      });
      ws.send(JSON.stringify({ id, method, params }));
    });

  await send("Tracing.start", {
    transferMode: "ReportEvents",
    traceConfig: {
      includedCategories: ["disabled-by-default-memory-infra"],
      excludedCategories: ["*"],
      memoryDumpConfig: { triggers: [] },
    },
  });
  const dump = await send("Tracing.requestMemoryDump", { deterministic: true, levelOfDetail: "detailed" });
  await send("Tracing.end");
  await Promise.race([done, new Promise((ok) => setTimeout(ok, 30_000))]);
  ws.close();
  if (!dump.success) throw new Error("memory dump failed");

  // Each process sends its totals and its allocators as two separate events.
  const totals = new Map();
  for (const e of events) {
    const footprint = e.ph === "v" ? e.args?.dumps?.process_totals?.private_footprint_bytes : undefined;
    if (footprint) totals.set(e.pid, parseInt(footprint, 16) / MIB);
  }
  const byPid = new Map();
  for (const e of events) {
    if (e.ph !== "v" || !e.args?.dumps?.allocators) continue;
    if (onlyPids.size && !onlyPids.has(e.pid)) continue;
    const top = {};
    for (const [name, a] of Object.entries(e.args.dumps.allocators)) {
      const size = a.attrs?.size;
      if (!size) continue;
      const bytes = parseInt(size.value, 16);
      // Top level plus one level below the big ones.
      const depth = name.split("/").length;
      if (depth === 1 || (depth === 2 && /^(partition_alloc|blink_gc|v8|malloc|cc|skia|gpu|web_cache|font_caches|discardable)/.test(name))) {
        top[name] = bytes / MIB;
      }
    }
    byPid.set(e.pid, { privateFootprintMiB: totals.get(e.pid) ?? null, allocators: top });
  }
  for (const [pid, info] of byPid) {
    console.log(`pid ${pid}  private footprint ${info.privateFootprintMiB?.toFixed(1) ?? "?"} MiB`);
    const rows = Object.entries(info.allocators).sort((a, b) => b[1] - a[1]).filter(([, v]) => v >= 0.5);
    for (const [name, mib] of rows) console.log(`  ${mib.toFixed(1).padStart(8)} MiB  ${name}`);
  }
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
