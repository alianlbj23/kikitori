"""引擎的遠端運算功能：`remote.*` 方法背後的狀態（docs/specs/remote-compute.md「T13 契約」）。

位於 engine 層，介於 stdio 協定（`engine.server`）與 `kikitori.remote` 之間：

- **B 的角色**：在引擎行程裡開關 `RemoteServer`（GPU 使用權與本機 session 共用，由
  `EngineServer` 提供 `acquire_gpu`／`release_gpu`）、開配對窗口、管理允許連進來的電腦。
- **A 的角色**：連到運算主機的配對埠、管理配對過的運算主機。遠端 session 本身在
  `EngineServer._load_remote_session`。

配對狀態與伺服器狀態以 `remote` 事件送給介面。所有方法都可以在 stdio 請求執行緒上呼叫；
開伺服器或配對埠只是開 socket，不會阻塞太久。
"""

import ipaddress
import logging
import socket
import threading
from collections.abc import Callable, Mapping
from typing import Any

from kikitori.config import Settings, SettingsError, SettingsStore
from kikitori.engine import protocol as p
from kikitori.pipeline.types import PipelineComponents
from kikitori.remote.host import fingerprint, local_addresses
from kikitori.remote.identity import (
    Identity,
    IdentityError,
    load_or_create_identity,
    reset_identity,
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

SendEvent = Callable[[str, Mapping[str, Any]], None]
ComponentBuilder = Callable[..., PipelineComponents]


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
    ) -> None:
        self._store = store
        self._send_event = send_event
        self._build_components = build_components
        self._acquire_gpu = acquire_gpu
        self._release_gpu = release_gpu
        self._app_version = app_version
        self._lock = threading.Lock()
        self._identity: Identity | None = None
        self._identity_error: str | None = None
        self._server: Any = None  # RemoteServer（延後載入）
        self._server_error: str | None = None
        self._pairing: PairingServer | PairingClient | None = None
        self._pairing_role: str | None = None
        self._pairing_state: dict[str, Any] | None = None

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

    def _set_server_error(self, message: str) -> None:
        with self._lock:
            self._server_error = message
        self._server_event()

    def stop_server(self) -> None:
        """停止監聽（進行中的遠端 session 一併結束）。"""
        with self._lock:
            server, self._server = self._server, None
            self._server_error = None
        if server is not None:
            server.stop()
            self._server_event()

    def disconnect(self, params: dict[str, Any]) -> dict[str, Any]:
        """`remote.disconnect`：斷開正在使用這台電腦的那台（B 的使用者拿回 GPU）。"""
        p.reject_unknown_params(params)
        with self._lock:
            server = self._server
        return {"disconnected": bool(server is not None and server.disconnect())}

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
        """引擎結束：取消配對、停止伺服器。"""
        self._end_pairing()
        self.stop_server()
