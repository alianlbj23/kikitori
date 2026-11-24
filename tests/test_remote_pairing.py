"""遠端運算的配對：兩邊比對數字、配對窗口、信任清單（docs/specs/remote-compute.md「身分與配對」）。"""

import datetime as dt
import json
import secrets
import tempfile
import unittest
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from kikitori.remote.identity import load_or_create_identity
from kikitori.remote.pairing import (
    PAIRING_WINDOW_SECS,
    ClientPairing,
    PairingError,
    PairingWindow,
    ServerPairing,
    TrustStore,
    comparison_digits,
)

SPKI_A = secrets.token_bytes(32)
SPKI_B = secrets.token_bytes(32)


def run_pairing(client: ClientPairing, server: ServerPairing) -> tuple[str, str]:
    """誠實的雙方照順序交換三則訊息。"""
    nonce_b = server.receive_commit(client.commit())
    return client.receive_nonce(nonce_b), server.receive_reveal(client.reveal())


class ComparisonTest(unittest.TestCase):
    def test_honest_peers_show_the_same_six_digits(self) -> None:
        a, b = run_pairing(
            ClientPairing(own=SPKI_A, peer=SPKI_B),
            ServerPairing(own=SPKI_B, peer=SPKI_A),
        )
        self.assertEqual(a, b)
        self.assertRegex(a, r"^\d{6}$")

    def test_a_man_in_the_middle_makes_the_numbers_differ(self) -> None:
        # 中間人 M 對 A 冒充 B、對 B 冒充 A：A 看到的是 (A, M)，B 看到的是 (M, B)
        spki_m = secrets.token_bytes(32)
        mismatches = 0
        for _ in range(20):
            a_side, _m1 = run_pairing(
                ClientPairing(own=SPKI_A, peer=spki_m),
                ServerPairing(own=spki_m, peer=SPKI_A),
            )
            _m2, b_side = run_pairing(
                ClientPairing(own=spki_m, peer=SPKI_B),
                ServerPairing(own=SPKI_B, peer=spki_m),
            )
            mismatches += a_side != b_side
        self.assertEqual(mismatches, 20)

    def test_the_numbers_depend_on_every_input(self) -> None:
        base = comparison_digits(SPKI_A, SPKI_B, b"a" * 32, b"b" * 32)
        self.assertNotEqual(
            base, comparison_digits(SPKI_B, SPKI_A, b"a" * 32, b"b" * 32)
        )
        self.assertNotEqual(
            base, comparison_digits(SPKI_A, SPKI_B, b"c" * 32, b"b" * 32)
        )
        self.assertNotEqual(
            base, comparison_digits(SPKI_A, SPKI_B, b"a" * 32, b"c" * 32)
        )

    def test_a_reveal_that_does_not_match_the_commitment_fails(self) -> None:
        # 先承諾後公開：看到 B 的亂數之後才換 A 的亂數，會被抓到
        client = ClientPairing(own=SPKI_A, peer=SPKI_B)
        server = ServerPairing(own=SPKI_B, peer=SPKI_A)
        server.receive_commit(client.commit())
        with self.assertRaises(PairingError):
            server.receive_reveal(secrets.token_bytes(32))

    def test_steps_out_of_order_fail(self) -> None:
        server = ServerPairing(own=SPKI_B, peer=SPKI_A)
        with self.assertRaises(PairingError):
            server.receive_reveal(b"x" * 32)  # 還沒收到承諾
        client = ClientPairing(own=SPKI_A, peer=SPKI_B)
        with self.assertRaises(PairingError):
            client.reveal()  # 還沒收到 B 的亂數
        server.receive_commit(client.commit())
        with self.assertRaises(PairingError):
            server.receive_commit(client.commit())  # 重送承諾
        with self.assertRaises(PairingError):
            client.commit()  # 承諾只能送一次

    def test_malformed_values_fail(self) -> None:
        with self.assertRaises(PairingError):
            ServerPairing(own=SPKI_B, peer=SPKI_A).receive_commit(b"short")
        client = ClientPairing(own=SPKI_A, peer=SPKI_B)
        client.commit()
        with self.assertRaises(PairingError):
            client.receive_nonce(b"short")
        with self.assertRaises(ValueError):
            ClientPairing(own=b"x", peer=SPKI_B)


class WindowTest(unittest.TestCase):
    def test_open_for_five_minutes_and_one_pairing_at_a_time(self) -> None:
        now = [1000.0]
        window = PairingWindow(clock=lambda: now[0])
        self.assertFalse(window.is_open())
        window.open()
        self.assertTrue(window.is_open())
        self.assertTrue(window.claim())
        self.assertFalse(window.claim())  # Sunshine GHSA-36ff：同時只能有一個待配對
        window.release()
        self.assertTrue(window.claim())
        now[0] += PAIRING_WINDOW_SECS + 1
        self.assertFalse(window.is_open())
        window.release()
        self.assertFalse(window.claim())  # 過期後不再接受

    def test_closing_ends_it_at_once(self) -> None:
        window = PairingWindow(clock=lambda: 0.0)
        window.open()
        window.close()
        self.assertFalse(window.is_open())
        self.assertFalse(window.claim())


class TrustStoreTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.path = root / "remote" / "trusted.json"
        self.peer = load_or_create_identity(root / "peer")
        self.peer_pem = self.peer.cert_path.read_bytes()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_add_list_remove_and_persist(self) -> None:
        store = TrustStore(self.path)
        self.assertFalse(store.is_trusted(self.peer.spki))
        self.assertEqual(store.add(self.peer_pem, "Living room PC"), self.peer.spki)
        reloaded = TrustStore(self.path)
        self.assertTrue(reloaded.is_trusted(self.peer.spki))
        self.assertEqual([p.name for p in reloaded.peers()], ["Living room PC"])
        # OpenSSL 的信任錨：對方的完整憑證
        self.assertIn(self.peer_pem.decode().strip(), reloaded.trusted_pem())
        self.assertTrue(store.remove(self.peer.spki))
        self.assertFalse(TrustStore(self.path).is_trusted(self.peer.spki))
        self.assertEqual(TrustStore(self.path).trusted_pem(), "")
        self.assertFalse(store.remove(self.peer.spki))

    def test_clear_forgets_everyone(self) -> None:
        store = TrustStore(self.path)
        store.add(self.peer_pem, "Living room PC")
        store.clear()
        self.assertEqual(TrustStore(self.path).peers(), [])
        self.assertFalse(TrustStore(self.path).is_trusted(self.peer.spki))
        TrustStore(
            Path(self._tmp.name) / "none" / "trusted.json"
        ).clear()  # 沒有檔案也可以

    def test_refuses_certificates_that_could_sign_others(self) -> None:
        # 能當 CA 的憑證放進信任錨，就能用它簽出新的憑證冒充（Sunshine GHSA-6jvv）
        key = ec.generate_private_key(ec.SECP256R1())
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "ca")])
        now = dt.datetime.now(dt.UTC)
        ca = (
            x509.CertificateBuilder()
            .subject_name(name)
            .issuer_name(name)
            .public_key(key.public_key())
            .serial_number(1)
            .not_valid_before(now)
            .not_valid_after(now + dt.timedelta(days=1))
            .add_extension(
                x509.BasicConstraints(ca=True, path_length=None), critical=True
            )
            .sign(key, hashes.SHA256())
        )
        store = TrustStore(self.path)
        for bad in (ca.public_bytes(serialization.Encoding.PEM), b"garbage"):
            with self.subTest(bad=bad[:20]), self.assertRaises(ValueError):
                store.add(bad, "x")
        self.assertFalse(self.path.exists())

    def test_nothing_is_written_until_a_pairing_is_added(self) -> None:
        # CVE-2024-45407：配對沒有完整成功之前，信任清單不能有任何紀錄
        store = TrustStore(self.path)
        run_pairing(
            ClientPairing(own=SPKI_A, peer=SPKI_B),
            ServerPairing(own=SPKI_B, peer=SPKI_A),
        )
        self.assertFalse(self.path.exists())
        self.assertEqual(store.peers(), [])

    def test_a_damaged_file_trusts_nobody(self) -> None:
        self.path.parent.mkdir(parents=True)
        for content in (
            "garbage",
            json.dumps({"peers": [{"cert": "zz", "name": "x", "paired_at": "t"}]}),
            json.dumps([1]),
        ):
            with self.subTest(content=content):
                self.path.write_text(content, encoding="utf-8")
                store = TrustStore(self.path)
                self.assertEqual(store.peers(), [])
                self.assertFalse(store.is_trusted(self.peer.spki))
                self.assertEqual(store.trusted_pem(), "")

    def test_names_are_cleaned_and_limited(self) -> None:
        store = TrustStore(self.path)
        store.add(self.peer_pem, "  bad\nname\x00" + "x" * 200)
        name = store.peers()[0].name
        self.assertNotIn("\n", name)
        self.assertNotIn("\x00", name)
        self.assertLessEqual(len(name), 64)


if __name__ == "__main__":
    unittest.main()
