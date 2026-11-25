"""無介面的運算主機（B）：`kikitori` 選單 →「運算主機」，給用 SSH 管理、沒開 app 視窗的電腦。

位於 remote 模組的宿主層（docs/specs/remote-compute.md「B 的兩種宿主」）。與 GUI 共用同一個設定
資料夾與 `compute/`（憑證、信任清單）。依使用者規則做成編號選單，不靠記指令參數：

1. 開始提供運算（前景執行；執行中可按鍵配對、列出、斷開目前的電腦、看目前負荷）
2. 允許新的電腦配對（輸入另一台電腦上顯示的 6 位數）
3. 已配對的電腦（列出／移除）
4. 準備模型（套用硬體建議、下載辨識模型與翻譯模型；重用引擎的下載邏輯）
5. 選擇模型（列出每個辨識模型在這台跑不跑得動；換完整辨識模型，沒下載的先下載）
6. 變更監聽位址（從這台電腦的私有網段與 Tailscale 位址選，不預設 0.0.0.0）
7. 重建這台電腦的身分

「選擇模型」與連進來的電腦看到的是同一份能力（`remote.capacity`，docs/specs/compute-capacity.md
「六」）。提供運算中，配對過的電腦也可以改這台的模型、請這台下載（「七」，`_HeadlessManager`）：
寫進同一個設定檔、直接用 ASR 的下載函式，並在終端機印出誰改了什麼與下載進度。
"""

import json
import logging
import queue
import socket
import threading
import time
from collections.abc import Callable, Mapping
from typing import Any

from kikitori.config import (
    Settings,
    SettingsError,
    SettingsStore,
    default_settings_path,
)
from kikitori.remote.capacity import (
    HostCapacity,
    HostCapacitySource,
    default_detect,
    default_downloaded,
)
from kikitori.remote.identity import (
    Identity,
    IdentityError,
    load_or_create_identity,
    reset_identity,
)
from kikitori.remote.manage import (
    DOWNLOAD_FAILED_DETAIL,
    DOWNLOAD_START_DETAIL,
    SETTINGS_INVALID_DETAIL,
    SETTINGS_SAVE_DETAIL,
    HostManageError,
    ProgressFn,
)
from kikitori.remote.network import is_allowed_listen_host, is_allowed_source
from kikitori.remote.pair_net import (
    TERMINAL_STATES,
    PairingServer,
    PairingUpdate,
    pairing_port,
)
from kikitori.remote.pairing import PairingError, TrustStore
from kikitori.remote.paths import compute_dir, servers_path, trusted_path
from kikitori.translate.presets import normalize_model_name

logger = logging.getLogger(__name__)

InputFn = Callable[[str], str]
OutFn = Callable[[str], None]

MENU = """
運算主機（替其他電腦跑辨識與翻譯）
  1) 開始提供運算
  2) 允許新的電腦配對
  3) 已配對的電腦
  4) 準備模型
  5) 選擇模型
  6) 變更監聽位址
  7) 重建這台電腦的身分（所有配對都要重做）
  8) 返回
"""

SERVING_HELP = (
    "執行中：p＝配對新電腦  l＝已配對的電腦  d＝斷開目前的電腦  s＝目前負荷  q＝停止"
)

# 只用來讓作業系統挑出「往那個方向」會用的本機位址；UDP connect 不送出任何封包
_PROBES = ("192.0.2.1", "100.100.100.100")
DOWNLOAD_TIMEOUT = 6 * 3600.0
REQUEST_TIMEOUT = 120.0
VRAM_QUERY_TIMEOUT = 2.0
"""按 s 時查顯示卡記憶體最多等多久：卡住時寧可這次不顯示。"""

_FIT_LABELS = {"ok": "跑得動", "tight": "可能吃緊", "over": "超出負荷"}

VramFn = Callable[[], tuple[int, int] | None]
"""目前顯示卡記憶體 `(已用, 總量)`（MiB）；沒有 NVIDIA 或查不到時 None。"""
DownloadFn = Callable[[str, Callable[[int, int], None], threading.Event], None]
"""`(model_id, on_bytes(downloaded, total), cancel)`：下載並驗證一個 ASR 模型（阻塞）。"""
RELAY_SECS = 1.0
"""轉給另一台電腦的下載進度最多每秒一則（下載函式每 1 MiB 回報一次）。"""


def default_download_asr(
    model_id: str, on_bytes: Callable[[int, int], None], cancel: threading.Event
) -> None:
    """真的下載 ASR 模型（`pipeline.factory` 是 ASR 唯一的接縫；延後載入，依賴較重）。"""
    from kikitori.pipeline.factory import download_asr_model

    download_asr_model(model_id, on_bytes, cancel)


def fingerprint(spki: bytes) -> str:
    """SPKI 雜湊的前 16 個位元組，每 2 位元組一組（給使用者核對用）。"""
    text = spki[:16].hex()
    return " ".join(text[i : i + 4] for i in range(0, len(text), 4))


def local_addresses() -> list[str]:
    """這台電腦可以監聽的私有網段與 Tailscale 位址（不含 127.0.0.1 與 0.0.0.0）。"""
    routed: list[str] = []
    for probe in _PROBES:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                sock.connect((probe, 9))
                routed.append(sock.getsockname()[0])
        except OSError:
            continue  # 沒有往那邊的路由（例如沒裝 Tailscale）
    named: list[str] = []
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None):
            named.append(str(info[4][0]))
    except OSError as exc:
        logger.info("cannot resolve this computer's own name: %s", exc)
    return reachable_addresses(routed, named)


def _usable(address: str) -> bool:
    # fe80:: 鏈路本地位址要帶網卡編號才能監聽，對使用者也沒有意義
    return (
        is_allowed_source(address)
        and not address.startswith(("127.", "::1"))
        and not address.lower().startswith("fe80:")
    )


def reachable_addresses(routed: list[str], named: list[str]) -> list[str]:
    """從候選位址挑出要讓使用者選的。

    `routed` 是作業系統往區網與 Tailscale 實際會用的位址（UDP 探測）；找得到就只列它們，
    虛擬網卡（VirtualBox、WSL）的位址會讓不懂網路的人不知道該選哪個。一個都沒有時
    （例如沒有預設閘道的區網）才退回這台電腦名稱解析到的全部私有位址。
    """
    preferred = [a for a in dict.fromkeys(routed) if _usable(a)]
    if preferred:
        return preferred
    return [a for a in dict.fromkeys(named) if _usable(a)]


class _Gate:
    """GPU 使用權：無介面主機沒有自己的字幕 session，只有一台遠端電腦能用。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._held = False

    def acquire(self) -> bool:
        with self._lock:
            if self._held:
                return False
            self._held = True
            return True

    def release(self) -> None:
        with self._lock:
            self._held = False


def query_vram(timeout: float = VRAM_QUERY_TIMEOUT) -> tuple[int, int] | None:
    """VRAM 最大那張 NVIDIA 顯示卡目前的 `(已用, 總量)`（MiB）；沒有或查不到時 None。"""
    from kikitori.hardware.nvidia import (
        NvidiaSmiError,
        find_nvidia_smi,
        query_nvidia_gpus,
    )
    from kikitori.hardware.system import os_name

    try:
        executable = find_nvidia_smi(os_name())
        if executable is None:
            return None
        gpus, _ = query_nvidia_gpus(executable, timeout=timeout)
    except (NvidiaSmiError, OSError) as exc:
        logger.info("cannot read the GPU memory: %s", exc)
        return None
    if not gpus:
        return None
    gpu = max(gpus, key=lambda g: g.memory_total_mb)
    if gpu.memory_used_mb is None:
        return None
    return gpu.memory_used_mb, gpu.memory_total_mb


class ComputeHost:
    """無介面主機的狀態：本機憑證、信任清單、設定，以及執行中的伺服器。"""

    def __init__(
        self,
        store: SettingsStore,
        identity: Identity,
        trust: TrustStore,
        app_version: str,
        *,
        capacity: HostCapacitySource | None = None,
        vram: VramFn = query_vram,
        download_asr: DownloadFn = default_download_asr,
    ) -> None:
        """
        Args:
            store: 設定。
            identity: 這台電腦的憑證。
            trust: 允許連進來的電腦。
            app_version: 握手時告訴對方的版本。
            capacity: 這台的能力（連進來的電腦與「選擇模型」看到的同一份）；None 時用真的
                硬體偵測與模型快取。
            vram: 目前顯示卡記憶體（「目前負荷」用）。
            download_asr: 別台請這台下載 ASR 模型時用的下載函式。
        """
        self.store = store
        self.identity = identity
        self.trust = trust
        self.app_version = app_version
        self.capacity: HostCapacitySource = capacity or HostCapacity(
            self.reload_settings, detect=default_detect, downloaded=default_downloaded
        )
        self.vram = vram
        self.download_asr = download_asr
        self.server: Any = None  # RemoteServer（延後載入：pipeline 依賴較重）
        # 「重讀設定檔」與「重讀＋寫入」互斥：連線執行緒（能力查詢、別台改模型）各自重讀，
        # 一條重讀到一半時另一條寫入，前者會把舊內容放回記憶體，之後的寫入就蓋掉別人剛存的。
        # 可重入：set_models 拿著鎖時呼叫 reload_settings。
        self.settings_lock = threading.RLock()
        self._downloads_lock = threading.Lock()
        self._downloads: dict[str, threading.Event] = {}
        """別台請這台下載、還在進行的 ASR 模型 → 取消用的事件。"""

    @property
    def settings(self) -> Settings:
        """目前的設定（每次重新讀檔：GUI 可能同時改過）。"""
        return self.reload_settings()

    def reload_settings(self) -> Settings:
        """重新讀設定檔；讀不進來時記 warning（store 用預設值，不會覆寫檔案）。"""
        with self.settings_lock:
            warning = self.store.load()
            if warning:
                logger.warning("settings: %s", warning)
            return self.store.get()

    def manager(self, out: OutFn) -> "_HeadlessManager":
        """別台電腦改這台的模型、請這台下載時的處理（訊息印到 `out`）。"""
        return _HeadlessManager(self, out)

    def start_server(self, out: OutFn = print) -> None:
        """依 `compute.listen_host`／`listen_port` 開始監聽。

        Args:
            out: 別台改這台的模型、請這台下載時，在終端機印出的地方。

        Raises:
            ValueError: 還沒選監聽位址。
            OSError: 埠被佔用。
        """
        from kikitori.pipeline.factory import build_session_components
        from kikitori.remote.server import RemoteServer

        compute = self.settings.compute
        if not compute.listen_host:
            raise ValueError("no listen address is set")
        gate = _Gate()
        self.server = RemoteServer(
            identity=self.identity,
            trust=self.trust,
            # 每場開始時重新讀檔：模型組合等用 B 目前的設定
            settings=self.reload_settings,
            build_components=build_session_components,
            acquire_gpu=gate.acquire,
            release_gpu=gate.release,
            host=compute.listen_host,
            port=compute.listen_port,
            app_version=self.app_version,
            capacity=self.capacity,
            manager=self.manager(out),
        )
        self.server.start()

    def stop_server(self) -> None:
        """停止監聽、斷開目前的電腦，並取消別台請這台做的下載。

        無介面主機的下載在提供運算的畫面裡進行：使用者按 q 離開之後不該還在背景下載、
        把進度印進選單裡。
        """
        server, self.server = self.server, None
        if server is not None:
            server.stop()
        with self._downloads_lock:
            cancels = list(self._downloads.values())
        for cancel in cancels:
            cancel.set()

    def begin_download(self, model: str) -> threading.Event:
        """登記一個下載，回傳取消用的事件。

        Raises:
            HostManageError: `download_in_progress`（同一個模型已在下載）。
        """
        with self._downloads_lock:
            if model in self._downloads:
                raise HostManageError(
                    "download_in_progress", f"{model} is already downloading"
                )
            cancel = threading.Event()
            self._downloads[model] = cancel
            return cancel

    def end_download(self, model: str) -> None:
        """下載結束（成功、失敗或取消）。"""
        with self._downloads_lock:
            self._downloads.pop(model, None)

    def trust_changed(self) -> None:
        """配對或移除之後：通知執行中的伺服器。"""
        if self.server is not None:
            self.server.trust_changed()

    def reset_identity(self) -> None:
        """換一個新的身分並忘掉所有配對（其他電腦記的是舊憑證，已經連不上這台）。

        先清配對清單再換身分：清單清不掉時身分還是原本的，可以安全重試。

        Raises:
            OSError: 無法刪除或寫入 `compute/` 裡的檔案。
        """
        self.stop_server()
        _forget_pairings(self.trust)
        self.identity = reset_identity(compute_dir())


class _HeadlessManager:
    """別台電腦改無介面主機的模型（`remote.manage.HostManager`）：寫設定檔、直接下載、印在終端機。"""

    def __init__(self, host: ComputeHost, out: OutFn) -> None:
        self._host = host
        self._out = out

    def set_models(
        self, by: str, asr: Mapping[str, str], translation: Mapping[str, str]
    ) -> None:
        """寫進設定檔（下一場字幕生效），並印出「〇〇 把辨識模型改成 …」。

        設定檔壞掉時不寫（「預設值＋那台的修改」會蓋掉這台使用者原本的設定）。回給那台的
        `detail` 是固定字句：例外原文含這台的路徑，只寫進 log。

        Raises:
            HostManageError: `settings_error`。
        """
        patch: dict[str, Any] = {}
        if asr:
            patch["asr"] = dict(asr)
        if translation:
            patch["translation"] = dict(translation)
        with self._host.settings_lock:
            # 先重讀：GUI 與這裡共用設定檔，不能用這個行程裡較舊的內容蓋掉別人剛存的
            self._host.reload_settings()
            if self._host.store.file_invalid:
                logger.warning(
                    "%s asked to change the models, but the settings file is invalid",
                    by,
                )
                raise HostManageError("settings_error", SETTINGS_INVALID_DETAIL)
            try:
                settings = self._host.store.update(patch)
            except (OSError, SettingsError) as exc:
                logger.error("cannot save the models %s chose: %s", by, exc)
                raise HostManageError("settings_error", SETTINGS_SAVE_DETAIL) from exc
        if asr:
            self._say(
                f"「{by}」把辨識模型改成：即時 {settings.asr.fast_model}、"
                f"完整 {settings.asr.final_model}（下一場字幕開始生效）。"
            )
        if translation:
            self._say(
                f"「{by}」把翻譯模型改成：即時 {settings.translation.fast_model}、"
                f"完整 {settings.translation.final_model}（下一場字幕開始生效）。"
            )

    def start_download(self, by: str, model: str, progress: ProgressFn) -> None:
        """在背景下載（不管提出要求的電腦還在不在），進度印在終端機並回報給 `progress`。

        Raises:
            HostManageError: `download_in_progress`；`settings_error`（下載執行緒開不起來，
                登記已撤銷）。
        """
        cancel = self._host.begin_download(model)
        try:
            threading.Thread(
                target=self._run,
                args=(model, progress, cancel),
                name=f"host-download-{model}",
                daemon=True,
            ).start()
        except RuntimeError as exc:
            # 開不了執行緒（資源耗盡）：不撤銷登記的話，這個模型之後永遠是「已在下載」
            logger.error("cannot start downloading %s for %s: %s", model, by, exc)
            self._host.end_download(model)
            raise HostManageError("settings_error", DOWNLOAD_START_DETAIL) from exc
        self._say(f"「{by}」要這台下載辨識模型 {model}…")

    def _say(self, line: str) -> None:
        """印到終端機；終端機壞掉（例如 SSH 斷了）只記 log，不能讓提出要求的電腦收不到結果。"""
        try:
            self._out(line)
        except Exception:
            logger.exception("cannot print to the terminal: %s", line)

    def _run(self, model: str, progress: ProgressFn, cancel: threading.Event) -> None:
        last_relay = 0.0
        last_percent = -10
        total = 0

        def report(downloaded: int, reported_total: int) -> None:
            nonlocal last_relay, last_percent, total
            total = reported_total
            now = time.monotonic()
            if now - last_relay >= RELAY_SECS:
                last_relay = now
                progress(downloaded, reported_total, "downloading", None)
            percent = downloaded * 100 // reported_total if reported_total else 0
            if percent >= last_percent + 10:
                last_percent = percent
                self._say(f"  {model}：{percent}%")

        try:
            self._host.download_asr(model, report, cancel)
        except Exception as exc:
            logger.exception("downloading %s for another computer failed", model)
            self._host.end_download(model)
            self._say(f"  {model}：下載失敗（{exc}）")
            # 例外原文可能含這台的檔案路徑：回給那台只用通用字句（完整原因在上面的 log）
            progress(0, total, "error", DOWNLOAD_FAILED_DETAIL)
            return
        self._host.end_download(model)
        self._say(f"  {model}：完成")
        progress(total, total, "done", None)


def _forget_pairings(trust: TrustStore) -> None:
    trust.clear()
    TrustStore(servers_path()).clear()


def open_host() -> ComputeHost:
    """讀這台電腦的設定與 `compute/`（第一次使用時產生憑證）。"""
    from kikitori.engine.server import engine_version

    store = SettingsStore(default_settings_path())
    # 一定要先 load()：沒載入的 store 是預設值，之後任何 update() 都會把整份設定蓋掉
    warning = store.load()
    if warning:
        logger.warning("settings: %s", warning)
    identity = load_or_create_identity(compute_dir())
    return ComputeHost(store, identity, TrustStore(trusted_path()), engine_version())


# --------------------------------------------------------------------------- 選單


def host_menu(
    input_fn: InputFn = input,
    out: OutFn = print,
    host_factory: Callable[[], ComputeHost] = open_host,
) -> int:
    """「運算主機」子選單；回傳結束碼。輸入結束（EOF）視同返回。"""
    try:
        host = host_factory()
    except IdentityError as exc:
        # 身分檔損毀：不自動重建（會讓所有配對失效、也可能掩蓋竄改），問使用者
        logger.error("the remote identity is unreadable: %s", exc)
        out(f"這台電腦的身分檔讀不出來：{exc}")
        if not _confirm_reset(input_fn, out):
            return 1
        try:
            _forget_pairings(TrustStore(trusted_path()))
            reset_identity(compute_dir())
            host = host_factory()
        except Exception as again:
            logger.exception("cannot reset the remote identity")
            out(f"無法重建：{again}")
            return 1
        out("已重建這台電腦的身分。")
    except Exception as exc:
        logger.exception("cannot open the compute host")
        out(f"無法讀取這台電腦的遠端運算設定：{exc}")
        return 1
    out(f"這台電腦的指紋：{fingerprint(host.identity.spki)}")
    while True:
        compute = host.settings.compute
        where = compute.listen_host or "（尚未選擇）"
        out(
            f"監聽位址：{where}  埠：{compute.listen_port}（配對用 {pairing_port(compute.listen_port)}）"
        )
        out(MENU)
        try:
            choice = input_fn("請輸入編號：").strip()
        except (EOFError, KeyboardInterrupt):
            out("")
            return 0
        if choice == "1":
            serve(host, input_fn, out)
        elif choice == "2":
            pair(host, input_fn, out)
        elif choice == "3":
            manage_peers(host, input_fn, out)
        elif choice == "4":
            prepare_models(host, input_fn, out)
        elif choice == "5":
            choose_model(host, input_fn, out)
        elif choice == "6":
            choose_listen_host(host, input_fn, out)
        elif choice == "7":
            reset_from_menu(host, input_fn, out)
        elif choice == "8":
            return 0
        else:
            out(f"無效的選項：{choice!r}")


def _confirm_reset(input_fn: InputFn, out: OutFn) -> bool:
    out("重建後，所有配對過的電腦都要重新配對一次。")
    try:
        return input_fn("要重建這台電腦的身分嗎？(y/N)：").strip().lower() == "y"
    except (EOFError, KeyboardInterrupt):
        out("")
        return False


def reset_from_menu(host: ComputeHost, input_fn: InputFn, out: OutFn) -> bool:
    """選單「重建這台電腦的身分」：確認後重建；回傳是否有重建。"""
    if not _confirm_reset(input_fn, out):
        return False
    try:
        host.reset_identity()
    except OSError as exc:
        logger.exception("cannot reset the remote identity")
        out(f"無法重建：{exc}")
        return False
    out(f"已重建。這台電腦的新指紋：{fingerprint(host.identity.spki)}")
    return True


def choose_listen_host(host: ComputeHost, input_fn: InputFn, out: OutFn) -> bool:
    """從這台電腦的位址選一個存進 `compute.listen_host`；回傳是否有選。"""
    addresses = local_addresses()
    out("要讓其他電腦從哪個位址連進來？（Tailscale 位址是 100.x.x.x）")
    for i, address in enumerate(addresses, 1):
        out(f"  {i}) {address}")
    out("  或直接輸入這台電腦的 IP；空白取消")
    try:
        answer = input_fn("請選擇：").strip()
    except (EOFError, KeyboardInterrupt):
        return False
    if not answer:
        return False
    if answer.isdigit() and 1 <= int(answer) <= len(addresses):
        answer = addresses[int(answer) - 1]
    if not is_allowed_listen_host(answer):
        out("只能用私有網段（區網）或 Tailscale 的位址。")
        return False
    try:
        host.store.update({"compute": {"listen_host": answer}})
    except Exception as exc:
        logger.exception("cannot save the listen address")
        out(f"無法儲存：{exc}")
        return False
    out(f"已設定監聽位址：{answer}")
    return True


def serve(host: ComputeHost, input_fn: InputFn, out: OutFn) -> None:
    """前景提供運算，直到使用者輸入 q、Ctrl+C 或輸入結束。"""
    if not host.settings.compute.listen_host and not choose_listen_host(
        host, input_fn, out
    ):
        return
    if not host.trust.peers():
        out("還沒有配對過任何電腦：先用「允許新的電腦配對」，或在執行中按 p。")
    try:
        host.start_server(out)
    except OSError as exc:
        out(f"無法開始監聽（埠被佔用？）：{exc}")
        return
    compute = host.settings.compute
    out(f"提供運算中：{compute.listen_host}:{compute.listen_port}")
    out(SERVING_HELP)
    try:
        while True:
            try:
                command = input_fn("> ").strip().lower()
            except EOFError:
                return
            if command == "q":
                return
            if command == "p":
                pair(host, input_fn, out)
            elif command == "l":
                list_peers(host, out)
            elif command == "d":
                server = host.server
                kicked = server is not None and server.disconnect()
                out("已斷開目前的電腦。" if kicked else "目前沒有電腦連線。")
            elif command == "s":
                out(load_line(host))
            elif command:
                out(SERVING_HELP)
    except KeyboardInterrupt:
        out("")
    finally:
        host.stop_server()
        out("已停止提供運算。")


def load_line(host: ComputeHost) -> str:
    """目前負荷一行：完整辨識忙碌度、待處理句數、顯示卡記憶體（compute-capacity 六）。"""
    server = host.server
    if server is None or server.owner_spki() is None:
        return "目前沒有電腦在用這台。"
    metrics = server.current_metrics()
    if metrics is None:
        return "有電腦在用這台，模型還在載入（或正在等它重新連線）。"
    busy = metrics.final_busy
    parts = [
        "完整辨識忙碌度：還沒有資料"
        if busy is None
        else f"完整辨識忙碌度 {round(busy * 100)}%",
        f"待處理 {metrics.backlog} 句",
    ]
    vram = host.vram()
    if vram is not None:
        used, total = vram
        parts.append(f"顯示卡記憶體 {used / 1024:.1f}／{total / 1024:.1f} GB")
    return "目前負荷：" + "・".join(parts)


_PAIR_FAILURES = {
    "mismatch": "數字不一樣，配對失敗（可能連到別台電腦，或有人想冒充）。",
    "concurrent_pairing": "有多台電腦同時嘗試配對，已全部中止。",
    "timeout": "等太久，配對已取消。",
    "expired": "5 分鐘內沒有電腦來配對，已關閉配對。",
    "cancelled": "已取消配對。",
    "rejected": "另一台電腦說數字不一樣，配對失敗。",
}


def pair(host: ComputeHost, input_fn: InputFn, out: OutFn) -> bool:
    """開 5 分鐘配對窗口，等另一台電腦連進來，再請使用者輸入它顯示的 6 位數。"""
    compute = host.settings.compute
    if not compute.listen_host and not choose_listen_host(host, input_fn, out):
        return False
    compute = host.settings.compute
    updates: queue.Queue[PairingUpdate] = queue.Queue()
    server = PairingServer(
        identity=host.identity,
        trust=host.trust,
        name=socket.gethostname(),
        host=compute.listen_host,
        port=pairing_port(compute.listen_port),
        on_update=updates.put,
    )
    try:
        server.open()
    except OSError as exc:
        out(f"無法開啟配對埠 {pairing_port(compute.listen_port)}：{exc}")
        return False
    out(
        f"已開放配對 5 分鐘：在另一台電腦的 kikitori 填入 {compute.listen_host}"
        f"（埠 {compute.listen_port}）並按「配對」。Ctrl+C 取消。"
    )
    try:
        while True:
            try:
                update = updates.get(timeout=0.5)
            except queue.Empty:
                continue
            if update.state == "enter_digits":
                if not _enter_digits(server, update, input_fn, out):
                    return False
            elif update.state in TERMINAL_STATES:
                return _pair_finished(host, update, out)
    except KeyboardInterrupt:
        server.cancel()
        out("已取消配對。")
        return False
    finally:
        server.join(5.0)


def _enter_digits(
    server: PairingServer, update: PairingUpdate, input_fn: InputFn, out: OutFn
) -> bool:
    out(f"「{update.peer_name or '另一台電腦'}」想要配對。")
    while True:
        try:
            text = input_fn("請輸入那台電腦畫面上的 6 位數（空白取消）：").strip()
        except (EOFError, KeyboardInterrupt):
            server.cancel()
            return False
        if server.join(0):
            # 等輸入的時候配對已經結束（逾時、對方取消）：input() 擋住了，現在才知道。不要把這行
            # 當成數字，也不要再問；結束的原因由呼叫端收到的結束狀態說明
            return True
        if not text:
            server.cancel()
            out("已取消配對。")
            return False
        try:
            server.submit_digits(text)
            return True
        except ValueError:
            out("要輸入 6 位數字。")
        except PairingError:
            return True  # 已經結束（逾時、對方取消）：交給結束狀態說明


def _pair_finished(host: ComputeHost, update: PairingUpdate, out: OutFn) -> bool:
    if update.state == "done":
        host.trust_changed()
        out("配對成功。請在另一台電腦上按「是」完成配對。")
        return True
    code = update.code or "internal_error"
    out(_PAIR_FAILURES.get(code, f"配對失敗（{code}）。"))
    return False


def list_peers(host: ComputeHost, out: OutFn) -> None:
    """列出已配對的電腦（標出目前連線中的那台）。"""
    peers = host.trust.peers()
    if not peers:
        out("還沒有配對過任何電腦。")
        return
    current = host.server.owner_spki() if host.server is not None else None
    for i, peer in enumerate(peers, 1):
        mark = "（連線中）" if peer.spki == current else ""
        out(f"  {i}) {peer.name}  {fingerprint(peer.spki)}  {peer.paired_at}{mark}")


def manage_peers(host: ComputeHost, input_fn: InputFn, out: OutFn) -> None:
    """列出已配對的電腦，可以輸入編號移除一台。"""
    list_peers(host, out)
    peers = host.trust.peers()
    if not peers:
        return
    try:
        answer = input_fn("輸入編號移除那台電腦（空白返回）：").strip()
    except (EOFError, KeyboardInterrupt):
        return
    if not answer:
        return
    if not (answer.isdigit() and 1 <= int(answer) <= len(peers)):
        out(f"無效的選項：{answer!r}")
        return
    peer = peers[int(answer) - 1]
    try:
        host.trust.remove(peer.spki)
    except OSError as exc:
        logger.error("cannot update the trust list: %s", exc)
        out(f"無法移除：{exc}")
        return
    host.trust_changed()  # 正在連線的話立刻斷開
    out(f"已移除「{peer.name}」，它要重新配對才能連線。")


# --------------------------------------------------------------------------- 準備模型


class _EngineDriver:
    """在同一個行程裡跑一個引擎，用 GUI 會送的請求準備模型（不另寫一份下載邏輯）。"""

    def __init__(self, out: OutFn) -> None:
        from kikitori.engine.io import MessageWriter
        from kikitori.engine.server import EngineServer
        from kikitori.engine.services import default_services

        self._out = out
        self._cond = threading.Condition()
        self._buffer = b""
        self._responses: dict[int, dict[str, Any]] = {}
        self._downloads: dict[str, dict[str, Any]] = {}
        self._next_id = 0
        services = default_services()
        # 與 EngineServer.serve() 一樣先讀設定檔：沒載入的 store 是預設值，接著的
        # settings.apply_recommendation 會以它為底存檔，把整份設定蓋掉
        warning = services.settings_store.load()
        if warning:
            logger.warning("settings: %s", warning)
        self._engine = EngineServer(services, MessageWriter(self))  # type: ignore[arg-type]

    # MessageWriter 的 stream 介面
    def write(self, data: bytes) -> int:
        with self._cond:
            self._buffer += data
            *lines, self._buffer = self._buffer.split(b"\n")
            for line in lines:
                if line:
                    self._on_message(json.loads(line))
            self._cond.notify_all()
        return len(data)

    def flush(self) -> None:
        """MessageWriter 每則訊息後呼叫；沒有緩衝要清。"""

    def _on_message(self, message: dict[str, Any]) -> None:
        if "id" in message and message["id"] is not None:
            self._responses[int(message["id"])] = message
        elif message.get("event") == "download_progress":
            data = message["data"]
            self._downloads[str(data["model"])] = data

    def request(self, method: str, params: Mapping[str, Any] | None = None) -> Any:
        """送一個請求並等回應。

        Raises:
            RuntimeError: 引擎回錯誤或逾時。
        """
        with self._cond:
            self._next_id += 1
            request_id = self._next_id
        message: dict[str, Any] = {"id": request_id, "method": method}
        if params is not None:
            message["params"] = dict(params)
        self._engine.handle_line(json.dumps(message).encode())
        with self._cond:
            if not self._cond.wait_for(
                lambda: request_id in self._responses, REQUEST_TIMEOUT
            ):
                raise RuntimeError(f"{method}: no reply in {REQUEST_TIMEOUT:.0f} s")
            reply = self._responses.pop(request_id)
        if "error" in reply:
            error = reply["error"]
            raise RuntimeError(f"{method}: {error.get('code')}: {error.get('message')}")
        return reply["result"]

    def download(self, method: str, params: Mapping[str, Any], model: str) -> bool:
        """開始下載並等到結束，途中顯示進度；回傳是否成功。"""
        with self._cond:
            self._downloads.pop(model, None)
        self.request(method, params)
        last = -1
        deadline = time.monotonic() + DOWNLOAD_TIMEOUT
        with self._cond:
            while True:
                if time.monotonic() > deadline:
                    raise RuntimeError(
                        f"{model}: no result in {DOWNLOAD_TIMEOUT:.0f} s"
                    )
                self._cond.wait(1.0)
                data = self._downloads.get(model)
                if data is None:
                    continue
                total = int(data.get("total_bytes") or 0)
                done = int(data.get("downloaded_bytes") or 0)
                percent = done * 100 // total if total else 0
                if data.get("state") == "downloading":
                    if percent >= last + 10:
                        last = percent
                        self._out(f"  {model}：{percent}%")
                    continue
                if data.get("state") == "done":
                    self._out(f"  {model}：完成")
                    return True
                self._out(f"  {model}：失敗（{data.get('error')}）")
                return False

    def close(self) -> None:
        """關閉引擎（停止下載、釋放資源）。"""
        self._engine.close()


def prepare_models(
    host: ComputeHost,
    input_fn: InputFn,
    out: OutFn,
    driver_factory: Callable[[OutFn], Any] = _EngineDriver,
) -> bool:
    """依這台電腦的硬體套用建議的模型組合，並下載還沒有的模型；回傳是否全部就緒。"""
    out("偵測硬體並套用建議的模型組合…")
    driver = driver_factory(out)
    try:
        applied = driver.request("settings.apply_recommendation")
        asr = applied["settings"]["asr"]
        out(f"辨識模型：即時 {asr['fast_model']}、完整 {asr['final_model']}")
        models = driver.request("models.list")
        ok = True
        runtime = models.get("gpu_runtime") or {}
        if runtime.get("supported") and not runtime.get("installed"):
            ok = _offer_gpu_runtime(driver, runtime, input_fn, out) and ok
        downloaded = {m["id"] for m in models["asr"] if m["downloaded"]}
        for model in dict.fromkeys((asr["fast_model"], asr["final_model"])):
            if model not in downloaded:
                out(f"下載辨識模型 {model}…")
                ok = driver.download("models.download", {"model": model}, model) and ok
        # 說話者標籤由 A 決定（隨 session 送來），這台自己沒開也要準備好（28 MB）
        speaker = models.get("speaker_model") or {}
        if speaker and not speaker.get("installed"):
            model_id = str(speaker.get("id", "speaker-embedding"))
            out("下載說話者標籤模型…")
            ok = (
                driver.download("models.download", {"model": model_id}, model_id) and ok
            )
        ok = _pull_translation_models(driver, applied["settings"], models, out) and ok
    except (RuntimeError, KeyError, TypeError) as exc:
        logger.exception("preparing models failed")
        out(f"準備模型失敗：{exc}")
        return False
    finally:
        driver.close()
    out("模型都準備好了。" if ok else "有些模型沒有準備好，請看上面的訊息。")
    return ok


def _ask_yes(input_fn: InputFn, prompt: str) -> bool:
    try:
        return input_fn(prompt).strip().lower() == "y"
    except (EOFError, KeyboardInterrupt):
        return False


def choose_model(
    host: ComputeHost,
    input_fn: InputFn,
    out: OutFn,
    driver_factory: Callable[[OutFn], Any] = _EngineDriver,
) -> bool:
    """列出每個辨識模型在這台跑不跑得動，換完整辨識模型（`asr.final_model`）；回傳是否有換。

    即時辨識模型不變（它決定字幕出現的速度，由「準備模型」依硬體設定）。選了還沒下載的模型
    先問要不要下載；選了超出負荷的再確認一次。
    """
    out("讀取這台電腦的顯示卡與模型…")
    info = host.capacity.host_info()
    gpu = info["gpu"]
    if gpu is not None:
        out(f"顯示卡：{gpu['name']}・{gpu['vram_total_mb'] / 1024:.0f} GB")
    elif info["device"] == "cpu":
        out("辨識在 CPU 上跑（沒有可用的 NVIDIA 顯示卡）。")
    memory = "顯示卡記憶體" if info["device"] == "cuda" else "記憶體"
    current = info["current"]
    recommended = (info["recommended"] or {}).get("final")
    out(f"即時辨識維持 {current['fast']}；這裡選的是完整辨識（定稿字幕）用的模型。")
    models: list[dict[str, Any]] = info["models"]
    for i, model in enumerate(models, 1):
        tags = [
            f"約 {model['need_mb'] / 1024:.1f} GB {memory}",
            _FIT_LABELS.get(model["fit"], "不確定跑不跑得動"),
            "已下載" if model["downloaded"] else "未下載",
        ]
        if model["id"] == recommended:
            tags.append("建議")
        if model["id"] == current["final"]:
            tags.append("目前")
        out(f"  {i}) {model['id']}  {'・'.join(tags)}")
    try:
        answer = input_fn("請輸入編號（空白取消）：").strip()
    except (EOFError, KeyboardInterrupt):
        return False
    if not answer:
        return False
    if not (answer.isdigit() and 1 <= int(answer) <= len(models)):
        out(f"無效的選項：{answer!r}")
        return False
    chosen = models[int(answer) - 1]
    model_id = str(chosen["id"])
    if chosen["fit"] == "over" and not _ask_yes(
        input_fn,
        f"{model_id} 可能超出這台電腦的負荷（字幕會跟不上），確定要用嗎？(y/N)：",
    ):
        out("沒有變更。")
        return False
    if not chosen["downloaded"]:
        if not _ask_yes(input_fn, f"{model_id} 還沒下載，要現在下載嗎？(y/N)："):
            out("沒有變更。")
            return False
        if not _download_model(model_id, out, driver_factory):
            out("下載失敗，沒有變更。")
            return False
    try:
        host.store.update({"asr": {"final_model": model_id}})
    except (OSError, SettingsError) as exc:
        logger.exception("cannot save the ASR model")
        out(f"無法儲存：{exc}")
        return False
    out(f"已設定完整辨識模型：{model_id}（下一場字幕開始生效）。")
    return True


def _download_model(
    model_id: str, out: OutFn, driver_factory: Callable[[OutFn], Any]
) -> bool:
    out(f"下載辨識模型 {model_id}…")
    driver = driver_factory(out)
    try:
        return bool(driver.download("models.download", {"model": model_id}, model_id))
    except RuntimeError as exc:
        logger.exception("downloading %s failed", model_id)
        out(f"下載失敗：{exc}")
        return False
    finally:
        driver.close()


def _offer_gpu_runtime(
    driver: Any, runtime: Mapping[str, Any], input_fn: InputFn, out: OutFn
) -> bool:
    out("這台電腦有 NVIDIA 顯示卡，但還沒有 GPU 加速元件（NVIDIA cuBLAS）。")
    out(f"使用前要同意 NVIDIA 的授權條款：{runtime.get('license_url')}")
    try:
        answer = input_fn("同意並下載嗎？(y/N)：").strip().lower()
    except (EOFError, KeyboardInterrupt):
        return False
    if answer != "y":
        out("略過 GPU 加速元件：辨識會在 CPU 上跑，比較慢。")
        return False
    model_id = str(runtime.get("id"))
    return bool(
        driver.download(
            "models.download", {"model": model_id, "accept_license": True}, model_id
        )
    )


def _pull_translation_models(
    driver: Any,
    settings: Mapping[str, Any],
    models: Mapping[str, Any],
    out: OutFn,
) -> bool:
    translation = settings["translation"]
    if not translation["enabled"]:
        return True
    installed = models["translation"].get("installed")
    if installed is None:
        out(
            "連不上這台電腦的翻譯伺服器（Ollama）：請先安裝並啟動 Ollama，"
            "之後再執行一次「準備模型」。"
        )
        return False
    have = {normalize_model_name(str(name)) for name in installed}
    ok = True
    for model in dict.fromkeys((translation["fast_model"], translation["final_model"])):
        if normalize_model_name(str(model)) in have:
            continue
        out(f"用 Ollama 下載翻譯模型 {model}…")
        ok = driver.download("models.pull_translation", {"model": model}, model) and ok
    return ok
