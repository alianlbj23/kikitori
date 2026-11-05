// Interactive launcher for app development (`npm run menu` in app/).
//
// Role: picks which engine the Tauri app talks to and starts `tauri dev` (or a
// debug build) with the right environment, so nobody has to remember
// KIKITORI_ENGINE_CMD / KIKITORI_ENGINE_FAKE. No shell is involved: commands are
// spawned with argument arrays.

import { spawn } from "node:child_process";
import { existsSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import process from "node:process";
import { createInterface } from "node:readline/promises";
import { fileURLToPath } from "node:url";

const appDir = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const repoRoot = resolve(appDir, "..");
const tauriCli = join(appDir, "node_modules", "@tauri-apps", "cli", "tauri.js");
const fakeEngine = join(appDir, "dev", "fake_engine.py");

const fakeCmd = (extra = []) =>
  JSON.stringify(["uv", "run", "--no-project", "python", fakeEngine, ...extra]);

const choices = [
  {
    label: "開發模式：正式引擎的假模式（合成聲音、假辨識與翻譯、假 8 GB NVIDIA 顯示卡缺 cuBLAS，建議）",
    args: ["dev"],
    env: { KIKITORI_ENGINE_FAKE: "1" },
  },
  {
    label: "開發模式：正式引擎的假模式，假硬體為 Apple Silicon（GPU 加速元件不支援）",
    args: ["dev"],
    env: { KIKITORI_ENGINE_FAKE: "1", KIKITORI_ENGINE_FAKE_HARDWARE: "apple" },
  },
  {
    label: "開發模式：正式引擎的假模式，GPU 加速元件下載會失敗（雜湊不符）",
    args: ["dev"],
    env: { KIKITORI_ENGINE_FAKE: "1", KIKITORI_ENGINE_FAKE_HARDWARE: "broken-runtime" },
  },
  {
    label: "開發模式：app/dev/fake_engine.py（不需要 Python 專案環境，字幕會一直產生）",
    args: ["dev"],
    env: { KIKITORI_ENGINE_CMD: fakeCmd() },
  },
  {
    label: "開發模式：假引擎加故障注入（每 3 句插入壞資料、25 秒後當機）",
    args: ["dev"],
    env: { KIKITORI_ENGINE_CMD: fakeCmd(["--garbage-every", "3", "--crash-after", "25"]) },
  },
  {
    label: "開發模式：正式引擎（實際擷取聲音、載入模型）",
    args: ["dev"],
    env: {},
  },
  {
    label: "建置除錯版執行檔（不打包安裝檔）",
    args: ["build", "--debug", "--no-bundle"],
    env: {},
  },
];

async function ask() {
  const rl = createInterface({ input: process.stdin, output: process.stdout });
  try {
    console.log("\nkikitori 開發選單\n");
    choices.forEach((c, i) => console.log(`  ${i + 1}. ${c.label}`));
    console.log(`  ${choices.length + 1}. 離開\n`);
    for (;;) {
      const answer = (await rl.question("請輸入編號：")).trim();
      const n = Number.parseInt(answer, 10);
      if (String(n) === answer && n >= 1 && n <= choices.length + 1) return n;
      console.log(`請輸入 1 到 ${choices.length + 1} 之間的數字。`);
    }
  } finally {
    rl.close();
  }
}

async function main() {
  if (!existsSync(tauriCli)) {
    console.error("找不到 Tauri CLI，請先在 app/ 執行 npm install。");
    return 1;
  }
  const n = await ask();
  if (n === choices.length + 1) return 0;
  const choice = choices[n - 1];
  console.log(`\n執行 tauri ${choice.args.join(" ")}`);
  for (const [k, v] of Object.entries(choice.env)) console.log(`  ${k}=${v}`);
  const child = spawn(process.execPath, [tauriCli, ...choice.args], {
    cwd: appDir,
    stdio: "inherit",
    env: { ...process.env, KIKITORI_ENGINE_CWD: repoRoot, ...choice.env },
  });
  return new Promise((resolveExit) => {
    child.on("error", (e) => {
      console.error(`無法啟動 Tauri CLI：${e.message}`);
      resolveExit(1);
    });
    child.on("exit", (code, signal) => resolveExit(code ?? (signal ? 1 : 0)));
  });
}

main().then(
  (code) => process.exit(code),
  (e) => {
    console.error(e);
    process.exit(1);
  },
);
