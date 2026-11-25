"""遠端運算的配對連線：B 的配對埠（`PairingServer`）與 A 的配對客戶端（`PairingClient`）。

位於 remote 模組的配對連線層（docs/specs/remote-compute.md「配對時序」與「設計審查」第 4、12–14 點）。
`pairing.py` 只有比對數字的邏輯；這裡把它放進 TLS 1.3 上的 WebSocket，交換四則 JSON 文字訊息
（位元組一律是小寫十六進位，長度固定；未知欄位、重複欄位、順序不對都直接中止）：

    A → B  pair.commit {cert_pem, name, commit}   A 的憑證**只能**出現在這一則
    B → A  pair.nonce  {nonce, name}
    A → B  pair.reveal {nonce, sig}               sig：A 的私鑰對 POP 訊息的 ECDSA-SHA256（r‖s）
    B → A  pair.result {ok: true}                 或任何時候 pair.abort {code}

- **B 不要求用戶端憑證**（Python 的 `ssl` 無法「要求但不驗證」），A 的憑證在 `pair.commit` 裡送，
  `sig` 證明 A 持有對應的私鑰；B 的金鑰由 TLS 握手證明。數字綁的是兩邊實際看到的 SPKI，中間人
  換掉任一邊數字就不同。憑證若能在 B 送出亂數之後才給，攻擊者就能試百萬把金鑰湊出相同數字。
- **B 的使用者要輸入 A 上顯示的數字**，不是按「確認」：區網上誰都能搶先連上配對埠，習慣性按確認
  就讓攻擊者配對成功。B 只開一個窗口、同時只收一個連線；窗口內出現第二個連線就全部中止。
- **只在雙方都確認後才寫入信任清單**（Sunshine CVE-2024-45407）：B 在數字相符時寫入；A 要收到
  `pair.result ok` **且**本機使用者確認「運算主機上顯示成功」才寫入（`pair.result` 中間人也能送）。
  B 寫入後 `pair.result` 沒送到 A 只會造成 B 單邊信任，無害：A 不信任 B、連不上，重新配對會
  覆蓋同一個 SPKI。
- 每一步網路逾時 10 秒、等使用者 120 秒；任何失敗都關閉配對埠。
- **log 不記**數字、亂數、金鑰與簽章。

兩個類別都給引擎從執行緒驅動：狀態以 `PairingUpdate` 回呼通知，終止狀態（done／failed）只會
通知一次，之後不再有任何回呼。
"""

import hmac
import ipaddress
import json
import logging
import re
import socket
import ssl
import threading
import time
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

from cryptography import x509
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import (
    decode_dss_signature,
    encode_dss_signature,
)
from websockets.exceptions import ConnectionClosed, InvalidHandshake, InvalidStatus
from websockets.http11 import Request, Response
from websockets.sync.client import ClientConnection, connect
from websockets.sync.server import Server, ServerConnection, serve

from kikitori.remote.identity import Identity, IdentityError, sign
from kikitori.remote.network import is_allowed_source
from kikitori.remote.pairing import (
    DIGITS,
    MAX_NAME_CHARS,
    NONCE_BYTES,
    ClientPairing,
    PairingError,
    PairingWindow,
    ServerPairing,
    TrustStore,
    _clean_name,
    _peer_certificate,
)
from kikitori.remote.sockets import (
    SafeClientConnection,
    SafeServerConnection,
    join_reader_before_close,
    shutdown_socket,
)

logger = logging.getLogger(__name__)

PATH = "/kikitori/pair/v1"
PORT_OFFSET = 1
"""配對埠 = 主埠 + 1（預設 47101）。"""
MAX_MESSAGE_BYTES = 8 * 1024
MAX_CERT_CHARS = 4 * 1024
"""自己產生的 P-256 憑證約 600 字元；上限擋掉塞大憑證耗資源。"""
STEP_TIMEOUT = 10.0
USER_TIMEOUT = 120.0
HANDSHAKE_TIMEOUT = 5.0
CLOSE_TIMEOUT = 2.0
COMMIT_BYTES = 32
SIGNATURE_BYTES = 64
"""P-256 簽章的 r‖s（各 32 位元組）：長度固定，比 DER 好驗證。"""
_POP_LABEL = b"kikitori-pair-pop-v1"
_POLL = 0.1
"""B 等使用者輸入時多久檢查一次連線（A 斷線就不必讓使用者白輸入）。"""
_WATCH = 0.2

# 失敗代碼（引擎轉成介面文字；B 送給 A 的只有 `WIRE_CODES`）
CODE_PAIRING_CLOSED = "pairing_closed"
"""B 沒有開配對窗口或已過期（A 連線被拒也算）。"""
CODE_CONCURRENT = "concurrent_pairing"
"""窗口內出現第二個配對連線：全部中止（可能有人搶先連線）。"""
CODE_MISMATCH = "mismatch"
"""B 的使用者輸入的數字與 B 算出的不同。"""
CODE_REJECTED = "rejected"
"""A 的使用者說運算主機上沒有顯示成功。"""
CODE_TIMEOUT = "timeout"
CODE_PROTOCOL = "protocol_error"
CODE_BAD_COMMITMENT = "bad_commitment"
CODE_BAD_SIGNATURE = "bad_signature"
CODE_BAD_CERTIFICATE = "bad_certificate"
CODE_CANCELLED = "cancelled"
CODE_CONNECTION_LOST = "connection_lost"
CODE_UNREACHABLE = "unreachable"
CODE_REFUSED = "refused"
"""B 在 WebSocket 升級前拒絕（來源不是私有網段或路徑不對）。"""
CODE_EXPIRED = "expired"
"""B 的窗口開滿 5 分鐘都沒有電腦來配對。"""
CODE_STORE_FAILED = "trust_store_error"
CODE_IDENTITY = "identity_error"
"""A 讀不到自己的憑證或私鑰（要使用者明確重設，不自動重產）。"""
CODE_INTERNAL = "internal_error"

WIRE_CODES = frozenset(
    {
        CODE_PAIRING_CLOSED,
        CODE_CONCURRENT,
        CODE_MISMATCH,
        CODE_TIMEOUT,
        CODE_PROTOCOL,
        CODE_BAD_COMMITMENT,
        CODE_BAD_SIGNATURE,
        CODE_BAD_CERTIFICATE,
        CODE_CANCELLED,
        CODE_STORE_FAILED,
        CODE_INTERNAL,
    }
)
"""B 可以在 `pair.abort` 告訴 A 的代碼；A 收到其他值一律當 `protocol_error`。"""

PairingState = Literal[
    "waiting", "enter_digits", "show_digits", "confirm", "done", "failed"
]
TERMINAL_STATES = frozenset({"done", "failed"})

_FIELDS: dict[str, frozenset[str]] = {
    "pair.commit": frozenset({"type", "cert_pem", "name", "commit"}),
    "pair.nonce": frozenset({"type", "nonce", "name"}),
    "pair.reveal": frozenset({"type", "nonce", "sig"}),
    "pair.result": frozenset({"type", "ok"}),
    "pair.abort": frozenset({"type", "code"}),
}
_HEX = re.compile(r"[0-9a-f]+")
_DIGITS = re.compile(rf"[0-9]{{{DIGITS}}}")
_HOSTNAME = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,62})(?:\.[A-Za-z0-9-]{1,63})*\.?")


@dataclass(frozen=True)
class PairingUpdate:
    """配對狀態的變化（給引擎轉成 `remote` 事件 `{kind: "pairing", ...}`）。

    Attributes:
        state: B：waiting → enter_digits → done／failed；
            A：waiting → show_digits → confirm → done／failed。
        digits: 只有 A 的 show_digits 帶（要顯示的 6 位數）；B 永遠不顯示自己的數字。
        peer_name: 對方自稱的名稱（已清理控制字元與長度）。
        code: failed 的原因（`CODE_*`）。
        spki: done 時對方憑證的 SPKI SHA-256（信任清單的鍵）。
    """

    state: PairingState
    digits: str | None = None
    peer_name: str | None = None
    code: str | None = None
    spki: bytes | None = None


OnUpdate = Callable[[PairingUpdate], None]


class _Abort(Exception):
    """配對中止；`code` 是 `CODE_*`。"""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


# --------------------------------------------------------------------------- 共用


def pairing_port(main_port: int) -> int:
    """主埠對應的配對埠（主埠 + 1）。

    Raises:
        ValueError: 主埠不在 1–65534。
    """
    if (
        isinstance(main_port, bool)
        or not isinstance(main_port, int)
        or not 1 <= main_port <= 65535 - PORT_OFFSET
    ):
        raise ValueError(f"port {main_port} out of range")
    return main_port + PORT_OFFSET


def normalize_digits(text: str) -> str:
    """使用者輸入的 6 位數：允許空白與全形數字（日文輸入法），其他一律拒絕。

    Raises:
        ValueError: 不是 6 位數字。
    """
    if not isinstance(text, str) or len(text) > 32:
        raise ValueError("enter the 6 digits shown on the other computer")
    cleaned = "".join(unicodedata.normalize("NFKC", text).split())
    if not _DIGITS.fullmatch(cleaned):
        raise ValueError("enter the 6 digits shown on the other computer")
    return cleaned


def proof_message(
    spki_a: bytes, spki_b: bytes, nonce_a: bytes, nonce_b: bytes
) -> bytes:
    """A 要簽的「持有私鑰」證明（每個欄位長度固定，直接串接不會有歧義）。"""
    return _POP_LABEL + spki_a + spki_b + nonce_a + nonce_b


def sign_proof(identity: Identity, message: bytes) -> bytes:
    """以 A 的私鑰簽 `proof_message`，回傳 64 位元組的 r‖s。

    Raises:
        IdentityError: 私鑰檔損毀。
        OSError: 讀不到私鑰檔。
    """
    r, s = decode_dss_signature(sign(identity, message))
    half = SIGNATURE_BYTES // 2
    return r.to_bytes(half, "big") + s.to_bytes(half, "big")


def verify_proof(cert_pem: str, signature: bytes, message: bytes) -> bool:
    """`signature`（r‖s）是否為 `cert_pem` 的私鑰對 `message` 的簽章。"""
    if len(signature) != SIGNATURE_BYTES:
        return False
    public = x509.load_pem_x509_certificate(cert_pem.encode("ascii")).public_key()
    if not isinstance(public, ec.EllipticCurvePublicKey):
        return False
    half = SIGNATURE_BYTES // 2
    r = int.from_bytes(signature[:half], "big")
    s = int.from_bytes(signature[half:], "big")
    if r == 0 or s == 0:
        return False
    try:
        public.verify(encode_dss_signature(r, s), message, ec.ECDSA(hashes.SHA256()))
    except InvalidSignature:
        return False
    return True


def _no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    data: dict[str, Any] = {}
    for key, value in pairs:
        if key in data:
            raise ValueError(f"duplicate field {key!r}")
        data[key] = value
    return data


def _reject_constant(name: str) -> None:
    raise ValueError(f"{name} is not allowed")


def _expect(message: str | bytes, *types: str) -> dict[str, Any]:
    """解一則訊息：必須是 `types` 其中一種、欄位剛好是 schema 上的那些。"""
    if not isinstance(message, str):
        raise _Abort(CODE_PROTOCOL)
    try:
        data = json.loads(
            message, object_pairs_hook=_no_duplicates, parse_constant=_reject_constant
        )
    except ValueError as exc:
        raise _Abort(CODE_PROTOCOL) from exc
    if not isinstance(data, dict) or data.get("type") not in types:
        raise _Abort(CODE_PROTOCOL)  # 包含順序不對（例如先送 reveal）
    if set(data) != _FIELDS[data["type"]]:
        raise _Abort(CODE_PROTOCOL)  # 未知或缺少的欄位（例如在 reveal 夾帶憑證）
    return data


def _hex(value: Any, size: int) -> bytes:
    if (
        not isinstance(value, str)
        or len(value) != 2 * size
        or not _HEX.fullmatch(value)
    ):
        raise _Abort(CODE_PROTOCOL)
    return bytes.fromhex(value)


def _name(value: Any) -> str:
    if not isinstance(value, str) or not 1 <= len(value) <= MAX_NAME_CHARS:
        raise _Abort(CODE_PROTOCOL)
    return _clean_name(value)


def _message(kind: str, **fields: Any) -> str:
    return json.dumps({"type": kind, **fields}, ensure_ascii=False)


def _closed_code(exc: ConnectionClosed, *, peer_codes: bool) -> str:
    """連線被關閉時的代碼：B 在關閉原因裡帶的代碼（只有 A 信任）、訊息太大或格式錯，其餘是斷線。"""
    if peer_codes and exc.rcvd is not None and exc.rcvd.reason in WIRE_CODES:
        return exc.rcvd.reason
    if exc.sent is not None and exc.sent.code in (1002, 1007, 1009):
        return CODE_PROTOCOL
    return CODE_CONNECTION_LOST


def _describe(cause: BaseException | None) -> str:
    """失敗原因的例外類別與訊息（網路層錯誤，不含數字、亂數或金鑰），方便判斷是哪一步斷的。"""
    return f"{type(cause).__name__}: {cause}" if cause is not None else "-"


_join_reader_before_close = join_reader_before_close
_SafeServerConnection = SafeServerConnection
_SafeClientConnection = SafeClientConnection


class _Notifier:
    """依序通知狀態；終止狀態只通知一次，之後的通知全部丟掉。"""

    def __init__(self, on_update: OnUpdate) -> None:
        self._on_update = on_update
        self._lock = threading.RLock()
        self._ended = False

    @property
    def ended(self) -> bool:
        with self._lock:
            return self._ended

    def emit(self, update: PairingUpdate) -> bool:
        with self._lock:
            if self._ended:
                return False
            self._ended = update.state in TERMINAL_STATES
            try:
                self._on_update(update)
            except Exception:
                # 回呼屬於呼叫端；它出錯不能讓配對卡在半路（之後的清理仍要做完）
                logger.exception("a pairing state callback failed")
            return True


# --------------------------------------------------------------------------- B


class _Pending:
    """B 上正在配對的那一個連線。由連線執行緒、使用者輸入與中止三方共用。"""

    def __init__(self, connection: ServerConnection) -> None:
        self.connection = connection
        self.lock = threading.Lock()
        self.wake = threading.Event()
        self.expected = False
        self.entered: str | None = None
        self.abort_code: str | None = None
        self.committing = False

    def offer(self, digits: str) -> bool:
        with self.lock:
            if not self.expected or self.entered is not None or self.abort_code:
                return False
            self.entered = digits
        self.wake.set()
        return True

    def abort(self, code: str) -> bool:
        """從別的執行緒中止；已經開始寫入信任清單就太晚了（回 False）。"""
        with self.lock:
            if self.committing or self.abort_code is not None:
                return False
            self.abort_code = code
        self.wake.set()
        return True

    def begin_commit(self) -> bool:
        with self.lock:
            if self.abort_code is not None:
                return False
            self.committing = True
            return True


class PairingServer:
    """B 的配對埠：`open()` 開窗口並監聽，配對成功、失敗或取消後關閉。一個物件只用一次。"""

    def __init__(
        self,
        *,
        identity: Identity,
        trust: TrustStore,
        name: str,
        host: str,
        port: int,
        on_update: OnUpdate,
        window: PairingWindow | None = None,
        step_timeout: float = STEP_TIMEOUT,
        user_timeout: float = USER_TIMEOUT,
    ) -> None:
        """
        Args:
            identity: B 自己的憑證（TLS 握手出示）。
            trust: B 的信任清單（`compute/trusted.json`）；只在數字相符後寫入。
            name: 告訴 A 的名稱（顯示用）。
            host: 監聽位址（與主埠相同）。
            port: 監聽埠（正式為 `pairing_port(主埠)`；0 由系統挑，測試用）。
            on_update: 狀態回呼（在背景執行緒呼叫）。
            window: 配對窗口（測試注入假時鐘用）；預設開 5 分鐘。
            step_timeout: 每一則網路訊息的逾時（秒）。
            user_timeout: 等 B 的使用者輸入數字的逾時（秒）。
        """
        self._identity = identity
        self._trust = trust
        self._name = _clean_name(name)
        self._host = host
        self._port = port
        self._notify = _Notifier(on_update)
        self._window = window if window is not None else PairingWindow()
        self._step_timeout = step_timeout
        self._user_timeout = user_timeout
        self._lock = threading.Lock()
        self._started = False
        self._finished = False
        self._pending: _Pending | None = None
        self._server: Server | None = None
        self._ended = threading.Event()
        self._stopped = threading.Event()

    @property
    def port(self) -> int:
        """實際監聽的埠（`open()` 之後）。"""
        if self._server is None:
            raise RuntimeError("the pairing port is not open")
        return int(self._server.socket.getsockname()[1])

    def open(self) -> None:
        """開配對窗口並開始監聽（背景執行緒）；通知 waiting。

        Raises:
            RuntimeError: 已經開過（一個物件只用一次）。
            OSError: 埠被佔用或無法監聽。
        """
        with self._lock:
            if self._started or self._finished:
                raise RuntimeError("this pairing server was already used")
            self._started = True
        self._window.open()
        try:
            self._server = serve(
                self._handle,
                self._host,
                self._port,
                ssl=self._ssl_context(),
                # None：只接受沒有 Origin 標頭的請求（瀏覽器發起的連線一定帶 Origin）
                origins=[None],
                compression=None,
                server_header=None,
                process_request=self._process_request,
                open_timeout=HANDSHAKE_TIMEOUT,
                ping_interval=None,
                close_timeout=CLOSE_TIMEOUT,
                max_size=MAX_MESSAGE_BYTES,
                max_queue=4,
                create_connection=_SafeServerConnection,
            )
        except OSError:
            with self._lock:
                self._finished = True
            self._window.close()
            self._ended.set()
            self._stopped.set()
            raise
        threading.Thread(
            target=self._server.serve_forever, name="pairing-server", daemon=True
        ).start()
        threading.Thread(target=self._watch, name="pairing-watch", daemon=True).start()
        logger.info("pairing port open on %s:%d", self._host, self.port)
        self._notify.emit(PairingUpdate("waiting"))

    def submit_digits(self, text: str) -> None:
        """B 的使用者輸入 A 上顯示的 6 位數（只能輸入一次；不同就中止）。

        Raises:
            ValueError: 不是 6 位數字（不算一次嘗試）。
            PairingError: 現在不是在等使用者輸入。
        """
        digits = normalize_digits(text)
        with self._lock:
            pending = None if self._finished else self._pending
        if pending is None or not pending.offer(digits):
            raise PairingError("not waiting for digits")

    def cancel(self) -> None:
        """B 的使用者取消：中止進行中的配對（A 收到 `cancelled`）並關閉配對埠。不等待。"""
        self._finish(PairingUpdate("failed", code=CODE_CANCELLED))

    def join(self, timeout: float) -> bool:
        """等配對埠完全關閉；回傳是否已關閉。"""
        return self._stopped.wait(timeout)

    # --- 內部 ---------------------------------------------------------------

    def _ssl_context(self) -> ssl.SSLContext:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.minimum_version = ssl.TLSVersion.TLSv1_3
        context.load_cert_chain(self._identity.cert_path, self._identity.key_path)
        # 不要求用戶端憑證：A 的憑證在 pair.commit 裡送、以簽章證明持有私鑰
        context.verify_mode = ssl.CERT_NONE
        # 不發 TLS 1.3 session ticket（配對不會續用 session）。ticket 是握手後才送的訊息：
        # 對方的讀取執行緒在 SSL_read 裡處理它的同時，另一條執行緒正以同一個 SSL 物件
        # SSL_write（websockets 的讀與寫在不同執行緒，OpenSSL 的 SSL 物件不能這樣並用），
        # 負載高時第一則訊息（HTTP 升級請求、pair.commit）會憑空消失，甚至當掉。
        context.num_tickets = 0
        return context

    @staticmethod
    def _process_request(
        connection: ServerConnection, request: Request
    ) -> Response | None:
        host = connection.remote_address[0] if connection.remote_address else ""
        if not is_allowed_source(str(host)):
            logger.warning("refused a pairing connection from a public address")
            return connection.respond(403, "Forbidden\n")
        if request.path != PATH:
            return connection.respond(404, "Not Found\n")
        return None

    def _watch(self) -> None:
        # 窗口到期而沒有人在配對：關閉配對埠。正在配對的連線有自己的逾時，不在這裡打斷
        while not self._ended.wait(_WATCH):
            with self._lock:
                busy = self._pending is not None
            if not busy and not self._window.is_open():
                logger.info("the pairing window expired")
                self._finish(PairingUpdate("failed", code=CODE_EXPIRED))

    def _finish(self, update: PairingUpdate, handled: _Pending | None = None) -> None:
        """結束這次配對（成功或失敗）：關窗口、通知、背景關閉配對埠。只有第一次有效。

        `handled` 是呼叫者自己的連線（它自己送 pair.abort）；其他進行中的連線由背景執行緒中止。
        """
        reject = None
        with self._lock:
            if self._finished:
                return
            pending = self._pending
            if (
                update.state == "failed"
                and update.code is not None
                and pending is not None
                and pending is not handled
            ):
                if pending.abort(update.code):
                    reject = pending.connection
                elif pending.committing:
                    # 已經在寫入信任清單（數字相符了）：讓連線執行緒以 done 結束並關閉，
                    # 否則 B 顯示失敗卻已經信任 A
                    return
            self._finished = True
        self._window.close()
        self._ended.set()
        # 在背景關：`Server.shutdown()` 會等所有連線執行緒結束，呼叫者可能就是其中之一
        threading.Thread(
            target=self._shutdown,
            args=(reject, update.code),
            name="pairing-shutdown",
            daemon=True,
        ).start()
        self._notify.emit(update)

    def _shutdown(self, reject: ServerConnection | None, code: str | None) -> None:
        try:
            if reject is not None and code is not None:
                self._reject(reject, code)
            if self._server is not None:
                self._server.shutdown()
        finally:
            self._stopped.set()

    @classmethod
    def _reject(cls, connection: ServerConnection, code: str) -> None:
        """送 pair.abort 並以同一個代碼當關閉原因（A 兩者都認得；重複送無害）。"""
        cls._send(connection, _message("pair.abort", code=code))
        connection.close(1008, code)

    @staticmethod
    def _send(connection: ServerConnection, text: str) -> None:
        try:
            connection.send(text)
        except ConnectionClosed:
            pass  # 對方已斷線：中止訊息送不到也沒關係，連線執行緒會收拾

    def _claim(self, connection: ServerConnection) -> _Pending | None:
        with self._lock:
            if self._finished:
                refusal: str | None = CODE_PAIRING_CLOSED
            elif self._pending is not None:
                refusal = CODE_CONCURRENT
            elif not self._window.claim():
                refusal = CODE_PAIRING_CLOSED
            else:
                self._pending = _Pending(connection)
                return self._pending
        self._send(connection, _message("pair.abort", code=refusal))
        if refusal == CODE_CONCURRENT:
            # 有人搶先或同時連線：無法分辨誰是真的 A，全部中止（Sunshine GHSA-36ff）
            logger.warning("a second computer tried to pair at the same time; aborting")
            self._finish(PairingUpdate("failed", code=CODE_CONCURRENT))
        connection.close(1008, refusal)
        return None

    def _handle(self, connection: ServerConnection) -> None:
        pending = self._claim(connection)
        if pending is None:
            return
        cause: BaseException | None = None
        try:
            update = self._pair(connection, pending)
        except _Abort as exc:
            code, cause = exc.code, exc.__cause__
        except TimeoutError as exc:
            code, cause = CODE_TIMEOUT, exc
        except ConnectionClosed as exc:
            code, cause = _closed_code(exc, peer_codes=False), exc
        except Exception:
            logger.exception("pairing failed unexpectedly")
            code = CODE_INTERNAL
        else:
            self._finish(update, handled=pending)
            return
        if pending.abort_code is not None:
            # 別的執行緒已經中止（取消、第二個連線）並通知過；自己再送一次中止原因：
            # 這個執行緒一返回，websockets 就以 1000 關閉連線，可能搶在背景的中止訊息之前
            self._reject(connection, pending.abort_code)
            return
        # info 而不是 warning：配對失敗（數字不同、逾時、對方取消）是使用者層級的結果，對話框已經
        # 用使用者的語言說明；warning 會被轉成介面上重複又是英文的「引擎提醒」
        logger.info("pairing failed: %s (%s)", code, _describe(cause))
        if code != CODE_CONNECTION_LOST:
            self._send(connection, _message("pair.abort", code=code))
        self._finish(PairingUpdate("failed", code=code), handled=pending)

    def _pair(self, connection: ServerConnection, pending: _Pending) -> PairingUpdate:
        commit = _expect(connection.recv(timeout=self._step_timeout), "pair.commit")
        cert_text = commit["cert_pem"]
        if (
            not isinstance(cert_text, str)
            or len(cert_text) > MAX_CERT_CHARS
            or not cert_text.isascii()
        ):
            raise _Abort(CODE_PROTOCOL)
        name_a = _name(commit["name"])
        commitment = _hex(commit["commit"], COMMIT_BYTES)
        try:
            spki_a, pem_a = _peer_certificate(cert_text.encode("ascii"))
        except ValueError as exc:
            raise _Abort(CODE_BAD_CERTIFICATE) from exc
        # 收到並驗過 A 的憑證之後才建立（設計審查第 13 點）
        pairing = ServerPairing(own=self._identity.spki, peer=spki_a)
        nonce_b = pairing.receive_commit(commitment)
        connection.send(_message("pair.nonce", nonce=nonce_b.hex(), name=self._name))

        reveal = _expect(connection.recv(timeout=self._step_timeout), "pair.reveal")
        nonce_a = _hex(reveal["nonce"], NONCE_BYTES)
        signature = _hex(reveal["sig"], SIGNATURE_BYTES)
        try:
            digits = pairing.receive_reveal(nonce_a)
        except PairingError as exc:
            raise _Abort(CODE_BAD_COMMITMENT) from exc
        message = proof_message(spki_a, self._identity.spki, nonce_a, nonce_b)
        if not verify_proof(pem_a, signature, message):
            raise _Abort(CODE_BAD_SIGNATURE)

        with pending.lock:
            pending.expected = True
        self._notify.emit(PairingUpdate("enter_digits", peer_name=name_a))
        entered = self._wait_digits(connection, pending)
        if not hmac.compare_digest(entered.encode("ascii"), digits.encode("ascii")):
            raise _Abort(CODE_MISMATCH)
        if not pending.begin_commit():
            raise _Abort(pending.abort_code or CODE_CANCELLED)
        try:
            spki = self._trust.add(pem_a.encode("ascii"), name_a)
        except (ValueError, OSError) as exc:
            logger.error("could not save the paired computer: %s", exc)
            raise _Abort(CODE_STORE_FAILED) from exc
        logger.info("paired with a new computer")
        try:
            connection.send(_message("pair.result", ok=True))
        except ConnectionClosed:
            # 已寫入但 A 收不到：B 單邊信任，無害（A 不會信任 B，重新配對會覆蓋同一個 SPKI）
            logger.info("the paired computer left before the result was sent")
        return PairingUpdate("done", peer_name=name_a, spki=spki)

    def _wait_digits(self, connection: ServerConnection, pending: _Pending) -> str:
        deadline = time.monotonic() + self._user_timeout
        while not pending.wake.wait(_POLL):
            if time.monotonic() >= deadline:
                raise TimeoutError
            try:
                connection.recv(timeout=0)  # 斷線會丟 ConnectionClosed
            except TimeoutError:
                continue
            raise _Abort(CODE_PROTOCOL)  # 這時 A 不該送任何訊息
        with pending.lock:
            entered, code = pending.entered, pending.abort_code
        if code is not None or entered is None:
            raise _Abort(code or CODE_CANCELLED)
        return entered


# --------------------------------------------------------------------------- A


def _check_host(host: str) -> str:
    if not isinstance(host, str) or not 1 <= len(host) <= 253:
        raise ValueError("invalid host")
    try:
        ipaddress.ip_address(host.split("%", 1)[0])
    except ValueError:
        if not _HOSTNAME.fullmatch(host):
            raise ValueError("invalid host") from None
    return host


def _check_port(port: int) -> int:
    if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
        raise ValueError("invalid port")
    return port


class PairingClient:
    """A 的配對客戶端：`start()` 連到 B 的配對埠，顯示數字、等使用者確認。一個物件只用一次。"""

    def __init__(
        self,
        *,
        identity: Identity,
        trust: TrustStore,
        name: str,
        host: str,
        port: int,
        on_update: OnUpdate,
        label: str | None = None,
        step_timeout: float = STEP_TIMEOUT,
        user_timeout: float = USER_TIMEOUT,
    ) -> None:
        """
        Args:
            identity: A 自己的憑證（在 pair.commit 送出、用私鑰簽證明）。
            trust: A 的信任清單（`compute/servers.json`，以 SPKI 為鍵）。
            name: 告訴 B 的名稱（顯示用）。
            host: B 的位址（IP 或主機名稱）。
            port: B 的配對埠（`pairing_port(主埠)`）。
            on_update: 狀態回呼（在背景執行緒呼叫）。
            label: 存進信任清單的名稱；None 時用 B 自稱的名稱。
            step_timeout: 連線與每一則網路訊息的逾時（秒）。
            user_timeout: 等使用者（B 輸入數字、A 確認）的逾時（秒）。

        Raises:
            ValueError: 位址或埠的格式不對。
        """
        self._identity = identity
        self._trust = trust
        self._name = _clean_name(name)
        self._host = _check_host(host)
        self._port = _check_port(port)
        self._label = label
        self._notify = _Notifier(on_update)
        self._step_timeout = step_timeout
        self._user_timeout = user_timeout
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._sock: ssl.SSLSocket | None = None
        self._ws: ClientConnection | None = None
        self._state: PairingState = "waiting"
        self._cancelled = False
        self._committing = False
        self._answer: bool | None = None
        self._answered = threading.Event()

    def start(self) -> None:
        """開始配對（背景執行緒）。

        Raises:
            RuntimeError: 已經開始過。
        """
        with self._lock:
            if self._thread is not None:
                raise RuntimeError("this pairing client was already used")
            self._thread = threading.Thread(
                target=self._run, name="pairing-client", daemon=True
            )
        self._thread.start()

    def confirm(self, same: bool) -> None:
        """A 的使用者回答「運算主機上顯示配對成功了嗎？」。

        `same=False` 在任何時候都等於中止（code `rejected`）；`same=True` 只能在 confirm 狀態。

        Raises:
            PairingError: 還沒收到 B 的結果就回答「是」。
        """
        with self._lock:
            if self._state == "confirm" and not self._cancelled:
                self._answer = same
                self._answered.set()
                return
        if same:
            raise PairingError("not waiting for confirmation")
        self._abort(CODE_REJECTED)

    def cancel(self) -> None:
        """取消：立刻關閉連線（B 那邊看到斷線），不寫入任何紀錄。不等待。"""
        self._abort(CODE_CANCELLED)

    def join(self, timeout: float) -> bool:
        """等背景執行緒結束；回傳是否已結束。"""
        thread = self._thread
        if thread is None:
            return True
        thread.join(timeout)
        return not thread.is_alive()

    # --- 內部 ---------------------------------------------------------------

    def _abort(self, code: str) -> None:
        with self._lock:
            if self._cancelled or self._committing or self._notify.ended:
                return
            self._cancelled = True
            # 在鎖內 shutdown：`_close` 要先拿到鎖才會關閉 socket，這裡碰到的一定還是同一個 handle
            if self._sock is not None:
                _shutdown_socket(self._sock)
        self._answered.set()
        self._notify.emit(PairingUpdate("failed", code=code))

    def _emit(self, update: PairingUpdate) -> None:
        with self._lock:
            if self._cancelled:
                return
            self._state = update.state
        self._notify.emit(update)

    def _register(self, sock: ssl.SSLSocket) -> None:
        # 與 `_abort` 用同一把鎖：取消要嘛看得到這個 socket，要嘛這裡看得到已取消
        with self._lock:
            if self._cancelled:
                cancelled = True
            else:
                self._sock = sock
                cancelled = False
        if cancelled:
            sock.close()
            raise _Abort(CODE_CANCELLED)

    def _run(self) -> None:
        cause: BaseException | None = None
        try:
            update = self._pair()
        except _Abort as exc:
            code, cause = exc.code, exc.__cause__
        except TimeoutError as exc:
            code, cause = CODE_TIMEOUT, exc
        except ConnectionClosed as exc:
            code, cause = _closed_code(exc, peer_codes=True), exc
        except InvalidStatus as exc:
            code, cause = CODE_REFUSED, exc
        except (InvalidHandshake, OSError) as exc:
            # Linux 的 TLS 1.3 中，被拒的連線常在讀 HTTP 回應時才看到關閉（InvalidMessage）
            code, cause = CODE_CONNECTION_LOST, exc
        except Exception:
            logger.exception("pairing failed unexpectedly")
            code = CODE_INTERNAL
        else:
            self._notify.emit(update)
            return
        finally:
            self._close()
        with self._lock:
            cancelled = self._cancelled
        if not cancelled:
            # info 而不是 warning：理由見 PairingServer 的同一行
            logger.info("pairing failed: %s (%s)", code, _describe(cause))
            self._notify.emit(PairingUpdate("failed", code=code))

    def _close(self) -> None:
        with self._lock:
            sock, self._sock = self._sock, None
            ws, self._ws = self._ws, None
        if sock is None:
            return
        _shutdown_socket(sock)
        if ws is not None:
            # 讀取執行緒還活著時不能關 socket（handle 會被重用，見 `_join_reader_before_close`）
            _join_reader_before_close(ws)
            if ws.recv_events_thread.is_alive():
                return  # 寧可留著 handle，也不要讓它被別條連線重用
        sock.close()

    def _ssl_context(self) -> ssl.SSLContext:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.minimum_version = ssl.TLSVersion.TLSv1_3
        # 還沒信任 B：不驗證它的憑證，數字綁的是這條連線上實際看到的 SPKI
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        context.load_cert_chain(self._identity.cert_path, self._identity.key_path)
        return context

    def _connect(self) -> tuple[ClientConnection, bytes, str]:
        """TCP → TLS → WebSocket；回傳連線與 B 的 (SPKI, PEM)。"""
        try:
            raw = socket.create_connection(
                (self._host, self._port), timeout=self._step_timeout
            )
        except ConnectionRefusedError as exc:
            raise _Abort(CODE_PAIRING_CLOSED) from exc
        except OSError as exc:  # 含逾時與找不到主機
            raise _Abort(CODE_UNREACHABLE) from exc
        # 自己做 TLS 再把 SSLSocket 交給 websockets（ws:// 不會再包一層）：這樣取消時手上一直有
        # 那個 socket 可以關，握手中也一樣（`sock=` 傳未加密的 socket 會被 wrap_socket 拆走）
        try:
            tls = self._ssl_context().wrap_socket(
                raw, server_hostname=None, do_handshake_on_connect=False
            )
        except (ssl.SSLError, OSError):
            raw.close()
            raise
        self._register(tls)
        try:
            tls.settimeout(self._step_timeout)
            tls.do_handshake()
            tls.settimeout(None)
        except (ssl.SSLError, OSError) as exc:
            raise _Abort(CODE_UNREACHABLE) from exc
        der = tls.getpeercert(binary_form=True)
        if not der:
            raise _Abort(CODE_BAD_CERTIFICATE)
        try:
            spki_b, pem_b = _peer_certificate(
                ssl.DER_cert_to_PEM_cert(der).encode("ascii")
            )
        except ValueError as exc:
            raise _Abort(CODE_BAD_CERTIFICATE) from exc
        host = f"[{self._host}]" if ":" in self._host else self._host
        ws = connect(
            f"ws://{host.replace('%', '%25')}:{self._port}{PATH}",
            sock=tls,
            proxy=None,
            compression=None,
            user_agent_header=None,
            open_timeout=self._step_timeout,
            ping_interval=None,
            close_timeout=CLOSE_TIMEOUT,
            max_size=MAX_MESSAGE_BYTES,
            max_queue=4,
            create_connection=_SafeClientConnection,
            # 直接拿連線（不是 context manager）：關閉由 `_close()` 負責，取消時也一樣
            legacy=True,
        )
        with self._lock:
            self._ws = ws  # 之後由 `_close` 等它的讀取執行緒結束才關 socket
        return ws, spki_b, pem_b

    def _receive(
        self, ws: ClientConnection, timeout: float, kind: str
    ) -> dict[str, Any]:
        data = _expect(ws.recv(timeout=timeout), kind, "pair.abort")
        if data["type"] == "pair.abort":
            code = data["code"]
            known = isinstance(code, str) and code in WIRE_CODES
            raise _Abort(code if known else CODE_PROTOCOL)
        return data

    def _pair(self) -> PairingUpdate:
        self._emit(PairingUpdate("waiting"))
        try:
            own_cert = self._identity.cert_path.read_text(encoding="ascii")
        except (OSError, UnicodeError) as exc:
            logger.error("cannot read this computer's certificate: %s", exc)
            raise _Abort(CODE_IDENTITY) from exc
        ws, spki_b, pem_b = self._connect()
        pairing = ClientPairing(own=self._identity.spki, peer=spki_b)
        ws.send(
            _message(
                "pair.commit",
                cert_pem=own_cert,
                name=self._name,
                commit=pairing.commit().hex(),
            )
        )
        reply = self._receive(ws, self._step_timeout, "pair.nonce")
        nonce_b = _hex(reply["nonce"], NONCE_BYTES)
        name_b = _name(reply["name"])
        digits = pairing.receive_nonce(nonce_b)
        nonce_a = pairing.reveal()
        try:
            signature = sign_proof(
                self._identity,
                proof_message(self._identity.spki, spki_b, nonce_a, nonce_b),
            )
        except (IdentityError, OSError) as exc:
            logger.error("cannot sign with this computer's key: %s", exc)
            raise _Abort(CODE_IDENTITY) from exc
        ws.send(_message("pair.reveal", nonce=nonce_a.hex(), sig=signature.hex()))
        self._emit(PairingUpdate("show_digits", digits=digits, peer_name=name_b))

        # B 的使用者有 user_timeout 可以輸入；多等一步讓 B 自己的逾時先發生、送出 abort
        result = self._receive(
            ws, self._user_timeout + self._step_timeout, "pair.result"
        )
        if result["ok"] is not True:
            raise _Abort(CODE_PROTOCOL)
        ws.close()
        self._close()

        # pair.result 中間人也能送：只有使用者看到 B 上的「配對成功」才證明連到的是真的 B
        self._emit(PairingUpdate("confirm", peer_name=name_b))
        if not self._answered.wait(self._user_timeout):
            raise _Abort(CODE_TIMEOUT)
        with self._lock:
            if self._cancelled:
                raise _Abort(CODE_CANCELLED)
            if not self._answer:
                raise _Abort(CODE_REJECTED)
            self._committing = True
        try:
            spki = self._trust.add(pem_b.encode("ascii"), self._label or name_b)
        except (ValueError, OSError) as exc:
            logger.error("could not save the paired server: %s", exc)
            raise _Abort(CODE_STORE_FAILED) from exc
        logger.info("paired with a compute server")
        return PairingUpdate("done", peer_name=name_b, spki=spki)


_shutdown_socket = shutdown_socket
