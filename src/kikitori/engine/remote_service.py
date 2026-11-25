"""引擎的遠端運算功能：`remote.*` 方法背後的狀態（docs/specs/remote-compute.md「T13 契約」）。

位於 engine 層，介於 stdio 協定（`engine.server`）與 `kikitori.remote` 之間：

- **B 的角色**：在引擎行程裡開關 `RemoteServer`（GPU 使用權與本機 session 共用，由
  `EngineServer` 提供 `acquire_gpu`／`release_gpu`）、開配對窗口、管理允許連進來的電腦。
- **A 的角色**：連到運算主機的配對埠、管理配對過的運算主機、查詢運算主機的能力
  （`remote.host_info`，docs/specs/compute-capacity.md「四」），以及改運算主機的模型、請它
  下載（`remote.host_set_models`／`remote.host_download`，「七」）。遠端 session 本身在
  `EngineServer._load_remote_session`。
- B 的能力（`welcome.host`）由 `remote.capacity.HostCapacity` 組出，用引擎的硬體偵測與模型清單。
- 別台改這台的模型時（`_EngineHostManager`）：寫這台的設定檔、下載走引擎既有的下載機制
  （這台的介面照樣收到 `download_progress`），並送 `remote` 事件 `host_changed`。
- 提供運算時每秒（沒有電腦連著時每 5 秒、狀態改變時立即）送 `remote` 事件 `serving`：
  正在替誰處理、這場處理了幾句、這台的負荷（「八」；只有句數，不含別人的字幕內容）。

配對狀態與伺服器狀態以 `remote` 事件送給介面。所有方法都可以在 stdio 請求執行緒上呼叫；
開伺服器或配對埠只是開 socket，不會阻塞太久。例外是會連線運算主機的 `host_info()`、
`host_set_models()`、`start_host_download()`（最多十幾秒），引擎在背景工作執行緒上呼叫它們。
"""

import ipaddress
import logging
import socket
import threading
from collections.abc import Callable, Collection, Mapping
from typing import Any

from kikitori.config import Settings, SettingsError, SettingsStore
from kikitori.engine import protocol as p
from kikitori.hardware import HardwareInfo
from kikitori.pipeline.types import PipelineComponents
from kikitori.remote import protocol as rp
from kikitori.remote.capacity import HostCapacity
from kikitori.remote.client import (
    MANAGE_TIMEOUT,
    PROBE_TIMEOUT,
    HostDownload,
    RemoteError,
    probe_host_info,
    set_host_models,
)
from kikitori.remote.host import fingerprint, local_addresses
from kikitori.remote.identity import (
    Identity,
    IdentityError,
    load_or_create_identity,
    reset_identity,
)
from kikitori.remote.manage import (
    SETTINGS_INVALID_DETAIL,
    SETTINGS_SAVE_DETAIL,
    HostManageError,
    ProgressFn,
)
from kikitori.remote.network import is_allowed_listen_host
from kikitori.remote.pair_net import (
    TERMINAL_STATES,
    PairingClient,
    PairingServer,
    PairingUpdate,
    normalize_digits,
    pairing_port,
)
from kikitori.remote.pairing import PairingError, TrustedPeer, TrustStore
from kikitori.remote.paths import compute_dir, servers_path, trusted_path

logger = logging.getLogger(__name__)

REMOTE_NO_LISTEN_HOST = "remote_no_listen_host"
REMOTE_PORT_IN_USE = "remote_port_in_use"
REMOTE_PAIRING_BUSY = "remote_pairing_busy"
REMOTE_PAIRING_CLOSED = "remote_pairing_closed"
REMOTE_IDENTITY_ERROR = "remote_identity_error"
REMOTE_NOT_FOUND = "remote_not_found"
PAIRING_JOIN = 5.0
HOST_INFO_TIMEOUT = PROBE_TIMEOUT
"""`remote.host_info` 最多等運算主機多久（秒）。"""
HOST_SET_MODELS_TIMEOUT = MANAGE_TIMEOUT
"""`remote.host_set_models`、`remote.host_download`（到 B 開始下載為止）最多等多久（秒）。"""
SERVING_SECS = 1.0
"""有電腦連著（或它的 session 等待續接）時 `serving` 事件的間隔。"""
SERVING_IDLE_SECS = 5.0
"""提供運算、但沒有電腦連著時 `serving` 事件的間隔。"""
FOLLOW_JOIN = 5.0

SendEvent = Callable[[str, Mapping[str, Any]], None]
ComponentBuilder = Callable[..., PipelineComponents]
StartDownload = Callable[[str, ProgressFn], None]
"""`(model, progress)`：用引擎的下載機制在背景下載 ASR 模型（`EngineServer`）。

Raises:
    HostManageError: `unknown_model`、`download_in_progress`。
"""


class _EngineHostManager:
    """別台電腦改這台的模型（`remote.manage.HostManager`，GUI 引擎的實作）。"""

    def __init__(
        self, store: SettingsStore, send_event: SendEvent, start_download: StartDownload
    ) -> None:
        self._store = store
        self._send_event = send_event
        self._start_download = start_download

    def set_models(
        self, by: str, asr: Mapping[str, str], translation: Mapping[str, str]
    ) -> None:
        """寫進設定檔（下一場生效），送 `host_changed` 讓這台的介面重讀設定並通知使用者。

        設定檔壞掉（引擎以預設值運作）時不寫：「預設值＋那台的修改」會蓋掉這台使用者原本的
        設定，要由這台的使用者自己處理。回給那台的 `detail` 是固定字句（例外原文含路徑）。
        """
        if self._store.file_invalid:
            logger.warning(
                "%s asked to change the models, but the settings file is invalid", by
            )
            raise HostManageError("settings_error", SETTINGS_INVALID_DETAIL)
        patch: dict[str, Any] = {}
        if asr:
            patch["asr"] = dict(asr)
        if translation:
            patch["translation"] = dict(translation)
        try:
            settings = self._store.update(patch)
        except (SettingsError, OSError) as exc:
            logger.error("cannot save the models %s chose: %s", by, exc)
            raise HostManageError("settings_error", SETTINGS_SAVE_DETAIL) from exc
        try:
            self._send_event(
                "remote",
                {
                    "kind": "host_changed",
                    "by": by,
                    "asr": {
                        "fast": settings.asr.fast_model,
                        "final": settings.asr.final_model,
                    },
                    "translation": {
                        "fast": settings.translation.fast_model,
                        "final": settings.translation.final_model,
                    },
                },
            )
        except Exception:
            # 設定已經存好：通知失敗（例如 stdout 已關）不能讓 A 以為沒套用
            logger.exception("cannot report the models another computer chose")

    def start_download(self, by: str, model: str, progress: ProgressFn) -> None:
        """走引擎的下載機制（這台的介面也看得到進度）。"""
        self._start_download(model, progress)


def _check_set_models(params: dict[str, Any]) -> rp.HostSetModels:
    """`remote.host_set_models` 的參數（格式與 B 收到的 `host.set_models` 相同）。"""
    p.reject_unknown_params(params, ("asr", "translation"))
    try:
        return rp.check_set_models(params.get("asr"), params.get("translation"))
    except rp.ProtocolViolation as exc:
        raise p.ProtocolError(p.INVALID_PARAMS, str(exc)) from exc


def _check_download(params: dict[str, Any]) -> str:
    p.reject_unknown_params(params, ("model",))
    try:
        return rp.check_download_model(params.get("model"))
    except rp.ProtocolViolation as exc:
        raise p.ProtocolError(p.INVALID_PARAMS, str(exc)) from exc


def _entry(peer: TrustedPeer) -> dict[str, Any]:
    return {
        "spki": peer.spki.hex(),
        "name": peer.name,
        "fingerprint": fingerprint(peer.spki),
        "paired_at": peer.paired_at,
    }


def _check_listen_host(value: object) -> None:
    """`server_set` 的 `listen_host`：是 IP 但不能監聽（所有介面、公用位址）時回 `invalid_params`。

    不是 IP 的字串交給設定驗證（`invalid_settings`），空字串表示清除。
    """
    if not isinstance(value, str) or not value.strip():
        return
    try:
        ipaddress.ip_address(value.strip())
    except ValueError:
        return
    if not is_allowed_listen_host(value.strip()):
        raise p.ProtocolError(
            p.INVALID_PARAMS,
            "listen_host must be a private-network or Tailscale address of this computer",
        )


class RemoteService:
    """`remote.*` 方法的實作；一個引擎一個實例。"""

    def __init__(
        self,
        *,
        store: SettingsStore,
        send_event: SendEvent,
        build_components: ComponentBuilder,
        acquire_gpu: Callable[[], bool],
        release_gpu: Callable[[], None],
        app_version: str,
        detect_hardware: Callable[[Settings], HardwareInfo],
        downloaded_models: Callable[[], Collection[str]],
        start_download: StartDownload,
    ) -> None:
        """
        Args:
            store: 設定。
            send_event: 送 `remote` 事件給介面。
            build_components: 替別台運算時建立 pipeline 元件。
            acquire_gpu: 取得 GPU 使用權（與本機 session 共用）。
            release_gpu: 歸還 GPU 使用權。
            app_version: 握手時告訴對方的版本。
            detect_hardware: 硬體偵測（替別台運算時告訴它這台的顯示卡與建議）。
            downloaded_models: 這台已下載的 ASR 模型 id（別台改這台的模型時檢查）。
            start_download: 別台請這台下載 ASR 模型時，用引擎的下載機制開始下載。
        """
        self._store = store
        self._send_event = send_event
        self._build_components = build_components
        self._acquire_gpu = acquire_gpu
        self._release_gpu = release_gpu
        self._app_version = app_version
        # 引擎一個、跨伺服器重開都沿用：偵測快取 30 秒（compute-capacity 二）
        self._capacity = HostCapacity(
            store.get, detect=detect_hardware, downloaded=downloaded_models
        )
        self._manager = _EngineHostManager(store, send_event, start_download)
        self._lock = threading.Lock()
        self._identity: Identity | None = None
        self._identity_error: str | None = None
        self._server: Any = None  # RemoteServer（延後載入）
        self._server_error: str | None = None
        self._pairing: PairingServer | PairingClient | None = None
        self._pairing_role: str | None = None
        self._pairing_state: dict[str, Any] | None = None
        # serving 事件（compute-capacity 八）：伺服器開著時一條執行緒；狀態改變時叫醒它
        self._serving_stop: threading.Event | None = None
        self._serving_wake = threading.Event()
        self._serving_thread: threading.Thread | None = None
        # A：還在追蹤進度的「在運算主機下載」（引擎結束時中斷）
        self._host_downloads: set[HostDownload] = set()

    @property
    def trust(self) -> TrustStore:
        """B：允許連進來的電腦。"""
        return TrustStore(trusted_path())

    @property
    def servers(self) -> TrustStore:
        """A：配對過的運算主機。"""
        return TrustStore(servers_path())

    def identity(self) -> Identity:
        """這台電腦的憑證（第一次用時產生）。

        Raises:
            p.ProtocolError: 讀不到（`remote_identity_error`：要使用者明確重設，不自動重建——
                那會讓所有配對失效）。
        """
        with self._lock:
            if self._identity is not None:
                return self._identity
            try:
                self._identity = load_or_create_identity(compute_dir())
                self._identity_error = None
            except (IdentityError, OSError) as exc:
                # OSError（權限、防毒鎖檔）也回報在這裡：讓 remote.status 照常回應、引擎照常
                # 啟動，使用者才看得到原因並能「重建這台電腦的身分」
                logger.error("cannot read this computer's remote identity: %s", exc)
                self._identity_error = str(exc)
                raise p.ProtocolError(REMOTE_IDENTITY_ERROR, str(exc)) from exc
            return self._identity

    # --- 狀態 -------------------------------------------------------------------

    def status(self) -> dict[str, Any]:
        """`remote.status`。"""
        try:
            identity: dict[str, Any] | None = {
                "fingerprint": fingerprint(self.identity().spki)
            }
        except p.ProtocolError:
            identity = None
        compute = self._store.get().compute
        with self._lock:
            server = self._server
            error = self._server_error
            pairing = None if self._pairing_state is None else dict(self._pairing_state)
            identity_error = self._identity_error
        return {
            "identity": identity,
            "identity_error": identity_error,
            "server": {
                "enabled": compute.server_enabled,
                "listening": server is not None,
                "listen_host": compute.listen_host,
                "port": compute.listen_port,
                "error": error,
                "peer": self._peer_info(server),
            },
            "addresses": local_addresses(),
            "peers": [_entry(peer) for peer in self.trust.peers()],
            "servers": [_entry(peer) for peer in self.servers.peers()],
            "pairing": pairing,
        }

    def _peer_info(self, server: Any) -> dict[str, Any] | None:
        if server is None:
            return None
        spki = server.owner_spki()
        state = server.peer_state()
        if spki is None or state is None:
            return None
        name = next(
            (peer.name for peer in self.trust.peers() if peer.spki == spki), "?"
        )
        return {"name": name, "fingerprint": fingerprint(spki), "state": state}

    def _server_event(self) -> None:
        self._serving_wake.set()  # 連線的電腦或它的 session 變了：serving 事件立刻送一次
        with self._lock:
            server = self._server
            error = self._server_error
        try:
            self._send_event(
                "remote",
                {
                    "kind": "server",
                    "listening": server is not None,
                    "peer": self._peer_info(server),
                    "error": error,
                },
            )
        except Exception:
            # 通知失敗（例如 stdout 已關）不能影響連線
            logger.exception("cannot report the remote server state")

    # --- 伺服器（B） ---------------------------------------------------------------

    def start_if_enabled(self) -> None:
        """引擎啟動時：設定裡開著就開始監聽（失敗記在 `server.error`，不讓引擎失敗）。"""
        if not self._store.get().compute.server_enabled:
            return
        try:
            self._start_server(self._store.get())
        except p.ProtocolError as exc:
            logger.warning("cannot start the remote compute server: %s", exc.message)

    def server_set(self, params: dict[str, Any]) -> dict[str, Any]:
        """`remote.server_set`。"""
        p.reject_unknown_params(params, ("enabled", "listen_host", "port"))
        enabled = params.get("enabled")
        if not isinstance(enabled, bool):
            raise p.ProtocolError(p.INVALID_PARAMS, "enabled must be true or false")
        patch: dict[str, Any] = {"server_enabled": enabled}
        if enabled:
            # 角色「讓別台電腦用這台處理」：這台自己用時就是本機運算，不會同時把聲音送去別台
            patch["mode"] = "local"
        if "listen_host" in params:
            _check_listen_host(params["listen_host"])
            patch["listen_host"] = params["listen_host"]
        if "port" in params:
            patch["listen_port"] = params["port"]  # 契約的 `port` 是監聽埠
        try:
            settings = self._store.update({"compute": patch})
        except SettingsError as exc:
            raise p.ProtocolError(p.INVALID_SETTINGS, str(exc)) from exc
        except OSError as exc:
            logger.error("cannot save settings: %s", exc)
            raise p.ProtocolError(
                p.SETTINGS_IO_ERROR, f"cannot save settings: {exc}"
            ) from exc
        self.stop_server()
        if enabled:
            self._start_server(settings)
        return self.status()

    def _start_server(self, settings: Settings) -> None:
        from kikitori.remote.server import RemoteServer

        compute = settings.compute
        if not compute.listen_host:
            self._set_server_error("no listen address is set")
            raise p.ProtocolError(
                REMOTE_NO_LISTEN_HOST, "choose which address to listen on first"
            )
        if not is_allowed_listen_host(compute.listen_host):
            # 手改設定檔也擋：只監聽區網或 Tailscale 的單一位址
            message = (
                f"{compute.listen_host} is not a private-network or Tailscale address"
            )
            self._set_server_error(message)
            raise p.ProtocolError(p.INVALID_SETTINGS, message)
        server = RemoteServer(
            identity=self.identity(),
            trust=self.trust,
            settings=self._store.get,
            build_components=self._build_components,
            acquire_gpu=self._acquire_gpu,
            release_gpu=self._release_gpu,
            host=compute.listen_host,
            port=compute.listen_port,
            app_version=self._app_version,
            on_change=self._server_event,
            capacity=self._capacity,
            manager=self._manager,
        )
        try:
            server.start()
        except OSError as exc:
            self._set_server_error(
                f"cannot listen on {compute.listen_host}:{compute.listen_port}: {exc}"
            )
            raise p.ProtocolError(
                REMOTE_PORT_IN_USE,
                f"cannot listen on {compute.listen_host}:{compute.listen_port}: {exc}",
            ) from exc
        with self._lock:
            self._server = server
            self._server_error = None
        self._server_event()
        # 在通知之後才開始：迴圈的第一則就是「開始提供運算」這次狀態改變，不會連送兩則
        self._start_serving_events(server)

    # --- serving 事件（B 的畫面，compute-capacity 八） --------------------------------

    def _start_serving_events(self, server: Any) -> None:
        stop = threading.Event()
        thread = threading.Thread(
            target=self._serving_loop,
            args=(server, stop),
            name="remote-serving-events",
            daemon=True,
        )
        with self._lock:
            self._serving_stop = stop
            self._serving_thread = thread
        thread.start()

    def _stop_serving_events(self) -> None:
        with self._lock:
            stop, self._serving_stop = self._serving_stop, None
            thread, self._serving_thread = self._serving_thread, None
        if stop is not None:
            stop.set()
            self._serving_wake.set()
        if thread is not None and thread is not threading.current_thread():
            thread.join(FOLLOW_JOIN)

    def _serving_loop(self, server: Any, stop: threading.Event) -> None:
        """有電腦連著時每秒、沒有時每 5 秒送一次；`_server_event` 叫醒時立刻送。"""
        while not stop.is_set():
            self._serving_wake.clear()
            try:
                data = self._serving_data(server)
                if stop.is_set():
                    return
                self._send_event("remote", data)
            except Exception:
                # 只是給畫面的資訊：出錯（例如 stdout 已關）不能影響提供運算
                logger.exception("cannot report what this computer is serving")
                return
            busy = data["peer"] is not None or data["session"] is not None
            self._serving_wake.wait(SERVING_SECS if busy else SERVING_IDLE_SECS)

    def _serving_data(self, server: Any) -> dict[str, Any]:
        """`{kind: serving, peer, session, metrics}`；不含字幕內容（只有句數）。"""
        return {
            "kind": "serving",
            "peer": self._peer_info(server),
            "session": server.session_info(),
            "metrics": server.current_load(),
        }

    def _set_server_error(self, message: str) -> None:
        with self._lock:
            self._server_error = message
        self._server_event()

    def stop_server(self) -> None:
        """停止監聽（進行中的遠端 session 一併結束）。"""
        with self._lock:
            server, self._server = self._server, None
            self._server_error = None
        self._stop_serving_events()
        if server is not None:
            server.stop()
            self._server_event()

    def invalidate_capacity(self) -> None:
        """硬體或翻譯模型變了（GPU 加速元件、拉完翻譯模型）：下次回答 A 時重新偵測。"""
        self._capacity.invalidate()

    def session_models(self) -> tuple[str, ...]:
        """替別台運算的那一場載入的 ASR 模型；沒有時空的。

        是那場開始時這台設定的模型：場中別台可以改這台的設定（下一場生效），所以不一定是現在的。
        """
        with self._lock:
            server = self._server
        return () if server is None else tuple(server.session_models())

    def disconnect(self, params: dict[str, Any]) -> dict[str, Any]:
        """`remote.disconnect`：斷開正在使用這台電腦的那台（B 的使用者拿回 GPU）。"""
        p.reject_unknown_params(params)
        with self._lock:
            server = self._server
        return {"disconnected": bool(server is not None and server.disconnect())}

    # --- 運算主機的能力（A） ---------------------------------------------------------

    def host_info(self) -> dict[str, Any]:
        """`remote.host_info`：用 `compute.host`／`compute.port` 查運算主機的能力（不開始字幕）。

        會連線（最多 `HOST_INFO_TIMEOUT` 秒），要在背景工作執行緒上呼叫。

        Returns:
            `{"host": HostInfo | None}`；運算主機沒送能力（舊版）或內容不合格時 `host` 為 null。

        Raises:
            p.ProtocolError: `remote_not_configured`、`remote_unreachable`、`remote_not_paired`、
                `remote_incompatible`、`remote_identity_error`、`remote_error`（回應不合格）。
        """
        compute = self._store.get().compute
        if not compute.host:
            raise p.ProtocolError("remote_not_configured", "no compute host is set")
        identity = self.identity()
        try:
            host = probe_host_info(
                identity=identity,
                servers=self.servers,
                host=compute.host,
                port=compute.port,
                app_version=self._app_version,
                timeout=HOST_INFO_TIMEOUT,
            )
        except RemoteError as exc:
            logger.info("cannot ask the compute host what it can do: %s", exc)
            raise p.ProtocolError(exc.code, str(exc)) from exc
        return {"host": host}

    # --- 管理運算主機的模型（A，compute-capacity 七） ---------------------------------

    def _manage_target(self) -> tuple[Identity, str, int]:
        compute = self._store.get().compute
        if not compute.host:
            raise p.ProtocolError("remote_not_configured", "no compute host is set")
        return self.identity(), compute.host, compute.port

    @staticmethod
    def check_host_set_models(params: dict[str, Any]) -> rp.HostSetModels:
        """`remote.host_set_models` 的參數（不合格回 `invalid_params`）；連線前先在請求執行緒檢查。"""
        return _check_set_models(params)

    def host_set_models(self, request: rp.HostSetModels) -> dict[str, Any]:
        """`remote.host_set_models`：改運算主機設定檔裡的模型（連線，要在背景工作執行緒上呼叫）。

        Returns:
            `{"host": HostInfo | None}`：運算主機套用後的能力（fit 依新的組合重算）。

        Raises:
            p.ProtocolError: 同 `host_info()`，另加 `remote_unknown_model`、
                `remote_model_not_downloaded`、`remote_translation_model_missing`。
        """
        identity, host, port = self._manage_target()
        try:
            info = set_host_models(
                identity=identity,
                servers=self.servers,
                host=host,
                port=port,
                app_version=self._app_version,
                asr=request.asr,
                translation=request.translation,
                timeout=HOST_SET_MODELS_TIMEOUT,
            )
        except RemoteError as exc:
            logger.info("cannot change the compute host's models: %s", exc)
            raise p.ProtocolError(exc.code, str(exc)) from exc
        return {"host": info}

    @staticmethod
    def check_host_download(params: dict[str, Any]) -> str:
        """`remote.host_download` 的參數（模型 id 的格式）。"""
        return _check_download(params)

    def start_host_download(self, model: str) -> HostDownload:
        """請運算主機下載 `model`，等到它開始（連線，要在背景工作執行緒上呼叫）。

        Raises:
            p.ProtocolError: 連線錯誤、`remote_unknown_model`、`remote_download_in_progress`、
                `remote_error`。
        """
        identity, host, port = self._manage_target()
        download = HostDownload(
            identity=identity,
            servers=self.servers,
            host=host,
            port=port,
            app_version=self._app_version,
            model=model,
            timeout=HOST_SET_MODELS_TIMEOUT,
        )
        try:
            download.start()
        except RemoteError as exc:
            logger.info("the compute host cannot download %s: %s", model, exc)
            raise p.ProtocolError(exc.code, str(exc)) from exc
        with self._lock:
            self._host_downloads.add(download)
        return download

    def follow_host_download(self, download: HostDownload) -> None:
        """在背景讀運算主機的下載進度，以 `remote` 事件 `host_download` 送給介面。"""
        threading.Thread(
            target=self._follow,
            args=(download,),
            name="remote-host-download",
            daemon=True,
        ).start()

    def drop_host_download(self, download: HostDownload) -> None:
        """不追蹤（`remote.host_download` 的回應沒送出）：中斷連線；運算主機照樣下載。"""
        download.cancel()
        with self._lock:
            self._host_downloads.discard(download)

    def _follow(self, download: HostDownload) -> None:
        def report(progress: dict[str, Any]) -> None:
            self._send_event("remote", {"kind": "host_download", **progress})

        try:
            download.follow(report)
        except Exception:
            # 送事件失敗（stdout 已關）或程式錯誤：記下來，運算主機的下載不受影響
            logger.exception("following the compute host's download failed")
        finally:
            with self._lock:
                self._host_downloads.discard(download)

    # --- 配對 -------------------------------------------------------------------

    def pair_open(self, params: dict[str, Any]) -> dict[str, Any]:
        """`remote.pair_open`（B）：開 5 分鐘的配對窗口。"""
        p.reject_unknown_params(params)
        compute = self._store.get().compute
        if not compute.listen_host:
            raise p.ProtocolError(
                REMOTE_NO_LISTEN_HOST, "choose which address to listen on first"
            )
        identity = self.identity()
        with self._lock:
            if self._pairing is not None:
                raise p.ProtocolError(REMOTE_PAIRING_BUSY, "a pairing is in progress")
            holder: list[PairingServer | PairingClient] = []
            server = PairingServer(
                identity=identity,
                trust=self.trust,
                name=socket.gethostname(),
                host=compute.listen_host,
                port=pairing_port(compute.listen_port),
                on_update=lambda update: self._on_pairing("server", update, holder),
            )
            holder.append(server)
            self._pairing = server
            self._pairing_role = "server"
        try:
            server.open()
        except OSError as exc:
            self._clear_pairing(server)
            raise p.ProtocolError(
                REMOTE_PORT_IN_USE,
                f"cannot open the pairing port {pairing_port(compute.listen_port)}: {exc}",
            ) from exc
        return self.status()

    def pair(self, params: dict[str, Any]) -> dict[str, Any]:
        """`remote.pair`（A）：連到運算主機的配對埠。"""
        p.reject_unknown_params(params, ("host", "port"))
        host = params.get("host")
        port = params.get("port", self._store.get().compute.port)
        if not isinstance(host, str) or not host.strip():
            raise p.ProtocolError(p.INVALID_PARAMS, "host must be a non-empty string")
        if (
            isinstance(port, bool)
            or not isinstance(port, int)
            or not 1024 <= port <= 65534
        ):
            raise p.ProtocolError(p.INVALID_PARAMS, "port must be in [1024, 65534]")
        identity = self.identity()
        host = host.strip()
        with self._lock:
            if self._pairing is not None:
                raise p.ProtocolError(REMOTE_PAIRING_BUSY, "a pairing is in progress")
            try:
                holder: list[PairingServer | PairingClient] = []
                client = PairingClient(
                    identity=identity,
                    trust=self.servers,
                    name=socket.gethostname(),
                    host=host,
                    port=pairing_port(port),
                    on_update=lambda update: self._on_pairing("client", update, holder),
                    label=host,
                )
                holder.append(client)
            except ValueError as exc:
                raise p.ProtocolError(p.INVALID_PARAMS, str(exc)) from exc
            self._pairing = client
            self._pairing_role = "client"
        client.start()
        return {}

    def pair_confirm(self, params: dict[str, Any]) -> dict[str, Any]:
        """`remote.pair_confirm`：B 輸入數字（`digits`）或 A 確認（`same`）。"""
        p.reject_unknown_params(params, ("digits", "same"))
        if ("digits" in params) == ("same" in params):
            raise p.ProtocolError(p.INVALID_PARAMS, "give exactly one of digits, same")
        with self._lock:
            pairing = self._pairing
        try:
            if "digits" in params:
                digits = params["digits"]
                if not isinstance(pairing, PairingServer) or not isinstance(
                    digits, str
                ):
                    raise p.ProtocolError(
                        REMOTE_PAIRING_CLOSED, "this computer is not waiting for digits"
                    )
                try:
                    normalize_digits(digits)
                except ValueError as exc:
                    raise p.ProtocolError(p.INVALID_PARAMS, str(exc)) from exc
                pairing.submit_digits(digits)
            else:
                same = params["same"]
                if not isinstance(same, bool):
                    raise p.ProtocolError(
                        p.INVALID_PARAMS, "same must be true or false"
                    )
                if not isinstance(pairing, PairingClient):
                    raise p.ProtocolError(
                        REMOTE_PAIRING_CLOSED, "this computer is not pairing"
                    )
                pairing.confirm(same)
        except PairingError as exc:
            raise p.ProtocolError(REMOTE_PAIRING_CLOSED, str(exc)) from exc
        return {}

    def pair_cancel(self, params: dict[str, Any]) -> dict[str, Any]:
        """`remote.pair_cancel`。"""
        p.reject_unknown_params(params)
        with self._lock:
            pairing = self._pairing
        if pairing is not None:
            pairing.cancel()
        return {}

    def _on_pairing(
        self,
        role: str,
        update: PairingUpdate,
        source: list[PairingServer | PairingClient],
    ) -> None:
        state = {
            "role": role,
            "state": update.state,
            "digits": update.digits,
            "peer_name": update.peer_name,
            "code": update.code,
        }
        terminal = update.state in TERMINAL_STATES
        server = None
        with self._lock:
            pairing = self._pairing
            if not source or source[0] is not pairing:
                return  # 上一次配對晚到的通知：不能蓋掉進行中的這一次
            self._pairing_state = None if terminal else state
            if terminal:
                self._pairing = None
                self._pairing_role = None
                server = self._server
        if (
            terminal
            and update.state == "done"
            and role == "server"
            and server is not None
        ):
            server.trust_changed()  # 新配對的電腦立刻能連
        try:
            self._send_event("remote", {"kind": "pairing", **state})
        except Exception:
            logger.exception("cannot report the pairing state")
        if terminal and pairing is not None:
            threading.Thread(
                target=pairing.join, args=(PAIRING_JOIN,), daemon=True
            ).start()

    def _clear_pairing(self, pairing: PairingServer | PairingClient) -> None:
        with self._lock:
            if self._pairing is pairing:
                self._pairing = None
                self._pairing_role = None
                self._pairing_state = None

    def unpair(self, params: dict[str, Any]) -> dict[str, Any]:
        """`remote.unpair`：移除一台允許連進來的電腦（`peer`）或配對過的運算主機（`server`）。"""
        p.reject_unknown_params(params, ("spki", "role"))
        spki_hex, role = params.get("spki"), params.get("role")
        if role not in ("peer", "server"):
            raise p.ProtocolError(p.INVALID_PARAMS, "role must be peer or server")
        try:
            if not isinstance(spki_hex, str) or len(spki_hex) != 64:
                raise ValueError("bad length")
            spki = bytes.fromhex(spki_hex)
        except ValueError as exc:
            raise p.ProtocolError(
                p.INVALID_PARAMS, "spki must be 64 hexadecimal characters"
            ) from exc
        store = self.trust if role == "peer" else self.servers
        try:
            removed = store.remove(spki)
        except OSError as exc:
            logger.error("cannot update the trust list: %s", exc)
            raise p.ProtocolError(
                p.SETTINGS_IO_ERROR, f"cannot update the trust list: {exc}"
            ) from exc
        if not removed:
            raise p.ProtocolError(REMOTE_NOT_FOUND, "no such paired computer")
        if role == "peer":
            with self._lock:
                server = self._server
            if server is not None:
                server.trust_changed()  # 正在連線的話立刻斷開
        return self.status()

    def identity_reset(self, params: dict[str, Any]) -> dict[str, Any]:
        """`remote.identity_reset`：重建這台電腦的身分，忘掉所有配對。

        身分損毀（`identity_error`）時唯一的修復方式；好好的身分也可以重建（例如懷疑私鑰外流）。
        其他電腦記的是舊憑證，兩份配對清單都清空，配對要重做。原本在提供運算就用新身分
        繼續監聽（失敗記在 `server.error`，重建本身已完成）。本機有 session 時由
        `EngineServer` 先擋下（`session_active`）。
        """
        p.reject_unknown_params(params)
        self._end_pairing()
        self.stop_server()
        with self._lock:
            try:
                # 先清配對清單再換身分：清不掉時身分還是原本的，使用者可以安全重試
                self.trust.clear()
                self.servers.clear()
            except OSError as exc:
                logger.error("cannot clear the paired computers: %s", exc)
                raise p.ProtocolError(
                    p.SETTINGS_IO_ERROR, f"cannot clear the paired computers: {exc}"
                ) from exc
            try:
                self._identity = reset_identity(compute_dir())
            except (IdentityError, OSError) as exc:
                self._identity = None
                self._identity_error = f"cannot reset the identity: {exc}"
                logger.error("cannot reset the remote identity: %s", exc)
                raise p.ProtocolError(
                    p.SETTINGS_IO_ERROR, f"cannot reset the identity: {exc}"
                ) from exc
            self._identity_error = None
        logger.info("reset this computer's remote identity; every pairing is gone")
        settings = self._store.get()
        if settings.compute.server_enabled:
            try:
                self._start_server(settings)
            except p.ProtocolError as exc:
                logger.warning(
                    "cannot listen again after the identity reset: %s", exc.message
                )
        else:
            self._server_event()
        return self.status()

    def _end_pairing(self) -> None:
        with self._lock:
            pairing = self._pairing
        if pairing is not None:
            pairing.cancel()
            pairing.join(PAIRING_JOIN)
            self._clear_pairing(pairing)

    def close(self) -> None:
        """引擎結束：取消配對、停止伺服器、不再追蹤運算主機的下載。"""
        self._end_pairing()
        self.stop_server()
        with self._lock:
            downloads = list(self._host_downloads)
            self._host_downloads.clear()
        for download in downloads:
            download.cancel()
