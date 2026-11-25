"""遠端運算：每台電腦自己的憑證與私鑰（docs/specs/remote-compute.md「身分與配對」）。"""

import os
import stat
import tempfile
import unittest
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives.asymmetric import ec

from kikitori.remote.identity import (
    IdentityError,
    load_or_create_identity,
    reset_identity,
    spki_sha256,
)


class IdentityTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name) / "remote"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_creates_a_p256_leaf_that_cannot_sign_other_certificates(self) -> None:
        identity = load_or_create_identity(self.dir)
        cert = x509.load_pem_x509_certificate(identity.cert_path.read_bytes())
        self.assertIsInstance(cert.public_key(), ec.EllipticCurvePublicKey)
        self.assertEqual(cert.public_key().curve.name, "secp256r1")
        basic = cert.extensions.get_extension_for_class(x509.BasicConstraints)
        self.assertTrue(basic.critical)
        self.assertFalse(basic.value.ca)  # Sunshine GHSA-6jvv：不能拿來簽衍生憑證
        self.assertEqual(identity.spki, spki_sha256(cert))
        self.assertEqual(len(identity.spki), 32)
        self.assertEqual(identity.fingerprint, identity.spki.hex())

    def test_reloads_the_same_identity(self) -> None:
        first = load_or_create_identity(self.dir)
        second = load_or_create_identity(self.dir)
        self.assertEqual(first.spki, second.spki)
        self.assertEqual(first.cert_path, second.cert_path)

    def test_two_computers_get_different_identities(self) -> None:
        other = Path(self._tmp.name) / "other"
        self.assertNotEqual(
            load_or_create_identity(self.dir).spki, load_or_create_identity(other).spki
        )

    @unittest.skipIf(os.name == "nt", "POSIX file modes")
    def test_private_key_is_readable_only_by_the_owner(self) -> None:
        identity = load_or_create_identity(self.dir)
        mode = stat.S_IMODE(identity.key_path.stat().st_mode)
        self.assertEqual(mode & 0o077, 0, oct(mode))

    def test_a_key_that_does_not_match_the_certificate_is_refused(self) -> None:
        load_or_create_identity(self.dir)
        other = Path(self._tmp.name) / "other"
        load_or_create_identity(other)
        (self.dir / "identity.key").write_bytes((other / "identity.key").read_bytes())
        with self.assertRaises(IdentityError):
            load_or_create_identity(self.dir)

    def test_a_damaged_file_is_refused_not_silently_replaced(self) -> None:
        # 悄悄重產會讓所有配對失效，也可能掩蓋被竄改：要使用者明確重設
        load_or_create_identity(self.dir)
        (self.dir / "identity.crt").write_text("garbage", encoding="utf-8")
        with self.assertRaises(IdentityError):
            load_or_create_identity(self.dir)

    def test_only_one_of_the_two_files_is_refused(self) -> None:
        load_or_create_identity(self.dir)
        (self.dir / "identity.key").unlink()
        with self.assertRaises(IdentityError):
            load_or_create_identity(self.dir)

    def test_reset_makes_a_new_identity_even_from_damaged_files(self) -> None:
        # 使用者明確要求重建（設定頁或運算主機選單）：損毀、缺一個檔案或好好的都換成新的
        first = load_or_create_identity(self.dir)
        (self.dir / "identity.crt").write_text("garbage", encoding="utf-8")
        second = reset_identity(self.dir)
        self.assertNotEqual(second.spki, first.spki)
        self.assertEqual(load_or_create_identity(self.dir).spki, second.spki)
        (self.dir / "identity.key").unlink()
        third = reset_identity(self.dir)
        self.assertNotEqual(third.spki, second.spki)
        self.assertEqual(len(reset_identity(Path(self._tmp.name) / "new").spki), 32)


if __name__ == "__main__":
    unittest.main()
