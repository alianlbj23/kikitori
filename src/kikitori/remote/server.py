"""遠端運算的運算主機（B）：接受一台已配對的電腦，把它送來的音訊交給現有的 `Pipeline`。

位於 remote 模組的伺服器層（docs/specs/remote-compute.md「架構」「安全」）。資安設定逐項對應
`research_notes/kikitori 遠端運算架構/security_advisories_threat_model.md`：

- **雙向 TLS 1.3**：只有信任清單上的電腦（完整憑證當信任錨）能完成握手；握手後再比對 SPKI，
  移除配對的電腦就算還有舊連線也立刻失效。
- **只收私有網段**（區網、Tailscale）：其他來源在 HTTP 升級前就回 403。
- **只接受沒有 `Origin` 的連線**（瀏覽器一定會帶 → 跨站 WebSocket 劫持被擋）、關閉壓縮
  （壓縮炸彈）、不送版本標頭、訊息大小上限、握手與閒置逾時。
- **只認得 `remote.protocol` 的幾種訊息**：不沿用 stdio 引擎的指令表（那裡有下載、刪除模型與改設定）。
- **一次只服務一台**，而且與 B 自己的字幕 session 共用同一個「GPU 使用權」（`acquire_gpu`）。
  同一台電腦（同一個 SPKI）的新連線取代舊連線：Wi-Fi 斷線通常沒有 FIN／RST，不取代的話
  A 重連時會被自己還沒逾時的舊連線擋成 `busy`（設計審查第 1 點）。
- **先握手、後佔 GPU**（docs/specs/compute-capacity.md「三」）：`hello {intent: probe}` 只回
  `welcome`（含這台的能力 `host`，見 `remote.capacity`）就關閉，不佔 GPU、忙碌時也回答；
  A 在 `session.start` 指定的 ASR 模型只接受 registry 裡、這台已下載的。
- **送出只經過 `Link`**（單一寫入執行緒、有上限的佇列、看門狗）：pipeline 的 worker 執行緒
  與 metrics 不直接碰網路，連線卡住時不會把 pipeline 一起卡死（設計審查第 2 點）。
- **log 不記**音訊與字幕內容。
"""

import logging
import ssl
import threading
import time
from collections import deque
from collections.abc import Callable, Mapping
from typing import Any

from websockets.exceptions import ConnectionClosed
from websockets.http11 import Request, Response
from websockets.sync.server import Server, ServerConnection, serve

from kikitori.asr.registry import MODELS
from kikitori.config import Settings
from kikitori.engine.events import MetricsReporter, gpu_sampler_for, subtitle_event
from kikitori.pipeline import Pipeline, PipelineState, SubtitleUpdate
from kikitori.pipeline.factory import pipeline_config
from kikitori.pipeline.metrics import PipelineMetrics
from kikitori.pipeline.types import Mode, PipelineComponents, release_components
from kikitori.remote import protocol as rp
from kikitori.remote.capacity import HostCapacitySource, default_downloaded
from kikitori.remote.identity import Identity, spki_sha256_der
from kikitori.remote.link import Link
from kikitori.remote.network import is_allowed_source
from kikitori.remote.pairing import TrustStore
from kikitori.remote.sockets import SafeServerConnection
from kikitori.remote.source import PushSource, PushSourceOverflow

logger = logging.getLogger(__name__)

PATH = rp.PATH
HANDSHAKE_TIMEOUT = 5.0
HELLO_TIMEOUT = 10.0
IDLE_TIMEOUT = 10.0
"""A 每 3 秒送心跳、執行中持續送音訊；這麼久沒有任何訊息就斷開（斷線、慢速連線攻擊、當掉的客戶端）。

與 A 判定斷線的時間相同（規格「斷線重連」）。
"""
SEND_QUEUE = 256
"""B → A 待送訊息上限（字幕、狀態、metrics、ack）；滿了表示 A 收不動或連線已斷。"""
TAKEOVER_TIMEOUT = 10.0
"""同一台電腦重連時，最多等舊連線收拾（停止 pipeline、釋放模型）多久。"""
MAX_PENDING_SAMPLES = 30 * rp.SAMPLE_RATE
ACK_EVERY_SAMPLES = rp.SAMPLE_RATE
STOP_TIMEOUT = 5.0
RETAIN_SECS = 45.0
"""斷線後保留 session 多久（從最後收到訊息起算）：必須比 A 放棄重連的 30 秒長（規格「斷線重連」）。"""
MAX_REPLAY = 64
"""續接時最多補送幾則完整字幕。"""

ComponentBuilder = Callable[..., PipelineComponents]
"""`(settings, mode, *, progress, cancelled)` → 元件（`pipeline.factory.build_session_components`）。"""


def server_ssl_context(identity: Identity, trust: TrustStore) -> ssl.SSLContext:
    """TLS 1.3、出示自己的憑證、**一定要**用戶端出示信任清單上的憑證。

    信任清單是空的時候不載入任何信任錨：所有握手都失敗（還沒配對過任何電腦）。
    """
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_3
    # 不送 TLS 1.3 session ticket：它在握手之後才送，A 的讀取執行緒處理 ticket 時另一條執行緒
    # 正在送第一則訊息，同一個 OpenSSL SSL 物件被兩條執行緒同時使用（負載高時訊息消失、甚至
    # segfault，2026-11-20 實測）。A 每次都完整握手，本來就不續用 session。
    context.num_tickets = 0
    context.load_cert_chain(identity.cert_path, identity.key_path)
    context.verify_mode = ssl.CERT_REQUIRED
    trusted = trust.trusted_pem()
    if trusted:
        context.load_verify_locations(cadata=trusted)
    return context


class _Session:
    """一場遠端字幕：B 的 pipeline、聲音來源與 metrics，以及目前接著的連線（可以換）。

    屬於 `RemoteServer`，不屬於某一條連線（設計審查第 3 點）：連線斷了 session 照樣跑、保留
    `RETAIN_SECS` 秒，同一台電腦重連後以 `session.resume` 接回。送出一律經 `send*()`：沒有
    接著的連線時丟掉（即時字幕、metrics 斷線期間沒有意義），但完整字幕與結束狀態另外留著，
    續接時補送。
    """

    def __init__(
        self,
        session_id: int,
        mode: Mode,
        owner: bytes,
        models: tuple[str, str] | None = None,
    ) -> None:
        self.id = session_id
        self.mode = mode
        self.owner = owner
        self.models = models
        """這一場的（即時, 完整）ASR 模型：A 可以指定，不一定是 B 設定裡的（給「使用中」判斷）。"""
        self.source = PushSource(MAX_PENDING_SAMPLES)
        self.cancelled = threading.Event()
        self.pipeline: Pipeline | None = None
        self.components: PipelineComponents | None = None
        self.metrics: MetricsReporter | None = None
        self.next_seq = 0
        self.audio_ended = False
        self.acked = 0
        self.ended = threading.Event()
        self.lock = threading.Lock()
        """保護 pipeline 的建立、啟動與停止、元件的釋放（載入執行緒與連線執行緒）。"""
        self.last_rx = time.monotonic()
        """最後一次收到 A 的訊息（保留時間從這裡起算）。"""
        self.retain: threading.Timer | None = None
        self._out_lock = threading.Lock()
        self._out: Link | None = None
        self._finals: deque[tuple[int, str]] = deque(maxlen=MAX_REPLAY)
        self._state = "loading"
        self._final_status: str | None = None

    @property
    def attached(self) -> bool:
        """目前有連線接著。"""
        with self._out_lock:
            return self._out is not None

    def attach(self, link: Link) -> None:
        """第一條連線：之後的訊息送到 `link`；音訊序號從 0 開始。"""
        with self._out_lock:
            self._out = link
            self.next_seq = 0

    def detach(self, link: Link) -> bool:
        """`link` 斷了：之後的即時訊息丟掉。回傳 `link` 是否就是接著的那一條。"""
        with self._out_lock:
            if self._out is not link:
                return False
            self._out = None
            return True

    def resume(self, link: Link, last_final_id: int) -> None:
        """重連：接上 `link`，送 `resumed`、補送 A 沒收到的完整字幕與結束狀態。

        全部在同一把鎖內放進佇列：補送的訊息不會和新的字幕交錯。
        """
        with self._out_lock:
            self._out = link
            self.next_seq = 0  # 每條連線的音訊序號從 0 開始；時間軸靠 start_sample
            link.send(rp.resumed(self.id, self.source.received_samples, self._state))
            for utterance_id, text in self._finals:
                if utterance_id > last_final_id:
                    link.send(text)
            if self._final_status is not None:
                link.send(self._final_status)

    def send(self, text: str) -> None:
        """即時訊息（即時字幕、metrics、ack、log）：沒有連線時丟掉。"""
        with self._out_lock:
            out = self._out
        if out is not None:
            out.send(text)

    def send_subtitle(self, update: SubtitleUpdate) -> None:
        """字幕；完整字幕另外留在環狀緩衝（斷線前剛送出的那幾則可能沒到）。"""
        text = rp.event("subtitle", subtitle_event(update, self.id))
        with self._out_lock:
            if update.is_final:
                self._finals.append((update.utterance_id, text))
            out = self._out
        if out is not None:
            out.send(text)

    def send_status(
        self,
        state: str,
        detail: str | None,
        code: str | None = None,
        data: dict[str, Any] | None = None,
    ) -> None:
        """狀態；結束狀態（idle／error）留著，斷線期間結束的話續接時補送。"""
        text = rp.status(state, detail, code, data)
        with self._out_lock:
            self._state = state
            if state in ("idle", "error"):
                self._final_status = text
            out = self._out
        if out is not None:
            out.send(text)


class RemoteServer:
    """一對一的遠端運算伺服器。`start()` 在背景執行緒接受連線，`stop()` 關閉。"""

    def __init__(
        self,
        *,
        identity: Identity,
        trust: TrustStore,
        settings: Callable[[], Settings],
        build_components: ComponentBuilder,
        acquire_gpu: Callable[[], bool],
        release_gpu: Callable[[], None],
        host: str,
        port: int,
        app_version: str,
        metrics_interval: float = 1.0,
        retain_secs: float = RETAIN_SECS,
        on_change: Callable[[], None] | None = None,
        capacity: HostCapacitySource | None = None,
    ) -> None:
        """
        Args:
            identity: B 自己的憑證。
            trust: 已配對的電腦。
            settings: 讀取 B 目前的設定（模型組合、翻譯伺服器等用 B 的）。
            build_components: 建立 pipeline 元件（載入 B 的模型）。
            acquire_gpu: 取得 GPU 使用權（B 自己在跑字幕或已有連線時回 False）。
            release_gpu: 歸還 GPU 使用權。
            host: 監聽位址（例如 Tailscale IP；來源仍會檢查）。
            port: 監聽埠；0 表示由系統挑（測試用）。
            app_version: 握手時告訴 A 的版本。
            metrics_interval: metrics 的間隔（秒）。
            retain_secs: 斷線後保留 session 多久（從最後收到訊息起算；測試用較短的值）。
            on_change: 連線中的電腦或它的狀態改變時呼叫（有電腦連上、斷線、等待續接、
                離開）；給介面更新「目前連線中的電腦」。在連線執行緒上呼叫，不能阻塞。
            capacity: B 的能力（`welcome.host`）與已下載的模型（檢查 A 指定的模型）；
                通常是 `remote.capacity.HostCapacity`。None 時 `host` 為 null、已下載的模型
                直接查這台電腦的模型快取。
        """
        self._capacity = capacity
        self._identity = identity
        self._trust = trust
        self._settings = settings
        self._build_components = build_components
        self._acquire_gpu = acquire_gpu
        self._release_gpu = release_gpu
        self._host = host
        self._port = port
        self._app_version = app_version
        self._metrics_interval = metrics_interval
        self._retain_secs = retain_secs
        self._on_change = on_change
        self._server: Server | None = None
        self._thread: threading.Thread | None = None
        self._ssl_context: ssl.SSLContext | None = None
        self._next_session_id = 1
        self._lock = threading.Lock()
        # 以下受 _lock 保護。GPU 使用權屬於一台電腦（_owner）：它的連線還在、或它的 session
        # 還在等續接時都不放開，其他電腦一律 busy。
        self._owner: bytes | None = None
        self._current: tuple[bytes, Link, threading.Event] | None = None
        """目前的連線：(對方 SPKI, Link, 收拾完成的事件)。"""
        self._session: _Session | None = None
        self._closing = False

    @property
    def port(self) -> int:
        """實際監聽的埠（`start()` 之後）。"""
        if self._server is None:
            raise RuntimeError("the server is not running")
        return int(self._server.socket.getsockname()[1])

    def start(self) -> None:
        """開始監聽（背景執行緒）。

        Raises:
            OSError: 埠被佔用或無法監聽。
        """
        # 留著同一個 context：配對新電腦後 `trust_changed()` 把它加進去（接受的連線都用它握手）
        self._ssl_context = server_ssl_context(self._identity, self._trust)
        self._closing = False
        self._server = serve(
            self._handle,
            self._host,
            self._port,
            ssl=self._ssl_context,
            # None：只接受沒有 Origin 標頭的請求（瀏覽器發起的連線一定帶 Origin）
            origins=[None],
            compression=None,
            server_header=None,
            process_request=self._process_request,
            open_timeout=HANDSHAKE_TIMEOUT,
            # 心跳由應用層的 ping／pong 處理（順便量來回時間）
            ping_interval=None,
            close_timeout=2.0,
            max_size=rp.MAX_TEXT_BYTES,
            max_queue=64,
            # 關閉前先等讀取執行緒結束（見 remote.sockets：Windows 的 handle 重用）
            create_connection=SafeServerConnection,
        )
        self._thread = threading.Thread(
            target=self._server.serve_forever, name="remote-server", daemon=True
        )
        self._thread.start()
        logger.info("remote compute server listening on %s:%d", self._host, self.port)

    def stop(self) -> None:
        """停止接受連線、關閉現有連線並結束 session（包括等待續接的）。可重複呼叫。"""
        with self._lock:
            self._closing = True  # 關閉造成的斷線不保留 session
        server, self._server = self._server, None
        if server is not None:
            server.shutdown()
        if self._thread is not None:
            self._thread.join(5.0)
            self._thread = None
        self._drop_retained()

    def trust_changed(self) -> None:
        """信任清單改了（配對或移除）：新配對的電腦立刻能連，被移除的電腦立刻斷線。

        OpenSSL 的信任錨只能增加不能移除，所以移除靠握手後的 SPKI 比對（`_peer_spki`）：
        被移除的電腦即使還能完成 TLS 握手，也在交換任何應用層訊息之前被斷開。等待續接的
        session 也一併結束。
        """
        context = self._ssl_context
        trusted = self._trust.trusted_pem()
        if context is not None and trusted:
            context.load_verify_locations(cadata=trusted)
        with self._lock:
            owner = self._owner
            current = self._current
        if owner is None or self._trust.is_trusted(owner):
            return
        if current is not None:
            current[1].abort("this computer was unpaired")
        self._drop_retained()

    def owner_spki(self) -> bytes | None:
        """正在使用這台電腦的那台（連線中或等待續接）的 SPKI；沒有時為 None。"""
        with self._lock:
            return self._owner

    def peer_state(self) -> str | None:
        """`"connected"`、`"waiting_resume"`（斷線、session 保留中）或 None。"""
        with self._lock:
            if self._current is not None:
                return "connected"
            return "waiting_resume" if self._session is not None else None

    def session_models(self) -> tuple[str, ...]:
        """目前這一場（含等待續接的）用的（即時, 完整）ASR 模型；沒有時空的。

        A 可以指定 B 的模型，所以不一定是 B 設定裡的；B 的引擎據此判斷模型「使用中」。
        """
        with self._lock:
            session = self._session
        if session is None or session.models is None:
            return ()
        return session.models

    def current_metrics(self) -> PipelineMetrics | None:
        """目前這一場的負荷（唯讀快照；無介面主機的「目前負荷」用）；沒有執行中的 pipeline 時 None。"""
        with self._lock:
            session = self._session
        pipeline = None if session is None else session.pipeline
        if pipeline is None or pipeline.state != "running":
            return None
        return pipeline.metrics()

    def disconnect(self, spki: bytes | None = None) -> bool:
        """斷開正在使用的電腦（連線中或等待續接；`spki` 指定時只在是它的時候）。

        Returns:
            是否斷開了一台。
        """
        with self._lock:
            owner, current = self._owner, self._current
        if owner is None or (spki is not None and owner != spki):
            return False
        with self._lock:
            session = self._session
        if session is not None:
            self._end_session(session)  # 不保留：B 的使用者要拿回 GPU
        if current is not None:
            current[1].abort("disconnected by the compute host")
        self._release_if_idle()
        return True

    # --- 連線 ---------------------------------------------------------------

    def _process_request(
        self, connection: ServerConnection, request: Request
    ) -> Response | None:
        host = connection.remote_address[0] if connection.remote_address else ""
        if not is_allowed_source(str(host)):
            logger.warning("refused a remote compute connection from a public address")
            return connection.respond(403, "Forbidden\n")
        if request.path != PATH:
            return connection.respond(404, "Not Found\n")
        return None

    def _peer_spki(self, connection: ServerConnection) -> bytes | None:
        """對方憑證的 SPKI；不在目前的信任清單上時為 None。"""
        sock = connection.socket
        if not isinstance(sock, ssl.SSLSocket):
            return None
        der = sock.getpeercert(binary_form=True)
        if not der:
            return None
        spki = spki_sha256_der(der)
        return spki if self._trust.is_trusted(spki) else None

    def _claim(self, spki: bytes, link: Link, done: threading.Event) -> bool:
        """取得服務權：同一台電腦的舊連線先被中斷並收拾完；其他電腦在用時回 False。"""
        with self._lock:
            current = self._current
        if current is not None:
            old_spki, old_link, old_done = current
            if old_spki != spki:
                return False
            old_link.abort("the same computer reconnected")
            if not old_done.wait(TAKEOVER_TIMEOUT):
                logger.warning("the previous connection did not finish in time")
                return False
        with self._lock:
            if self._current is not None or self._closing:
                return False
            if self._owner is None:
                if not self._acquire_gpu():
                    return False
                self._owner = spki
            elif self._owner != spki:
                return False  # 別台電腦的 session 正在等續接
            self._current = (spki, link, done)
        self._changed()
        return True

    def _changed(self) -> None:
        if self._on_change is None:
            return
        try:
            self._on_change()
        except Exception:
            # 只是給介面的通知：出錯不能影響連線
            logger.exception("the remote server change callback failed")

    def _unclaim(self, link: Link) -> None:
        with self._lock:
            mine = self._current is not None and self._current[1] is link
            if mine:
                self._current = None
        self._release_if_idle()
        if mine:
            self._changed()

    def _release_if_idle(self) -> None:
        """沒有連線、也沒有等待續接的 session 時歸還 GPU 使用權。"""
        with self._lock:
            if (
                self._owner is None
                or self._current is not None
                or self._session is not None
            ):
                return
            self._owner = None
        self._release_gpu()
        self._changed()

    def _handle(self, connection: ServerConnection) -> None:
        # 握手時已驗證過憑證；這裡再比對目前的信任清單（移除配對之後的舊連線立刻失效）
        spki = self._peer_spki(connection)
        if spki is None:
            logger.warning(
                "closing a remote compute connection from an unpaired computer"
            )
            connection.close(1008, "not paired")
            return
        link = Link(connection, max_queue=SEND_QUEUE, name="remote-server")
        link.start()
        # 先握手、後佔 GPU（compute-capacity 三）：probe 只查能力，B 忙碌時也要回答，而且不能
        # 因為「同一台電腦的新連線」把它正在跑的字幕連線取代掉
        hello = self._handshake(connection, link)
        if hello is None:
            link.join(5.0)
            return
        if hello.intent == "probe":
            link.send(self._welcome(detect=True))
            link.close(1000, "probe answered")
            link.join(5.0)
            return
        done = threading.Event()
        if not self._claim(spki, link, done):
            link.send(rp.busy("this computer is busy (another session is running)"))
            link.close(1013, "busy")
            link.join(5.0)
            return
        session: _Session | None = None
        lost = False
        try:
            # 開字幕時不重新偵測硬體（nvidia-smi、翻譯伺服器要數秒）：用快取，沒有就不帶
            link.send(self._welcome(detect=False))
            while True:
                message = connection.recv(timeout=IDLE_TIMEOUT)
                if session is not None:
                    session.last_rx = time.monotonic()
                if isinstance(message, bytes):
                    self._on_audio(session, message)
                    continue
                parsed = rp.parse_client(message)
                if isinstance(parsed, rp.SessionStart):
                    if session is not None and not session.ended.is_set():
                        raise rp.ProtocolViolation("a session is already running")
                    # 被拒（A 指定的模型不能用）時留著原本的（已結束的）session：連線結束時
                    # 照樣由 _leave 收拾，GPU 使用權才會歸還
                    session = self._start_session(link, spki, parsed) or session
                elif isinstance(parsed, rp.SessionResume):
                    if session is not None:
                        raise rp.ProtocolViolation("this connection has a session")
                    session = self._resume(link, spki, parsed)
                elif isinstance(parsed, rp.SessionStop):
                    if session is not None:
                        self._end_session(session)
                elif isinstance(parsed, rp.EndAudio):
                    if session is None:
                        raise rp.ProtocolViolation("end of audio without a session")
                    # 之後再送音訊是協定錯誤（PushSource 已關閉）；pipeline 處理完剩下的就 idle
                    session.audio_ended = True
                    session.source.close()
                elif isinstance(parsed, rp.SetTranslation):
                    if session is not None and session.pipeline is not None:
                        session.pipeline.set_translation_enabled(parsed.enabled)
                elif isinstance(parsed, rp.Ping):
                    link.send(rp.pong(parsed.nonce))
                else:  # 第二次 hello
                    raise rp.ProtocolViolation("unexpected hello")
        except TimeoutError:
            logger.info("remote client idle for %.0f s; closing", IDLE_TIMEOUT)
            lost = True
            link.abort("idle")
        except rp.ProtocolViolation as exc:
            # 不保留 session：送壞訊息的對象不值得等
            logger.warning("remote client sent an invalid message: %s", exc)
            link.close(1008, "protocol violation")
        except ConnectionClosed:
            logger.info("remote client disconnected")
            lost = True
        finally:
            try:
                if session is not None:
                    self._leave(session, link, lost)
            finally:
                self._unclaim(link)
                done.set()
                link.close()
                link.join(5.0)

    def _leave(self, session: _Session, link: Link, lost: bool) -> None:
        """連線結束：斷線（不是 A 主動停止）時保留還在跑的 session 等續接，否則結束它。"""
        with self._lock:
            closing = self._closing
        detached = session.detach(link)
        if lost and detached and not closing and not session.ended.is_set():
            self._retain(session)
        elif detached or not session.attached:
            self._end_session(session)

    def _handshake(self, connection: ServerConnection, link: Link) -> rp.Hello | None:
        """等 A 的 hello（還沒佔 GPU）；不合格、版本不同或斷線時關閉連線並回 None。"""
        try:
            hello = rp.parse_client(_text(connection.recv(timeout=HELLO_TIMEOUT)))
            if not isinstance(hello, rp.Hello):
                raise rp.ProtocolViolation("expected hello")
        except TimeoutError:
            logger.info("remote client sent no hello in %.0f s; closing", HELLO_TIMEOUT)
            link.abort("no hello")
            return None
        except rp.ProtocolViolation as exc:
            logger.warning("remote client sent an invalid hello: %s", exc)
            link.close(1008, "protocol violation")
            return None
        except ConnectionClosed:
            logger.info("remote client disconnected before the handshake")
            link.close()
            return None
        if hello.protocol_version != rp.PROTOCOL_VERSION:
            link.send(
                rp.incompatible(f"this computer speaks protocol {rp.PROTOCOL_VERSION}")
            )
            link.close(1002, "incompatible")
            return None
        return hello

    def _welcome(self, *, detect: bool) -> str:
        asr = self._settings().asr
        return rp.welcome(
            self._app_version,
            {"fast": asr.fast_model, "final": asr.final_model},
            self._host_info(detect=detect),
        )

    def _host_info(self, *, detect: bool) -> dict[str, Any] | None:
        """這台的能力；查不到（程式錯誤）時 None——能力只是參考資訊，不能讓握手失敗。"""
        if self._capacity is None:
            return None
        try:
            return self._capacity.host_info(detect=detect)
        except Exception:
            logger.exception("cannot describe this computer's capabilities")
            return None

    def _model_problem(self, requested: Mapping[str, str]) -> tuple[str, str] | None:
        """A 指定的 ASR 模型能不能用：`(code, detail)`，可以用時 None。

        只接受 registry 裡、這台已下載的模型：A 不能叫 B 載入任意路徑，也不能叫 B 下載。
        """
        if not requested:
            return None
        unknown = [m for m in requested.values() if m not in MODELS]
        if unknown:
            return (
                "unknown_model",
                f"this computer does not know the model {unknown[0]}",
            )
        try:
            downloaded = (
                default_downloaded()
                if self._capacity is None
                else self._capacity.downloaded_models()
            )
        except Exception as exc:
            # OSError 以外（模型目錄格式錯誤等）也回 status error：否則例外穿過 _handle，
            # A 只看到連線中斷
            logger.exception("cannot list the downloaded ASR models")
            return "load_error", f"cannot list the downloaded models: {exc}"
        missing = [m for m in dict.fromkeys(requested.values()) if m not in downloaded]
        if missing:
            return (
                "model_not_downloaded",
                f"{', '.join(missing)} is not downloaded on the compute host",
            )
        return None

    # --- session -----------------------------------------------------------

    def _start_session(
        self, link: Link, owner: bytes, start: rp.SessionStart
    ) -> _Session | None:
        """開始一場；A 指定的模型不能用時送 `status error`（不開始）並回 None。"""
        problem = self._model_problem(rp.requested_models(start))
        if problem is not None:
            code, detail = problem
            logger.warning("refused a remote session: %s", detail)
            link.send(rp.status("error", detail, code))
            return None
        settings = rp.session_settings(self._settings(), start)
        # A 不續接、直接開新的一場：保留中的舊 session（同一台電腦的）先結束、釋放模型
        self._drop_retained()
        with self._lock:
            session = _Session(
                self._next_session_id,
                start.mode,
                owner,
                (settings.asr.fast_model, settings.asr.final_model),
            )
            self._next_session_id += 1
            self._session = session
        session.attach(link)
        link.send(rp.session_started(session.id))
        threading.Thread(
            target=self._load,
            args=(session, settings),
            name=f"remote-session-{session.id}",
            daemon=True,
        ).start()
        return session

    def _resume(
        self, link: Link, owner: bytes, resume: rp.SessionResume
    ) -> _Session | None:
        """接回保留中的 session；不行（過期、別台電腦、編號不符）時回 `resume_failed`。"""
        with self._lock:
            session = self._session
            ok = (
                session is not None
                and session.owner == owner
                and session.id == resume.session_id
                and not session.attached
            )
            if ok and session is not None and session.retain is not None:
                session.retain.cancel()
                session.retain = None
        if not ok or session is None:
            link.send(rp.resume_failed("there is no session to resume"))
            return None
        session.last_rx = time.monotonic()
        session.resume(link, resume.last_final_id)
        logger.info("remote session %d resumed", session.id)
        return session

    def _retain(self, session: _Session) -> None:
        """斷線：保留 session `retain_secs` 秒（從最後收到訊息起算），等同一台電腦續接。"""
        delay = max(0.0, self._retain_secs - (time.monotonic() - session.last_rx))
        timer = threading.Timer(delay, self._expire, args=(session,))
        timer.daemon = True
        with self._lock:
            if self._session is not session:
                return
            session.retain = timer
        logger.info(
            "remote client lost; keeping session %d for %.0f s", session.id, delay
        )
        timer.start()

    def _expire(self, session: _Session) -> None:
        with self._lock:
            if self._session is not session or session.attached:
                return
            # 在同一把鎖內摘掉：之後到的 session.resume 一定看到「沒有可接回的 session」，
            # 不會剛接上就被結束（審查第 9 點）
            self._session = None
        logger.info("remote session %d was not resumed in time; ending it", session.id)
        self._end_session(session)
        self._release_if_idle()

    def _drop_retained(self) -> None:
        """結束等待續接的 session（沒有連線接著的那一場）。"""
        with self._lock:
            session = self._session
        if session is not None and not session.attached:
            self._end_session(session)
        self._release_if_idle()

    def _load(self, session: _Session, settings: Settings) -> None:
        status = session.send_status
        status("loading", None)
        try:
            components = self._build_components(
                settings,
                session.mode,
                progress=lambda step, done, total: status(
                    "loading", None, None, {"step": step, "done": done, "total": total}
                ),
                cancelled=session.cancelled,
            )
        except FileNotFoundError as exc:
            status("error", str(exc), "model_not_downloaded")
            session.ended.set()
            return
        except Exception as exc:
            logger.exception("loading a remote session failed")
            status("error", f"{type(exc).__name__}: {exc}", "load_error")
            session.ended.set()
            return

        def on_log(level: str, message: str) -> None:
            # 可恢復的警告（例如翻譯失敗）讓 A 的使用者看得到；內容是錯誤說明，不含字幕
            session.send(rp.event("log", {"level": level, "message": message[:2000]}))

        def on_status(state: PipelineState, detail: str | None) -> None:
            if state in ("idle", "error"):
                session.ended.set()
            status(state, detail, None if state != "error" else "pipeline_error")

        # 「檢查停止 → 建立並啟動 pipeline」與 `_end_session` 互斥：Pipeline.stop() 不能與
        # start() 同時進行（會 join 還沒啟動的執行緒），停止後也不能再啟動
        with session.lock:
            session.components = components
            if session.cancelled.is_set():
                self._release(session)
                return
            pipeline = Pipeline(
                components,
                pipeline_config(settings, session.mode),
                # 只放進佇列：pipeline 的 worker 執行緒不能卡在網路上
                on_subtitle=session.send_subtitle,
                on_status=on_status,
                on_log=on_log,
            )
            session.pipeline = pipeline
            # B 用 CUDA 時 metrics 帶 B 的 GPU 使用量，A 的負荷圖顯示的就是 B 的負荷
            sampler = gpu_sampler_for(components)
            session.metrics = MetricsReporter(
                session.id,
                pipeline,
                lambda name, data: session.send(rp.event(name, dict(data))),
                self._metrics_interval,
                gpu_usage=None if sampler is None else sampler.get,
            )
            session.metrics.start()
            try:
                pipeline.start(session.source)
            except Exception as exc:
                logger.exception("starting a remote session failed")
                status("error", f"{type(exc).__name__}: {exc}", "load_error")
                session.ended.set()

    def _on_audio(self, session: _Session | None, data: bytes) -> None:
        if session is None or session.ended.is_set() or session.audio_ended:
            raise rp.ProtocolViolation("audio without a running session")
        frame = rp.decode_audio(data)
        # 時間軸靠樣本計數：序號（每條連線從 0 起）與起始樣本必須接續，否則字幕時間會錯位
        if (
            frame.seq != session.next_seq
            or frame.start_sample != session.source.received_samples
        ):
            raise rp.ProtocolViolation("audio frames are out of order")
        session.next_seq += 1
        try:
            session.source.push(frame.audio)
        except PushSourceOverflow as exc:
            logger.error("remote audio is piling up: %s", exc)
            raise rp.ProtocolViolation("this computer cannot keep up") from exc
        except RuntimeError as exc:
            # 另一條執行緒剛結束這一場（斷開、過期、停止）：PushSource 已關閉
            raise rp.ProtocolViolation("the session has ended") from exc
        received = session.source.received_samples
        if received - session.acked >= ACK_EVERY_SAMPLES:
            session.acked = received
            session.send(rp.ack(received))

    def _end_session(self, session: _Session) -> None:
        with self._lock:
            if session.retain is not None:
                session.retain.cancel()
                session.retain = None
            if self._session is session:
                self._session = None
        session.cancelled.set()
        with session.lock:  # 等進行中的 pipeline.start() 完成（見 `_load`）
            pipeline = session.pipeline
        if pipeline is not None:
            pipeline.stop(STOP_TIMEOUT)  # 它的 on_status 送出 idle
        else:
            session.source.stop()
            if not session.ended.is_set():
                # 還在載入就被停止：pipeline 不存在、不會送 idle，A 會白等（審查第 7 點）
                session.send_status("idle", None)
        with session.lock:
            self._release(session)
        session.ended.set()

    @staticmethod
    def _release(session: _Session) -> None:
        if session.metrics is not None:
            session.metrics.stop()
            session.metrics = None
        components, session.components = session.components, None
        if components is not None:
            release_components(components)


def _text(message: str | bytes) -> str:
    if not isinstance(message, str):
        raise rp.ProtocolViolation("expected a text message")
    return message
