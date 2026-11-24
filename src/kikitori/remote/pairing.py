"""遠端運算的配對：兩邊比對數字、配對窗口與信任清單。

位於 remote 模組的配對層（docs/specs/remote-compute.md「身分與配對」），只有邏輯、不碰網路，
連線層（`remote/pair_net.py`）把三則訊息（承諾、B 的亂數、公開）放進配對用的 TLS 連線。

**兩邊比對數字（類似藍牙 Numeric Comparison）**：A 先送 `SHA-256(nonce_A)` 作為承諾，B 回
`nonce_B`，A 再公開 `nonce_A`；雙方各自算
`SHA-256("kikitori-pair-v1" ‖ spki_A ‖ spki_B ‖ nonce_A ‖ nonce_B)` 取 6 位數顯示，使用者看到兩邊
相同、兩邊都按「確認」才算成功。中間人兩邊各換成自己的公鑰，兩邊數字就幾乎一定不同（要剛好一樣
只有百萬分之一）；先承諾後公開讓它無法看了 B 的亂數再挑 A 的亂數來湊。原本「配對碼 HMAC」的設計
會被配對當下的中間人離線試完 100 萬組碼（Sunshine CVE-2024-51738），已放棄。

**信任清單只在兩邊都確認之後才寫入**（`TrustStore.add` 由連線層在確認後呼叫）：配對失敗不能留下
任何紀錄（Sunshine CVE-2024-45407）。
"""

import hashlib
import hmac
import json
import logging
import os
import secrets
import threading
import time
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum, auto
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from kikitori.remote.identity import spki_sha256

logger = logging.getLogger(__name__)

NONCE_BYTES = 32
SPKI_BYTES = 32
DIGITS = 6
PAIRING_WINDOW_SECS = 5 * 60
MAX_PEERS = 32
MAX_NAME_CHARS = 64
_LABEL = b"kikitori-pair-v1"


class PairingError(RuntimeError):
    """配對失敗：順序錯誤、承諾不符或值的格式不對。連線層應中止配對、關閉連線。"""


def comparison_digits(
    spki_a: bytes, spki_b: bytes, nonce_a: bytes, nonce_b: bytes
) -> str:
    """兩台電腦各自顯示的 6 位數（A 是客戶端、B 是運算主機）。"""
    digest = hashlib.sha256(_LABEL + spki_a + spki_b + nonce_a + nonce_b).digest()
    return f"{int.from_bytes(digest[:8], 'big') % 10**DIGITS:0{DIGITS}d}"


def _check(value: bytes, size: int, what: str) -> bytes:
    if not isinstance(value, bytes) or len(value) != size:
        raise PairingError(f"{what} must be {size} bytes")
    return value


class _Step(Enum):
    START = auto()
    COMMITTED = auto()
    NONCE_RECEIVED = auto()
    DONE = auto()


class ClientPairing:
    """A 端（要連去運算主機的那台）：承諾 → 收 B 的亂數 → 公開。一個物件只配對一次。"""

    def __init__(self, own: bytes, peer: bytes) -> None:
        """
        Args:
            own: 這台電腦的 SPKI SHA-256。
            peer: 配對連線上看到的對方憑證 SPKI SHA-256。

        Raises:
            ValueError: SPKI 長度不對。
        """
        if len(own) != SPKI_BYTES or len(peer) != SPKI_BYTES:
            raise ValueError("SPKI hashes must be 32 bytes")
        self._own, self._peer = own, peer
        self._nonce = secrets.token_bytes(NONCE_BYTES)
        self._peer_nonce = b""
        self._step = _Step.START

    def commit(self) -> bytes:
        """第一則：對自己亂數的承諾。"""
        if self._step is not _Step.START:
            raise PairingError("the commitment was already sent")
        self._step = _Step.COMMITTED
        return hashlib.sha256(self._nonce).digest()

    def receive_nonce(self, nonce_b: bytes) -> str:
        """收到 B 的亂數；回傳要顯示的 6 位數。"""
        if self._step is not _Step.COMMITTED:
            raise PairingError("unexpected nonce")
        self._peer_nonce = _check(nonce_b, NONCE_BYTES, "the peer nonce")
        self._step = _Step.NONCE_RECEIVED
        return comparison_digits(self._own, self._peer, self._nonce, self._peer_nonce)

    def reveal(self) -> bytes:
        """第三則：公開自己的亂數（收到 B 的亂數之後才可以）。"""
        if self._step is not _Step.NONCE_RECEIVED:
            raise PairingError("cannot reveal before receiving the peer nonce")
        self._step = _Step.DONE
        return self._nonce


class ServerPairing:
    """B 端（運算主機）：收承諾 → 送自己的亂數 → 收公開並驗證。一個物件只配對一次。

    B 的配對埠不要求用戶端憑證（Python 的 `ssl` 無法「要求但不驗證」），A 的憑證放在
    `pair.commit` 裡送來；連線層驗過那張憑證之後才建立這個物件。
    """

    def __init__(self, own: bytes, peer: bytes) -> None:
        """
        Args:
            own: 這台電腦的 SPKI SHA-256。
            peer: A 在 `pair.commit` 送來（已驗證）的憑證 SPKI SHA-256。

        Raises:
            ValueError: SPKI 長度不對。
        """
        if len(own) != SPKI_BYTES or len(peer) != SPKI_BYTES:
            raise ValueError("SPKI hashes must be 32 bytes")
        self._own, self._peer = own, peer
        self._nonce = secrets.token_bytes(NONCE_BYTES)
        self._commitment = b""
        self._step = _Step.START

    def receive_commit(self, commitment: bytes) -> bytes:
        """收到 A 的承諾；回傳要送給 A 的亂數。"""
        if self._step is not _Step.START:
            raise PairingError("unexpected commitment")
        self._commitment = _check(
            commitment, hashlib.sha256().digest_size, "the commitment"
        )
        self._step = _Step.COMMITTED
        return self._nonce

    def receive_reveal(self, nonce_a: bytes) -> str:
        """收到 A 公開的亂數並驗證承諾；回傳要顯示的 6 位數（注意 A 在前、B 在後）。"""
        if self._step is not _Step.COMMITTED:
            raise PairingError("unexpected reveal")
        _check(nonce_a, NONCE_BYTES, "the revealed nonce")
        self._step = _Step.DONE
        if not hmac.compare_digest(hashlib.sha256(nonce_a).digest(), self._commitment):
            raise PairingError("the revealed nonce does not match the commitment")
        return comparison_digits(self._peer, self._own, nonce_a, self._nonce)


class PairingWindow:
    """B 端的配對窗口：使用者按下後開 5 分鐘，同時只允許一個待配對連線（Sunshine GHSA-36ff）。"""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        self._until = 0.0
        self._claimed = False

    def open(self) -> None:
        """使用者在 B 上按「允許新的電腦連線」。"""
        with self._lock:
            self._until = self._clock() + PAIRING_WINDOW_SECS
            self._claimed = False

    def close(self) -> None:
        """配對完成、取消或失敗：立刻關閉。"""
        with self._lock:
            self._until = 0.0
            self._claimed = False

    def is_open(self) -> bool:
        """窗口是否還開著。"""
        with self._lock:
            return self._clock() < self._until

    def claim(self) -> bool:
        """一個配對連線佔用窗口；已有人佔用或已過期時回 False（連線層應立刻斷開）。"""
        with self._lock:
            if self._claimed or self._clock() >= self._until:
                return False
            self._claimed = True
            return True

    def release(self) -> None:
        """待配對的連線結束（沒有成功）：讓下一個連線可以試。"""
        with self._lock:
            self._claimed = False


@dataclass(frozen=True)
class TrustedPeer:
    """一台已配對的電腦。"""

    spki: bytes
    cert_pem: str
    name: str
    paired_at: str


def _clean_name(name: str) -> str:
    text = "".join(
        " " if unicodedata.category(ch).startswith("C") else ch for ch in name
    )
    return " ".join(text.split())[:MAX_NAME_CHARS] or "kikitori"


def _peer_certificate(cert_pem: bytes) -> tuple[bytes, str]:
    """驗證對方的憑證能當信任錨（P-256、不能當 CA），回傳 (SPKI SHA-256, 正規化的 PEM)。

    Raises:
        ValueError: 不是 PEM 憑證、不是 P-256，或標成可以簽發其他憑證（Sunshine GHSA-6jvv）。
    """
    cert = x509.load_pem_x509_certificate(cert_pem)
    public = cert.public_key()
    if (
        not isinstance(public, ec.EllipticCurvePublicKey)
        or public.curve.name != "secp256r1"
    ):
        raise ValueError("the peer certificate is not ECDSA P-256")
    try:
        basic = cert.extensions.get_extension_for_class(x509.BasicConstraints).value
    except x509.ExtensionNotFound as exc:
        raise ValueError("the peer certificate has no basic constraints") from exc
    if basic.ca:
        raise ValueError("the peer certificate may sign other certificates")
    return spki_sha256(cert), cert.public_bytes(serialization.Encoding.PEM).decode(
        "ascii"
    )


class TrustStore:
    """已配對電腦的清單（JSON 檔，只有本人可讀寫）：對方的完整憑證與名稱。

    完整憑證是 OpenSSL 雙向 TLS 的信任錨（`trusted_pem()`）；連線層握手後再比對 SPKI。
    檔案損毀時誰都不信任，並記 warning。
    """

    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = threading.Lock()

    def _load(self) -> list[TrustedPeer]:
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return []
        except (OSError, ValueError) as exc:
            logger.warning("ignoring an unreadable trust list %s: %s", self._path, exc)
            return []
        peers: list[TrustedPeer] = []
        try:
            for item in raw["peers"][:MAX_PEERS]:
                spki, pem = _peer_certificate(str(item["cert"]).encode("ascii"))
                peers.append(
                    TrustedPeer(
                        spki,
                        pem,
                        _clean_name(str(item["name"])),
                        str(item["paired_at"]),
                    )
                )
        except (TypeError, KeyError, ValueError, UnicodeError) as exc:
            logger.warning("ignoring a damaged trust list %s: %s", self._path, exc)
            return []
        return peers

    def _save(self, peers: list[TrustedPeer]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "peers": [
                {"cert": p.cert_pem, "name": p.name, "paired_at": p.paired_at}
                for p in peers
            ]
        }
        tmp = self._path.with_name(self._path.name + ".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
        os.replace(tmp, self._path)

    def peers(self) -> list[TrustedPeer]:
        """目前已配對的電腦。"""
        with self._lock:
            return self._load()

    def trusted_pem(self) -> str:
        """所有已配對電腦的憑證（給 `SSLContext.load_verify_locations(cadata=...)`）；沒有時為空字串。"""
        with self._lock:
            return "".join(p.cert_pem for p in self._load())

    def is_trusted(self, spki: bytes) -> bool:
        """這個 SPKI 是否在清單上（常數時間比對每一筆）。"""
        with self._lock:
            found = False
            for peer in self._load():
                found |= hmac.compare_digest(peer.spki, spki)
            return found

    def add(self, cert_pem: bytes, name: str) -> bytes:
        """兩邊都確認之後才呼叫：加入對方的憑證（已存在則更新名稱），回傳它的 SPKI。

        Raises:
            ValueError: 不是可用的憑證（見 `_peer_certificate`）或已達上限。
        """
        spki, pem = _peer_certificate(cert_pem)
        with self._lock:
            peers = [p for p in self._load() if p.spki != spki]
            if len(peers) >= MAX_PEERS:
                raise ValueError(f"at most {MAX_PEERS} paired computers")
            when = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            peers.append(TrustedPeer(spki, pem, _clean_name(name), when))
            self._save(peers)
        return spki

    def clear(self) -> None:
        """移除全部（重建這台電腦的身分後，舊的配對都不能用了）。"""
        with self._lock:
            if self._path.exists():
                self._save([])

    def remove(self, spki: bytes) -> bool:
        """移除一台；回傳是否有移除（連線層要另外立刻斷開它的連線、重建 TLS 設定）。"""
        with self._lock:
            peers = self._load()
            kept = [p for p in peers if p.spki != spki]
            if len(kept) == len(peers):
                return False
            self._save(kept)
            return True
