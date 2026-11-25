"""遠端運算：這台電腦自己的身分（自簽憑證與私鑰）。

位於 remote 模組的身分層（docs/specs/remote-compute.md「身分與配對」）。每台電腦第一次啟用遠端
功能時產生一組 ECDSA P-256 金鑰與自簽憑證；配對時雙方交換並記住對方憑證的 SPKI（公鑰的
SHA-256），之後每次連線都是雙向 TLS。

- 憑證標成 `BasicConstraints(ca=False)`：已配對的金鑰不能再簽出別的憑證來冒充（Sunshine GHSA-6jvv）。
  實驗確認 OpenSSL 把這種自簽憑證當信任錨時接受它本身、拒絕它簽出的憑證（invalid CA certificate）。
- 私鑰以只有本人可讀的權限建立（POSIX 0600；Windows 在使用者設定資料夾內，靠該資料夾的 ACL）。
- 檔案損毀或不相符時丟 `IdentityError`，**不會悄悄重產**：重產會讓所有配對失效，也可能掩蓋竄改；
  由使用者在介面上明確重設。
"""

import datetime as dt
import hashlib
import os
import secrets
from dataclasses import dataclass
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

CERT_NAME = "identity.crt"
KEY_NAME = "identity.key"
VALIDITY = dt.timedelta(days=20 * 365)
"""信任靠 SPKI pin，不靠有效期限；給長的期限，避免使用者某天突然連不上。"""


class IdentityError(RuntimeError):
    """身分檔案損毀、不完整或金鑰與憑證不相符。"""


@dataclass(frozen=True)
class Identity:
    """這台電腦的身分。

    Attributes:
        cert_path: PEM 憑證（給 `SSLContext.load_cert_chain`）。
        key_path: PEM 私鑰（PKCS#8，不加密；靠檔案權限保護）。
        spki: 公鑰（SubjectPublicKeyInfo DER）的 SHA-256，配對與 pin 用。
    """

    cert_path: Path
    key_path: Path
    spki: bytes

    @property
    def fingerprint(self) -> str:
        """`spki` 的十六進位（顯示與存檔用）。"""
        return self.spki.hex()


def spki_sha256(cert: x509.Certificate) -> bytes:
    """憑證公鑰的 SHA-256（RFC 7469 的 pin 定義；換憑證但不換金鑰時不變）。"""
    der = cert.public_key().public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    return hashlib.sha256(der).digest()


def spki_sha256_der(cert_der: bytes) -> bytes:
    """DER 憑證（`SSLSocket.getpeercert(binary_form=True)`）的 SPKI SHA-256。"""
    return spki_sha256(x509.load_der_x509_certificate(cert_der))


def sign(identity: Identity, data: bytes) -> bytes:
    """以這台電腦的私鑰對 `data` 做 ECDSA-SHA256 簽章（DER）。

    配對時用來證明 A 真的持有它送出的憑證的私鑰（`pair.reveal` 的 `sig`）。每次從檔案讀取，
    不把私鑰留在記憶體裡的長壽物件上。

    Raises:
        IdentityError: 私鑰檔損毀或不是 P-256。
        OSError: 無法讀取私鑰檔。
    """
    try:
        key = serialization.load_pem_private_key(
            identity.key_path.read_bytes(), password=None
        )
    except ValueError as exc:
        raise IdentityError("the identity key is damaged; reset it") from exc
    if not isinstance(key, ec.EllipticCurvePrivateKey) or key.curve.name != "secp256r1":
        raise IdentityError("the identity key is not ECDSA P-256")
    return key.sign(data, ec.ECDSA(hashes.SHA256()))


def _write_private(path: Path, data: bytes) -> None:
    # O_EXCL：不覆蓋既有檔案；0o600：只有本人可讀寫（Windows 忽略模式，靠設定資料夾的 ACL）
    fd = os.open(
        path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o600
    )
    with os.fdopen(fd, "wb") as fh:
        fh.write(data)


def _create(directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name(
        [x509.NameAttribute(NameOID.COMMON_NAME, f"kikitori {secrets.token_hex(4)}")]
    )
    now = dt.datetime.now(dt.UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(days=1))
        .not_valid_after(now + VALIDITY)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=False,
                crl_sign=False,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .sign(key, hashes.SHA256())
    )
    _write_private(
        directory / KEY_NAME,
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ),
    )
    (directory / CERT_NAME).write_bytes(cert.public_bytes(serialization.Encoding.PEM))


def reset_identity(directory: Path) -> Identity:
    """刪掉 `directory` 裡的身分（損毀或缺檔也可以）並產生新的。

    只在使用者明確要求時呼叫：其他電腦記的是舊憑證，所有配對都要重做。

    Raises:
        OSError: 無法刪除或寫入檔案。
        IdentityError: 剛寫好的檔案讀不回來（例如另一個行程同時在寫）。
    """
    for name in (KEY_NAME, CERT_NAME):
        (directory / name).unlink(missing_ok=True)
    return load_or_create_identity(directory)


def load_or_create_identity(directory: Path) -> Identity:
    """讀取 `directory` 裡的身分；兩個檔案都不存在時產生新的。

    Raises:
        IdentityError: 只有其中一個檔案、檔案損毀、不是 P-256 或金鑰與憑證不相符。
        OSError: 無法建立或讀取檔案。
    """
    cert_path, key_path = directory / CERT_NAME, directory / KEY_NAME
    if not cert_path.exists() and not key_path.exists():
        _create(directory)
    elif not (cert_path.is_file() and key_path.is_file()):
        raise IdentityError(f"the identity in {directory} is incomplete; reset it")
    try:
        cert = x509.load_pem_x509_certificate(cert_path.read_bytes())
        key = serialization.load_pem_private_key(key_path.read_bytes(), password=None)
    except ValueError as exc:
        raise IdentityError(
            f"the identity in {directory} is damaged; reset it"
        ) from exc
    if not isinstance(key, ec.EllipticCurvePrivateKey) or key.curve.name != "secp256r1":
        raise IdentityError("the identity key is not ECDSA P-256")
    public = cert.public_key()
    if not isinstance(public, ec.EllipticCurvePublicKey) or (
        public.public_numbers() != key.public_key().public_numbers()
    ):
        raise IdentityError("the identity key does not match its certificate")
    return Identity(cert_path, key_path, spki_sha256(cert))
