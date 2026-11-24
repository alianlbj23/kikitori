"""遠端運算的實機驗收（A 端，互動選單）：和運算主機配對、用音檔即時跑一場、記錄延遲。

暫時性實驗工具，不屬於正式程式碼，也不會被 unittest 收集（docs/specs/remote-compute.md「驗收」）。
A 用這台電腦的設定資料夾（與 app 共用 `compute/`）：配對結果就是 app 之後會用的那一份。

量的東西：
- 每句完整字幕的 `latency_ms`（B 量「句尾判定 → 送出」，A 加上 RTT/2）、心跳的 RTT。
- 從開始送音訊到第一句完整字幕的時間。

用法：
  uv run python -X utf8 experiments/remote_compute/menu.py
  uv run python -X utf8 experiments/remote_compute/menu.py --batch run <host> <port> <wav> [label]
"""

from __future__ import annotations

import json
import queue
import socket
import statistics
import sys
import threading
import time
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
RESULTS = HERE / "results"
DEFAULT_WAV = ROOT / "experiments" / "e2e_acceptance" / "data" / "final_ja.wav"

sys.path.insert(0, str(ROOT / "src"))

from kikitori.capture import FileSource
from kikitori.config import SettingsStore, default_settings_path
from kikitori.engine.server import engine_version
from kikitori.remote import pair_net as pn
from kikitori.remote.client import RemoteClientSession, RemoteError
from kikitori.remote.identity import load_or_create_identity
from kikitori.remote.pairing import TrustStore
from kikitori.remote.paths import compute_dir, servers_path

MENU = """
遠端運算實機驗收（A 端）
  1) 和運算主機配對
  2) 用音檔跑一場遠端字幕
  3) 已配對的運算主機
  4) 離開
"""


def _settings() -> Any:
    store = SettingsStore(default_settings_path())
    store.load()  # 沒 load() 的 store 是預設值
    return store.get()


def pair(host: str, port: int, ask: Any = input) -> bool:
    """連到運算主機的配對埠，顯示數字，等使用者確認。"""
    identity = load_or_create_identity(compute_dir())
    updates: queue.Queue[pn.PairingUpdate] = queue.Queue()
    client = pn.PairingClient(
        identity=identity,
        trust=TrustStore(servers_path()),
        name=socket.gethostname(),
        host=host,
        port=pn.pairing_port(port),
        on_update=updates.put,
        label=host,
    )
    client.start()
    while True:
        update = updates.get()
        if update.state == "show_digits" and update.digits:
            d = update.digits
            print(
                f"請在運算主機「{update.peer_name}」上輸入：{d[:3]} {d[3:]}", flush=True
            )
        elif update.state == "confirm":
            answer = ask("運算主機上顯示配對成功了嗎？(y/N)：").strip().lower()
            client.confirm(answer == "y")
        elif update.state in pn.TERMINAL_STATES:
            print("配對成功" if update.state == "done" else f"配對失敗：{update.code}")
            client.join(10)
            return update.state == "done"


class Relay:
    """本機的 TCP 轉送，可以「斷線」一段時間：不轉送、也不關閉（像 Wi-Fi 掉了，沒有 FIN／RST）。

    斷線期間新的連線要等到恢復才接到運算主機（連不上的效果）。恢復後舊連線照常轉送——和真的
    網路一樣，TCP 沒逾時的話會自己接續；兩端若已經判定斷線就各自關掉它。
    """

    def __init__(self, host: str, port: int) -> None:
        self._target = (host, port)
        self._listener = socket.create_server(("127.0.0.1", 0))
        self.port = self._listener.getsockname()[1]
        self._dark_until = 0.0
        threading.Thread(target=self._accept, daemon=True).start()

    def go_dark(self, seconds: float) -> None:
        print(f"[relay] 斷線 {seconds:.0f} 秒", flush=True)
        self._dark_until = time.monotonic() + seconds

    def _wait_light(self) -> None:
        while time.monotonic() < self._dark_until:
            time.sleep(0.05)

    def _accept(self) -> None:
        while True:
            client, _ = self._listener.accept()
            threading.Thread(target=self._open, args=(client,), daemon=True).start()

    def _open(self, client: socket.socket) -> None:
        self._wait_light()
        try:
            upstream = socket.create_connection(self._target, timeout=5)
        except OSError:
            client.close()
            return
        upstream.settimeout(None)
        for a, b in ((client, upstream), (upstream, client)):
            threading.Thread(target=self._pump, args=(a, b), daemon=True).start()

    def _pump(self, src: socket.socket, dst: socket.socket) -> None:
        try:
            while True:
                data = src.recv(65536)
                if not data:
                    break
                self._wait_light()  # 斷線期間收到的資料先扣著（不轉送、不關閉）
                dst.sendall(data)
        except OSError:
            pass
        finally:
            for sock in (src, dst):
                try:
                    sock.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass


def run(
    host: str,
    port: int,
    wav: Path,
    label: str = "",
    drop_at: float | None = None,
    drop_secs: float = 0.0,
) -> dict[str, Any]:
    """用音檔（即時速度）跑一場，回傳並存下量測結果。"""
    settings = _settings()
    identity = load_or_create_identity(compute_dir())
    ended = threading.Event()
    statuses: list[tuple[float, str, str | None, str | None]] = []
    finals: list[dict[str, Any]] = []
    rtts: list[float] = []
    started_at = [0.0]
    audio_t0 = [0.0]

    def on_status(state: str, detail: str | None, code: str | None, data: Any) -> None:
        statuses.append((time.monotonic(), state, detail, code))
        print(f"[status] {state} {code or ''} {detail or ''} {data or ''}", flush=True)
        if state == "running":
            started_at[0] = time.monotonic()
        if state in ("idle", "error"):
            ended.set()

    def on_event(name: str, data: dict[str, Any]) -> None:
        if name == "metrics" and data.get("rtt_ms") is not None:
            rtts.append(float(data["rtt_ms"]))
        if name == "subtitle" and data["is_final"] and data["original"]:
            now = time.monotonic()
            e2e = (now - audio_t0[0] - data["end_sample"] / 16000) * 1000
            data = dict(data, received_at=now, e2e_ms=round(e2e))
            finals.append(data)
            print(
                f"#{data['utterance_id']} e2e {data['e2e_ms']} ms (B {data.get('latency_ms')} ms)  "
                f"{data['original']}  →  {data['translation']}",
                flush=True,
            )

    target_host, target_port = host, port
    relay = None
    if drop_at is not None:
        relay = Relay(host, port)
        target_host, target_port = "127.0.0.1", relay.port
    session = RemoteClientSession(
        identity=identity,
        servers=TrustStore(servers_path()),
        host=target_host,
        port=target_port,
        app_version=engine_version(),
        mode="media",
        settings=settings,
        on_status=on_status,
        on_event=on_event,
        capture_code=lambda exc: "capture_error",
    )
    t0 = time.monotonic()
    try:
        session.connect()
    except RemoteError as exc:
        print(f"連線失敗：{exc.code}：{exc}")
        return {"error": exc.code}
    connect_secs = time.monotonic() - t0
    print(f"已連上（{connect_secs:.2f} s），運算主機的模型：{session.models}")
    # 音訊位置的牆鐘時間 = 開始送出的時刻 + 樣本數 ÷ 16000（與 pipeline_smoke 的定義相同）
    audio_t0[0] = time.monotonic()
    session.start(FileSource(wav, realtime=True))
    if relay is not None and drop_at is not None:
        threading.Timer(drop_at, relay.go_dark, args=(drop_secs,)).start()
    ended.wait()
    session.stop(5.0)
    latencies = [f["latency_ms"] for f in finals if f.get("latency_ms") is not None]
    first_final = (
        finals[0]["received_at"] - started_at[0] if finals and started_at[0] else None
    )
    result = {
        "label": label,
        "host": host,
        "port": port,
        "wav": wav.name,
        "models": session.models,
        "connect_secs": round(connect_secs, 3),
        "finals": len(finals),
        "latency_ms": _stats(latencies),
        "e2e_ms": _stats([f["e2e_ms"] for f in finals]),
        "rtt_ms": _stats(rtts),
        "first_final_secs": None if first_final is None else round(first_final, 2),
        "end": statuses[-1][1:] if statuses else None,
        "lines": [
            {
                k: f.get(k)
                for k in (
                    "utterance_id",
                    "start_sample",
                    "end_sample",
                    "latency_ms",
                    "e2e_ms",
                    "original",
                    "translation",
                    "voice",
                )
            }
            for f in finals
        ],
    }
    RESULTS.mkdir(exist_ok=True)
    name = time.strftime("%Y%m%d-%H%M%S") + (f"_{label}" if label else "") + ".json"
    (RESULTS / name).write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        json.dumps(
            {k: v for k, v in result.items() if k != "lines"}, ensure_ascii=False
        )
    )
    print(f"結果：{RESULTS / name}")
    return result


def local(wav: Path, label: str = "local") -> dict[str, Any]:
    """基準：在這台電腦（運算主機）上用同一套 pipeline 與設定跑同一個音檔，量同樣的延遲。"""
    from kikitori.pipeline import Pipeline
    from kikitori.pipeline.factory import build_session_components, pipeline_config
    from kikitori.pipeline.types import release_components

    settings = _settings()
    print("載入模型…", flush=True)
    components = build_session_components(settings, "media")
    finals: list[dict[str, Any]] = []
    done = threading.Event()
    audio_t0 = [0.0]

    def on_subtitle(update: Any) -> None:
        if update.is_final and update.original:
            now = time.monotonic()
            e2e = round((now - audio_t0[0] - update.end_sample / 16000) * 1000)
            finals.append({"latency_ms": update.latency_ms, "e2e_ms": e2e})
            print(f"#{update.utterance_id} e2e {e2e} ms  {update.original}", flush=True)

    def on_status(state: str, detail: str | None) -> None:
        if state in ("idle", "error"):
            print(f"[status] {state} {detail or ''}", flush=True)
            done.set()

    pipeline = Pipeline(
        components,
        pipeline_config(settings, "media"),
        on_subtitle=on_subtitle,
        on_status=on_status,
    )
    audio_t0[0] = time.monotonic()
    pipeline.start(FileSource(wav, realtime=True))
    done.wait()
    release_components(components)
    result = {
        "label": label,
        "wav": wav.name,
        "models": {"fast": settings.asr.fast_model, "final": settings.asr.final_model},
        "finals": len(finals),
        "latency_ms": _stats([f["latency_ms"] for f in finals if f["latency_ms"]]),
        "e2e_ms": _stats([f["e2e_ms"] for f in finals]),
    }
    RESULTS.mkdir(exist_ok=True)
    name = time.strftime("%Y%m%d-%H%M%S") + f"_{label}.json"
    (RESULTS / name).write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(result, ensure_ascii=False))
    return result


def _stats(values: list[float]) -> dict[str, float] | None:
    if not values:
        return None
    ordered = sorted(values)
    return {
        "n": len(ordered),
        "median": round(statistics.median(ordered), 1),
        "p90": round(ordered[min(len(ordered) - 1, int(len(ordered) * 0.9))], 1),
        "max": round(ordered[-1], 1),
    }


def _ask_host() -> tuple[str, int]:
    settings = _settings()
    default = settings.compute.host
    host = input(f"運算主機位址[{default}]：").strip() or default
    port = int(input(f"埠[{settings.compute.port}]：").strip() or settings.compute.port)
    return host, port


def menu() -> int:
    while True:
        print(MENU)
        try:
            choice = input("請輸入編號：").strip()
        except EOFError:
            return 0
        if choice == "1":
            pair(*_ask_host())
        elif choice == "2":
            host, port = _ask_host()
            wav = input(f"音檔[{DEFAULT_WAV.name}]：").strip()
            run(host, port, Path(wav) if wav else DEFAULT_WAV)
        elif choice == "3":
            for peer in TrustStore(servers_path()).peers():
                print(f"  {peer.name}  {peer.spki.hex()[:32]}  {peer.paired_at}")
        elif choice == "4":
            return 0


def main(argv: list[str]) -> int:
    if argv[:2] == ["--batch", "run"]:
        host, port, wav = argv[2], int(argv[3]), Path(argv[4])
        result = run(host, port, wav, argv[5] if len(argv) > 5 else "")
        return 0 if "error" not in result else 1
    if argv[:2] == ["--batch", "drop"]:
        # --batch drop <host> <port> <wav> <幾秒後斷線> <斷幾秒>
        host, port, wav = argv[2], int(argv[3]), Path(argv[4])
        at, secs = float(argv[5]), float(argv[6])
        result = run(host, port, wav, f"drop{secs:.0f}s", drop_at=at, drop_secs=secs)
        return 0 if "error" not in result else 1
    if argv[:2] == ["--batch", "local"]:
        local(Path(argv[2]))
        return 0
    if argv[:2] == ["--batch", "pair"]:
        return 0 if pair(argv[2], int(argv[3])) else 1
    return menu()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
