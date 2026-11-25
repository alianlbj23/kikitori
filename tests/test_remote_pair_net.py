"""遠端運算的配對連線：真的 TLS 1.3（127.0.0.1）上的 B 配對埠與 A 配對客戶端。

對應 docs/specs/remote-compute.md「配對時序」與威脅模型筆記的配對測試：成功時兩邊都寫入、
數字不同／使用者說不一樣／逾時／順序錯／承諾或簽章不符／壞憑證／憑證出現在後面的訊息／未知欄位
都中止且不留紀錄（CVE-2024-45407）、窗口關閉或過期就拒絕、第二個連線全部中止（GHSA-36ff）、
取消立刻斷線、來源位址檢查。
"""

import datetime as dt
import hashlib
import json
import queue
import secrets
import socket
import ssl
import tempfile
import threading
import time
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID
from websockets.exceptions import ConnectionClosed, InvalidStatus
from websockets.sync.client import ClientConnection, connect
from websockets.sync.server import ServerConnection, serve

from kikitori.remote import pair_net as pn
from kikitori.remote.identity import Identity, load_or_create_identity
from kikitori.remote.pairing import PairingError, PairingWindow, TrustStore

WAIT = 20.0


class Recorder:
    """收集狀態回呼（在背景執行緒呼叫），測試端依序等待。"""

    def __init__(self) -> None:
        self.updates: queue.Queue[pn.PairingUpdate] = queue.Queue()
        self.seen: list[pn.PairingUpdate] = []

    def __call__(self, update: pn.PairingUpdate) -> None:
        self.updates.put(update)

    def until(self, state: str, timeout: float = WAIT) -> pn.PairingUpdate:
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise AssertionError(f"no {state!r} update; saw {self.seen}")
            try:
                update = self.updates.get(timeout=remaining)
            except queue.Empty:
                continue
            self.seen.append(update)
            if update.state == state:
                return update
            if update.state in pn.TERMINAL_STATES:
                raise AssertionError(f"ended with {update} while waiting for {state}")

    def failure(self, timeout: float = WAIT) -> str | None:
        return self.until("failed", timeout).code

    def quiet_for(self, seconds: float) -> bool:
        """這段時間內沒有終止狀態。"""
        deadline = time.monotonic() + seconds
        while (remaining := deadline - time.monotonic()) > 0:
            try:
                update = self.updates.get(timeout=remaining)
            except queue.Empty:
                return True
            self.seen.append(update)
            if update.state in pn.TERMINAL_STATES:
                return False
        return True


def raw_context() -> ssl.SSLContext:
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.minimum_version = ssl.TLSVersion.TLSv1_3
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    return context


def abort_code(ws: ClientConnection) -> str:
    """讀到 pair.abort 為止，回傳它的代碼；連線先關閉時回傳 `closed`。"""
    try:
        while True:
            message = json.loads(ws.recv(timeout=WAIT))
            if message["type"] == "pair.abort":
                return str(message["code"])
    except ConnectionClosed:
        return "closed"


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def write_ca_identity(directory: Path) -> Identity:
    """一張能當 CA 的憑證（不該被接受為信任錨，Sunshine GHSA-6jvv）。"""
    directory.mkdir(parents=True)
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "ca")])
    now = dt.datetime.now(dt.UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(1)
        .not_valid_before(now - dt.timedelta(days=1))
        .not_valid_after(now + dt.timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    cert_path, key_path = directory / "ca.crt", directory / "ca.key"
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    return Identity(cert_path, key_path, b"\0" * 32)


class PairNetCase(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.a = load_or_create_identity(self.root / "a")
        self.b = load_or_create_identity(self.root / "b")
        self.stranger = load_or_create_identity(self.root / "x")
        self.trust_a_path = self.root / "a" / "servers.json"
        self.trust_b_path = self.root / "b" / "trusted.json"
        self.trust_a = TrustStore(self.trust_a_path)
        self.trust_b = TrustStore(self.trust_b_path)

    def open_server(self, **kwargs: Any) -> tuple[pn.PairingServer, Recorder]:
        events = Recorder()
        server = pn.PairingServer(
            identity=self.b,
            trust=self.trust_b,
            name="Compute box",
            host="127.0.0.1",
            port=0,
            on_update=events,
            **kwargs,
        )
        server.open()
        self.addCleanup(server.join, WAIT)
        self.addCleanup(server.cancel)
        events.until("waiting")
        return server, events

    def start_client(
        self, port: int, **kwargs: Any
    ) -> tuple[pn.PairingClient, Recorder]:
        events = Recorder()
        client = pn.PairingClient(
            identity=self.a,
            trust=self.trust_a,
            name="Living room PC",
            host="127.0.0.1",
            port=port,
            on_update=events,
            **kwargs,
        )
        client.start()
        self.addCleanup(client.join, WAIT)
        self.addCleanup(client.cancel)
        return client, events

    def raw(self, port: int, **kwargs: Any) -> ClientConnection:
        ws = connect(
            f"wss://127.0.0.1:{port}{kwargs.pop('path', pn.PATH)}",
            ssl=raw_context(),
            proxy=None,
            compression=None,
            open_timeout=5,
            max_size=None,
            legacy=True,
            # 與 pair_net 一樣等讀取執行緒結束才關 socket，測試的連線才不會在 Windows 上
            # 讀走下一個測試的連線資料（見 pair_net._join_reader_before_close）
            create_connection=pn._SafeClientConnection,
            **kwargs,
        )
        self.addCleanup(ws.close)
        return ws

    def raw_commit(
        self, ws: ClientConnection, nonce_a: bytes, **overrides: Any
    ) -> bytes:
        """誠實地送 pair.commit（可覆蓋欄位），回傳 B 的亂數。"""
        message = {
            "type": "pair.commit",
            "cert_pem": self.a.cert_path.read_text(encoding="ascii"),
            "name": "A",
            "commit": hashlib.sha256(nonce_a).hexdigest(),
            **overrides,
        }
        ws.send(json.dumps(message))
        reply = json.loads(ws.recv(timeout=WAIT))
        self.assertEqual(reply["type"], "pair.nonce", reply)
        return bytes.fromhex(reply["nonce"])

    def proof(self, signer: Identity, nonce_a: bytes, nonce_b: bytes) -> str:
        message = pn.proof_message(self.a.spki, self.b.spki, nonce_a, nonce_b)
        return pn.sign_proof(signer, message).hex()

    def assert_nothing_written(self) -> None:
        # CVE-2024-45407：失敗的配對在兩邊都不能留下任何紀錄
        self.assertFalse(self.trust_a_path.exists())
        self.assertFalse(self.trust_b_path.exists())

    def pair_until_digits(
        self, **server_kwargs: Any
    ) -> tuple[pn.PairingServer, Recorder, pn.PairingClient, Recorder, str]:
        server, b_events = self.open_server(**server_kwargs)
        client, a_events = self.start_client(server.port)
        shown = a_events.until("show_digits")
        entering = b_events.until("enter_digits")
        self.assertEqual(entering.peer_name, "Living room PC")
        self.assertIsNone(entering.digits)  # B 不顯示自己的數字
        assert shown.digits is not None
        return server, b_events, client, a_events, shown.digits


class SuccessTest(PairNetCase):
    def test_both_sides_trust_each_other_after_matching_digits_and_confirmation(
        self,
    ) -> None:
        server, b_events, client, a_events, digits = self.pair_until_digits()
        self.assertRegex(digits, r"^\d{6}$")
        self.assertEqual(a_events.seen[-1].peer_name, "Compute box")
        # B 的使用者輸入 A 上顯示的數字（相同才會成功，等於驗證兩邊數字一樣）
        server.submit_digits(f"{digits[:3]} {digits[3:]}")
        b_done = b_events.until("done")
        self.assertEqual(b_done.spki, self.a.spki)
        self.assertTrue(self.trust_b.is_trusted(self.a.spki))
        a_events.until("confirm")
        # A 收到結果、但使用者確認之前，A 不寫入
        self.assertFalse(self.trust_a_path.exists())
        client.confirm(True)
        a_done = a_events.until("done")
        self.assertEqual(a_done.spki, self.b.spki)
        self.assertTrue(self.trust_a.is_trusted(self.b.spki))
        self.assertEqual([p.name for p in self.trust_a.peers()], ["Compute box"])
        self.assertEqual([p.name for p in self.trust_b.peers()], ["Living room PC"])
        # 成功後配對埠關閉
        self.assertTrue(server.join(WAIT))
        self.assertTrue(client.join(WAIT))
        with self.assertRaises(OSError):
            socket.create_connection(("127.0.0.1", server.port), timeout=5).close()

    def test_full_width_digits_are_accepted_and_bad_input_is_not_an_attempt(
        self,
    ) -> None:
        server, b_events, client, a_events, digits = self.pair_until_digits()
        for bad in ("12345", "abcdef", "1234567", ""):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                server.submit_digits(bad)
        server.submit_digits(
            digits.translate(str.maketrans("0123456789", "０１２３４５６７８９"))
        )
        b_events.until("done")
        a_events.until("confirm")
        client.confirm(True)
        a_events.until("done")

    def test_the_label_overrides_the_servers_own_name(self) -> None:
        server, b_events = self.open_server()
        client, a_events = self.start_client(server.port, label="192.168.8.236")
        digits = a_events.until("show_digits").digits or ""
        b_events.until("enter_digits")
        server.submit_digits(digits)
        b_events.until("done")
        a_events.until("confirm")
        client.confirm(True)
        a_events.until("done")
        self.assertEqual([p.name for p in self.trust_a.peers()], ["192.168.8.236"])


class UserDecisionTest(PairNetCase):
    def test_wrong_digits_on_b_abort_and_nothing_is_written(self) -> None:
        server, b_events, _client, a_events, digits = self.pair_until_digits()
        wrong = f"{(int(digits) + 1) % 1_000_000:06d}"
        server.submit_digits(wrong)
        self.assertEqual(b_events.failure(), pn.CODE_MISMATCH)
        self.assertEqual(a_events.failure(), pn.CODE_MISMATCH)
        self.assert_nothing_written()
        # 只能輸入一次：失敗之後再輸入也沒用
        with self.assertRaises(PairingError):
            server.submit_digits(digits)
        self.assertTrue(server.join(WAIT))

    def test_a_user_saying_different_writes_nothing_on_a(self) -> None:
        server, b_events, client, a_events, digits = self.pair_until_digits()
        server.submit_digits(digits)
        b_events.until("done")
        a_events.until("confirm")
        client.confirm(False)
        self.assertEqual(a_events.failure(), pn.CODE_REJECTED)
        self.assertFalse(self.trust_a_path.exists())
        # B 已寫入：單邊信任無害（A 不信任 B，連不上；重新配對會覆蓋同一個 SPKI）
        self.assertTrue(self.trust_b.is_trusted(self.a.spki))

    def test_a_cannot_confirm_before_the_result(self) -> None:
        _server, _b, client, a_events, _digits = self.pair_until_digits()
        with self.assertRaises(PairingError):
            client.confirm(True)
        # 「不一樣」任何時候都是中止
        client.confirm(False)
        self.assertEqual(a_events.failure(), pn.CODE_REJECTED)
        self.assert_nothing_written()

    def test_b_user_timeout_aborts_both_sides(self) -> None:
        _server, b_events, _client, a_events, _digits = self.pair_until_digits(
            user_timeout=0.5
        )
        self.assertEqual(b_events.failure(), pn.CODE_TIMEOUT)
        self.assertEqual(a_events.failure(), pn.CODE_TIMEOUT)
        self.assert_nothing_written()

    def test_a_confirmation_timeout_writes_nothing_on_a(self) -> None:
        server, b_events = self.open_server()
        _client, a_events = self.start_client(server.port, user_timeout=0.5)
        digits = a_events.until("show_digits").digits or ""
        b_events.until("enter_digits")
        server.submit_digits(digits)
        b_events.until("done")
        a_events.until("confirm")
        self.assertEqual(a_events.failure(), pn.CODE_TIMEOUT)
        self.assertFalse(self.trust_a_path.exists())


class SocketLifecycleTest(PairNetCase):
    """A 與 B 都不能在 websockets 讀取執行緒還活著時從別的執行緒關閉 socket。

    那條執行緒用建立時記下的 handle 做阻塞的 SSL_read；socket 先被關掉的話，Windows 會把同一個
    handle 值配給下一個新 socket，那條執行緒就讀走別條連線的資料（實際症狀：下一個測試的 HTTP
    升級請求憑空消失，伺服器 5 秒後握手逾時，A 回報 connection_lost）。
    """

    def closes_with_reader_alive(self, scenario: Any) -> list[str]:
        """執行 scenario，回傳「別的執行緒在讀取執行緒還活著時關閉 socket」的連線種類。"""
        connections: list[Any] = []
        real_connect = pn.connect
        real_server_init = pn._SafeServerConnection.__init__

        def spy_connect(*args: Any, **kwargs: Any) -> ClientConnection:
            ws = real_connect(*args, **kwargs)
            connections.append(ws)
            return ws

        def spy_server_init(self_: Any, *args: Any, **kwargs: Any) -> None:
            real_server_init(self_, *args, **kwargs)
            connections.append(self_)

        real_close = ssl.SSLSocket.close
        unsafe: list[str] = []

        def spy_close(sock: ssl.SSLSocket) -> None:
            for ws in connections:
                # 讀取執行緒在自己的收尾裡關閉 socket 是安全的；要抓的是別的執行緒先關
                reader = ws.recv_events_thread
                if (
                    ws.socket is sock
                    and threading.current_thread() is not reader
                    and reader.is_alive()
                ):
                    unsafe.append(type(ws).__name__)
            real_close(sock)

        with (
            mock.patch.object(pn, "connect", spy_connect),
            mock.patch.object(pn._SafeServerConnection, "__init__", spy_server_init),
            mock.patch.object(ssl.SSLSocket, "close", spy_close),
        ):
            scenario()
        kinds = {type(ws).__name__ for ws in connections}
        self.assertIn("SafeServerConnection", kinds, "B never accepted")
        self.assertIn("SafeClientConnection", kinds, "A never connected")
        return unsafe

    def test_cancel_waits_for_the_reader_before_closing(self) -> None:
        def scenario() -> None:
            server, b_events, client, a_events, _digits = self.pair_until_digits()
            client.cancel()
            self.assertEqual(a_events.failure(), pn.CODE_CANCELLED)
            self.assertTrue(client.join(WAIT))
            b_events.failure()
            self.assertTrue(server.join(WAIT))

        self.assertEqual(self.closes_with_reader_alive(scenario), [])

    def test_a_failure_from_b_waits_for_the_reader_before_closing(self) -> None:
        def scenario() -> None:
            server, b_events, client, a_events, digits = self.pair_until_digits()
            server.submit_digits(f"{(int(digits) + 1) % 1_000_000:06d}")
            self.assertEqual(a_events.failure(), pn.CODE_MISMATCH)
            self.assertTrue(client.join(WAIT))
            b_events.failure()
            self.assertTrue(server.join(WAIT))

        self.assertEqual(self.closes_with_reader_alive(scenario), [])


class CancelTest(PairNetCase):
    def test_cancel_on_a_closes_the_connection_at_once(self) -> None:
        server, b_events, client, a_events, _digits = self.pair_until_digits()
        client.cancel()
        self.assertEqual(a_events.failure(timeout=1.0), pn.CODE_CANCELLED)
        self.assertTrue(client.join(5.0))
        # B 正在等使用者輸入，也會發現 A 斷線
        self.assertEqual(b_events.failure(), pn.CODE_CONNECTION_LOST)
        with self.assertRaises(PairingError):
            server.submit_digits("123456")
        self.assert_nothing_written()

    def test_cancel_on_b_tells_a(self) -> None:
        server, b_events, _client, a_events, _digits = self.pair_until_digits()
        server.cancel()
        self.assertEqual(b_events.failure(), pn.CODE_CANCELLED)
        self.assertEqual(a_events.failure(), pn.CODE_CANCELLED)
        self.assertTrue(server.join(WAIT))
        self.assert_nothing_written()


class WindowTest(PairNetCase):
    def test_no_pairing_port_means_pairing_closed(self) -> None:
        _client, a_events = self.start_client(free_port())
        self.assertEqual(a_events.failure(), pn.CODE_PAIRING_CLOSED)

    def test_a_cancelled_window_refuses(self) -> None:
        server, b_events = self.open_server()
        port = server.port
        server.cancel()
        self.assertEqual(b_events.failure(), pn.CODE_CANCELLED)
        self.assertTrue(server.join(WAIT))
        _client, a_events = self.start_client(port)
        self.assertEqual(a_events.failure(), pn.CODE_PAIRING_CLOSED)

    def test_an_expired_window_closes_the_port(self) -> None:
        now = [1000.0]
        server, b_events = self.open_server(window=PairingWindow(clock=lambda: now[0]))
        port = server.port
        now[0] += 10_000
        self.assertEqual(b_events.failure(), pn.CODE_EXPIRED)
        self.assertTrue(server.join(WAIT))
        _client, a_events = self.start_client(port)
        self.assertEqual(a_events.failure(), pn.CODE_PAIRING_CLOSED)
        self.assert_nothing_written()

    def test_a_second_connection_aborts_everything(self) -> None:
        server, b_events, _client, a_events, digits = self.pair_until_digits()
        intruder = self.raw(server.port)
        self.assertEqual(abort_code(intruder), pn.CODE_CONCURRENT)
        self.assertEqual(b_events.failure(), pn.CODE_CONCURRENT)
        self.assertEqual(a_events.failure(), pn.CODE_CONCURRENT)
        self.assertTrue(server.join(WAIT))
        # 窗口已關：原本的配對也不能再完成
        with self.assertRaises(PairingError):
            server.submit_digits(digits)
        self.assert_nothing_written()

    def test_the_server_is_single_use(self) -> None:
        server, _events = self.open_server()
        with self.assertRaises(RuntimeError):
            server.open()


class HardeningTest(PairNetCase):
    def test_source_address_filter_is_reused(self) -> None:
        window = PairingWindow()
        server, b_events = self.open_server(window=window)
        with mock.patch.object(pn, "is_allowed_source", return_value=False):
            _client, a_events = self.start_client(server.port)
            self.assertEqual(a_events.failure(), pn.CODE_REFUSED)
        # 在升級前就被拒的連線不佔用窗口
        self.assertTrue(b_events.quiet_for(0.5))
        self.assertTrue(window.is_open())

    def test_browser_origin_and_unknown_path_are_refused(self) -> None:
        server, b_events = self.open_server()
        with self.assertRaises(InvalidStatus) as ctx:
            self.raw(server.port, origin="https://evil.example")
        self.assertEqual(ctx.exception.response.status_code, 403)
        with self.assertRaises(InvalidStatus) as ctx:
            self.raw(server.port, path="/kikitori/v1")
        self.assertEqual(ctx.exception.response.status_code, 404)
        self.assertTrue(b_events.quiet_for(0.3))

    def test_no_session_tickets_are_sent(self) -> None:
        # TLS 1.3 的 ticket 在握手後才送：A 的讀取執行緒處理它的同時另一條執行緒在送
        # 第一則訊息，同一個 OpenSSL SSL 物件被兩條執行緒同時使用，負載高時訊息會消失
        server, _b_events = self.open_server()
        ws = self.raw(server.port)
        ws.send("not json")  # 讓 A 這邊讀到 B 回的資料（ticket 若有會先被處理）
        abort_code(ws)
        assert isinstance(ws.socket, ssl.SSLSocket)
        session = ws.socket.session
        self.assertIsNotNone(session)
        assert session is not None
        self.assertFalse(session.has_ticket)

    def test_per_step_timeout(self) -> None:
        server, b_events = self.open_server(step_timeout=0.3)
        ws = self.raw(server.port)  # 連上後什麼都不送
        self.assertEqual(abort_code(ws), pn.CODE_TIMEOUT)
        self.assertEqual(b_events.failure(), pn.CODE_TIMEOUT)

    def test_malformed_first_messages_abort_and_write_nothing(self) -> None:
        pem = self.a.cert_path.read_text(encoding="ascii")
        commit = hashlib.sha256(b"x").hexdigest()
        valid = {"type": "pair.commit", "cert_pem": pem, "name": "A", "commit": commit}
        cases: dict[str, tuple[str | bytes, str]] = {
            "out of order": (
                json.dumps(
                    {"type": "pair.reveal", "nonce": "00" * 32, "sig": "00" * 64}
                ),
                pn.CODE_PROTOCOL,
            ),
            "unknown field": (json.dumps({**valid, "admin": True}), pn.CODE_PROTOCOL),
            "missing field": (
                json.dumps({k: v for k, v in valid.items() if k != "name"}),
                pn.CODE_PROTOCOL,
            ),
            "duplicate field": (
                json.dumps(valid)[:-1] + ', "name": "B"}',
                pn.CODE_PROTOCOL,
            ),
            "not json": ("hello", pn.CODE_PROTOCOL),
            "binary frame": (b"\x00\x01", pn.CODE_PROTOCOL),
            "uppercase hex": (
                json.dumps({**valid, "commit": commit.upper()}),
                pn.CODE_PROTOCOL,
            ),
            "short commit": (json.dumps({**valid, "commit": "ab"}), pn.CODE_PROTOCOL),
            "garbage cert": (
                json.dumps({**valid, "cert_pem": "-----BEGIN CERTIFICATE-----\nzz"}),
                pn.CODE_BAD_CERTIFICATE,
            ),
            "cert over 4 KiB": (
                json.dumps({**valid, "cert_pem": pem + " " * 4096}),
                pn.CODE_PROTOCOL,
            ),
            "ca cert": (
                json.dumps(
                    {
                        **valid,
                        "cert_pem": write_ca_identity(
                            self.root / "ca"
                        ).cert_path.read_text(encoding="ascii"),
                    }
                ),
                pn.CODE_BAD_CERTIFICATE,
            ),
            "name too long": (
                json.dumps({**valid, "name": "x" * 65}),
                pn.CODE_PROTOCOL,
            ),
        }
        for label, (message, code) in cases.items():
            with self.subTest(label):
                server, b_events = self.open_server()
                ws = self.raw(server.port)
                ws.send(message)
                self.assertEqual(abort_code(ws), code)
                self.assertEqual(b_events.failure(), code)
                self.assertTrue(server.join(WAIT))
                self.assert_nothing_written()

    def test_an_oversized_message_closes_the_connection(self) -> None:
        server, b_events = self.open_server()
        ws = self.raw(server.port)
        ws.send("x" * (pn.MAX_MESSAGE_BYTES + 1))
        self.assertEqual(abort_code(ws), "closed")
        self.assertEqual(b_events.failure(), pn.CODE_PROTOCOL)
        self.assert_nothing_written()

    def test_a_reveal_that_does_not_match_the_commitment(self) -> None:
        server, b_events = self.open_server()
        ws = self.raw(server.port)
        nonce_b = self.raw_commit(ws, secrets.token_bytes(32))
        other = secrets.token_bytes(32)  # 看了 B 的亂數之後換一個
        ws.send(
            json.dumps(
                {
                    "type": "pair.reveal",
                    "nonce": other.hex(),
                    "sig": self.proof(self.a, other, nonce_b),
                }
            )
        )
        self.assertEqual(abort_code(ws), pn.CODE_BAD_COMMITMENT)
        self.assertEqual(b_events.failure(), pn.CODE_BAD_COMMITMENT)
        self.assert_nothing_written()

    def test_a_signature_from_another_key_is_refused(self) -> None:
        # 送了別人的憑證卻沒有它的私鑰：簽章不符
        for label, signer in (("stranger", self.stranger), ("garbage", None)):
            with self.subTest(label):
                server, b_events = self.open_server()
                ws = self.raw(server.port)
                nonce_a = secrets.token_bytes(32)
                nonce_b = self.raw_commit(ws, nonce_a)
                sig = (
                    self.proof(signer, nonce_a, nonce_b)
                    if signer is not None
                    else secrets.token_bytes(64).hex()
                )
                ws.send(
                    json.dumps(
                        {"type": "pair.reveal", "nonce": nonce_a.hex(), "sig": sig}
                    )
                )
                self.assertEqual(abort_code(ws), pn.CODE_BAD_SIGNATURE)
                self.assertEqual(b_events.failure(), pn.CODE_BAD_SIGNATURE)
                self.assert_nothing_written()

    def test_the_certificate_cannot_come_in_a_later_message(self) -> None:
        # 憑證只能在 pair.commit：B 送出亂數之後才給憑證，攻擊者就能試很多把金鑰湊數字
        server, b_events = self.open_server()
        ws = self.raw(server.port)
        nonce_a = secrets.token_bytes(32)
        nonce_b = self.raw_commit(ws, nonce_a)
        ws.send(
            json.dumps(
                {
                    "type": "pair.reveal",
                    "nonce": nonce_a.hex(),
                    "sig": self.proof(self.a, nonce_a, nonce_b),
                    "cert_pem": self.stranger.cert_path.read_text(encoding="ascii"),
                }
            )
        )
        self.assertEqual(abort_code(ws), pn.CODE_PROTOCOL)
        self.assertEqual(b_events.failure(), pn.CODE_PROTOCOL)
        self.assert_nothing_written()

    def test_a_second_commit_is_out_of_order(self) -> None:
        server, b_events = self.open_server()
        ws = self.raw(server.port)
        self.raw_commit(ws, secrets.token_bytes(32))
        ws.send(
            json.dumps(
                {
                    "type": "pair.commit",
                    "cert_pem": self.a.cert_path.read_text(encoding="ascii"),
                    "name": "A",
                    "commit": "00" * 32,
                }
            )
        )
        self.assertEqual(abort_code(ws), pn.CODE_PROTOCOL)
        self.assertEqual(b_events.failure(), pn.CODE_PROTOCOL)

    def test_messages_while_b_waits_for_digits_abort(self) -> None:
        server, b_events = self.open_server()
        ws = self.raw(server.port)
        nonce_a = secrets.token_bytes(32)
        nonce_b = self.raw_commit(ws, nonce_a)
        ws.send(
            json.dumps(
                {
                    "type": "pair.reveal",
                    "nonce": nonce_a.hex(),
                    "sig": self.proof(self.a, nonce_a, nonce_b),
                }
            )
        )
        b_events.until("enter_digits")
        ws.send(json.dumps({"type": "pair.result", "ok": True}))
        self.assertEqual(abort_code(ws), pn.CODE_PROTOCOL)
        self.assertEqual(b_events.failure(), pn.CODE_PROTOCOL)
        self.assert_nothing_written()


class FakeServerCase(PairNetCase):
    """A 對上不照規矩來的 B：B 的回覆一樣是不可信的輸入。"""

    def fake_b(self, handler: Any, identity: Identity | None = None) -> int:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        who = identity or self.b
        context.load_cert_chain(who.cert_path, who.key_path)
        context.num_tickets = (
            0  # 與 PairingServer 相同（見 test_no_session_tickets_are_sent）
        )
        stop = threading.Event()

        def wrapped(ws: ServerConnection) -> None:
            try:
                handler(ws)
                stop.wait(WAIT)
            except ConnectionClosed:
                pass  # A 中止並斷線：這正是測試要看的

        server = serve(
            wrapped,
            "127.0.0.1",
            0,
            ssl=context,
            compression=None,
            create_connection=pn._SafeServerConnection,
        )
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.shutdown)
        self.addCleanup(stop.set)
        return int(server.socket.getsockname()[1])

    def test_a_times_out_waiting_for_the_nonce(self) -> None:
        port = self.fake_b(lambda ws: ws.recv(timeout=WAIT))
        _client, a_events = self.start_client(port, step_timeout=0.5)
        self.assertEqual(a_events.failure(), pn.CODE_TIMEOUT)
        self.assert_nothing_written()

    def test_a_rejects_unknown_fields_and_codes_from_b(self) -> None:
        replies = {
            "extra field": {
                "type": "pair.nonce",
                "nonce": "00" * 32,
                "name": "B",
                "cert_pem": "x",
            },
            "unknown abort code": {"type": "pair.abort", "code": "run_this"},
            "result too early": {"type": "pair.result", "ok": True},
        }
        for label, reply in replies.items():
            with self.subTest(label):

                def handler(
                    ws: ServerConnection, reply: dict[str, Any] = reply
                ) -> None:
                    ws.recv(timeout=WAIT)
                    ws.send(json.dumps(reply))

                _client, a_events = self.start_client(self.fake_b(handler))
                self.assertEqual(a_events.failure(), pn.CODE_PROTOCOL)
                self.assert_nothing_written()

    def test_a_refuses_a_server_certificate_that_could_sign_others(self) -> None:
        ca = write_ca_identity(self.root / "evil")
        port = self.fake_b(lambda ws: ws.recv(timeout=WAIT), identity=ca)
        _client, a_events = self.start_client(port)
        self.assertEqual(a_events.failure(), pn.CODE_BAD_CERTIFICATE)
        self.assert_nothing_written()

    def test_a_does_not_trust_a_result_ok_false(self) -> None:
        def handler(ws: ServerConnection) -> None:
            ws.recv(timeout=WAIT)
            ws.send(json.dumps({"type": "pair.nonce", "nonce": "11" * 32, "name": "B"}))
            ws.recv(timeout=WAIT)
            ws.send(json.dumps({"type": "pair.result", "ok": False}))

        _client, a_events = self.start_client(self.fake_b(handler))
        a_events.until("show_digits")
        self.assertEqual(a_events.failure(), pn.CODE_PROTOCOL)
        self.assert_nothing_written()


class HelperTest(unittest.TestCase):
    def test_pairing_port_is_main_port_plus_one(self) -> None:
        self.assertEqual(pn.pairing_port(47100), 47101)
        for bad in (0, 65535, -1, True):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                pn.pairing_port(bad)

    def test_normalize_digits(self) -> None:
        self.assertEqual(pn.normalize_digits(" 123 456 "), "123456")
        self.assertEqual(pn.normalize_digits("１２３４５６"), "123456")
        for bad in ("12345", "1234567", "12345a", "١٢٣٤٥٦", "x" * 100):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                pn.normalize_digits(bad)

    def test_proof_signature_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            me = load_or_create_identity(Path(tmp) / "me")
            other = load_or_create_identity(Path(tmp) / "other")
            pem = me.cert_path.read_text(encoding="ascii")
            message = pn.proof_message(me.spki, other.spki, b"a" * 32, b"b" * 32)
            sig = pn.sign_proof(me, message)
            self.assertEqual(len(sig), pn.SIGNATURE_BYTES)
            self.assertTrue(pn.verify_proof(pem, sig, message))
            self.assertFalse(pn.verify_proof(pem, sig, message + b"x"))
            self.assertFalse(
                pn.verify_proof(pem, pn.sign_proof(other, message), message)
            )
            self.assertFalse(pn.verify_proof(pem, b"\0" * 64, message))

    def test_client_validates_host_and_port(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            me = load_or_create_identity(Path(tmp) / "me")
            trust = TrustStore(Path(tmp) / "servers.json")
            for host, port in (
                ("", 1),
                ("a b", 1),
                ("x/y", 1),
                ("ok", 0),
                ("ok", 70000),
            ):
                with self.subTest(host=host, port=port), self.assertRaises(ValueError):
                    pn.PairingClient(
                        identity=me,
                        trust=trust,
                        name="A",
                        host=host,
                        port=port,
                        on_update=lambda _u: None,
                    )


if __name__ == "__main__":
    unittest.main()
