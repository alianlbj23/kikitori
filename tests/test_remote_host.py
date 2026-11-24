"""無介面的運算主機（`kikitori` 選單 →「運算主機」）：選監聽位址、配對、移除、提供運算、準備模型、
選擇模型、目前負荷。

配對與提供運算用真的連線（127.0.0.1）；準備模型與選擇模型用假的引擎驅動（記錄送了哪些請求）
與假的能力（`HostInfo`），不下載任何東西、不跑 nvidia-smi。
"""

import os
import queue
import socket
import tempfile
import threading
import unittest
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest import mock

from kikitori.config import SettingsStore, default_settings_path
from kikitori.remote import host as h
from kikitori.remote import pair_net as pn
from kikitori.remote.identity import IdentityError, load_or_create_identity
from kikitori.remote.pairing import TrustStore

WAIT = 20.0


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class Script:
    """依序回答選單的 input；答案可以是字串或「等某件事再回答」的函式。輸入用完視同 EOF。"""

    def __init__(self, *answers: str | Callable[[], str]) -> None:
        self.answers = list(answers)
        self.prompts: list[str] = []

    def __call__(self, prompt: str) -> str:
        self.prompts.append(prompt)
        if not self.answers:
            raise EOFError
        answer = self.answers.pop(0)
        return answer() if callable(answer) else answer


class HostCase(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        env = mock.patch.dict(os.environ, {"KIKITORI_CONFIG_DIR": str(self.root / "b")})
        env.start()
        self.addCleanup(env.stop)
        self.store = SettingsStore(default_settings_path())
        self.store.load()
        self.identity = load_or_create_identity(self.root / "b" / "compute")
        self.trust = TrustStore(self.root / "b" / "compute" / "trusted.json")
        self.host = h.ComputeHost(self.store, self.identity, self.trust, "0.3.1")
        self.addCleanup(self.host.stop_server)
        self.lines: list[str] = []
        self.window_open = threading.Event()

    def out(self, line: str) -> None:
        self.lines.append(line)
        if "已開放配對" in line:
            self.window_open.set()

    def said(self, text: str) -> bool:
        return any(text in line for line in self.lines)

    def listen_on_loopback(self) -> int:
        port = free_port()
        # 監聽埠是 listen_port；port（這台連別台用的）留預設，確定主機不會用錯
        self.store.update(
            {"compute": {"listen_host": "127.0.0.1", "listen_port": port}}
        )
        return port


class ListenHostTest(HostCase):
    def test_pick_one_of_this_computers_addresses(self) -> None:
        with mock.patch.object(
            h, "local_addresses", return_value=["192.168.8.236", "100.88.94.10"]
        ):
            self.assertTrue(h.choose_listen_host(self.host, Script("2"), self.out))
        self.assertEqual(self.store.get().compute.listen_host, "100.88.94.10")

    def test_a_public_address_is_refused(self) -> None:
        # 0.0.0.0 也不行（ipaddress 把它算成 private；綁所有介面違反威脅模型）
        for typed in ("8.8.8.8", "0.0.0.0"):
            with self.subTest(typed=typed):
                with mock.patch.object(h, "local_addresses", return_value=[]):
                    self.assertFalse(
                        h.choose_listen_host(self.host, Script(typed), self.out)
                    )
                self.assertEqual(self.store.get().compute.listen_host, "")
                self.assertTrue(self.said("私有網段"))

    def test_blank_cancels(self) -> None:
        with mock.patch.object(h, "local_addresses", return_value=["10.0.0.2"]):
            self.assertFalse(h.choose_listen_host(self.host, Script(""), self.out))

    def test_only_the_addresses_other_computers_can_reach_are_offered(self) -> None:
        # 實際有路由的位址（區網、Tailscale）找得到時只列它們：虛擬網卡（VirtualBox、WSL）與
        # fe80:: 鏈路本地位址（不能直接監聽）會讓不懂網路的人不知道選哪個
        named = [
            "192.168.8.155",
            "fe80::4cca:5a38:fcc2:9bd5",
            "192.168.56.1",
            "172.21.144.1",
            "100.101.150.6",
            "fd7a:115c:a1e0::2b01:9607",
            "8.8.8.8",
            "127.0.0.1",
        ]
        self.assertEqual(
            h.reachable_addresses(["192.168.8.155", "100.101.150.6"], named),
            ["192.168.8.155", "100.101.150.6"],
        )
        # 沒有任何路由（例如只有區網、沒有預設閘道）：列出全部私有位址，但不含 fe80::
        self.assertEqual(
            h.reachable_addresses([], named),
            [
                "192.168.8.155",
                "192.168.56.1",
                "172.21.144.1",
                "100.101.150.6",
                "fd7a:115c:a1e0::2b01:9607",
            ],
        )
        # 探測到的若是公用位址（沒有私有網段）也不算
        self.assertEqual(h.reachable_addresses(["8.8.8.8"], ["10.0.0.2"]), ["10.0.0.2"])

    def test_local_addresses_never_offer_loopback_or_public(self) -> None:
        for address in h.local_addresses():
            with self.subTest(address=address):
                self.assertFalse(address.startswith("127."))
                self.assertTrue(h.is_allowed_source(address))


class OpenHostTest(HostCase):
    def test_changing_a_setting_keeps_the_rest_of_the_file(self) -> None:
        # 2026-11-20 實機：open_host() 沒有 load()，選監聽位址時把整份設定（ASR 模型等）蓋成預設值
        existing = SettingsStore(default_settings_path())
        existing.load()
        existing.update(
            {"asr": {"fast_model": "large-v3-turbo", "final_model": "large-v3"}}
        )
        host = h.open_host()
        with mock.patch.object(h, "local_addresses", return_value=["10.0.0.2"]):
            self.assertTrue(h.choose_listen_host(host, Script("1"), self.out))
        reread = SettingsStore(default_settings_path())
        reread.load()
        settings = reread.get()
        self.assertEqual(settings.compute.listen_host, "10.0.0.2")
        self.assertEqual(
            (settings.asr.fast_model, settings.asr.final_model),
            ("large-v3-turbo", "large-v3"),
        )
        self.assertEqual(host.settings.asr.final_model, "large-v3")


class PeersTest(HostCase):
    def test_remove_a_peer(self) -> None:
        a = load_or_create_identity(self.root / "a")
        self.trust.add(a.cert_path.read_bytes(), "Living room PC")
        with mock.patch.object(self.host, "trust_changed") as changed:
            h.manage_peers(self.host, Script("1"), self.out)
        self.assertFalse(self.trust.is_trusted(a.spki))
        changed.assert_called_once()
        self.assertTrue(self.said("Living room PC"))

    def test_fingerprint_groups(self) -> None:
        text = h.fingerprint(bytes(range(32)))
        self.assertEqual(text, "0001 0203 0405 0607 0809 0a0b 0c0d 0e0f")


class PairTest(HostCase):
    def test_pair_by_typing_the_digits_shown_on_the_other_computer(self) -> None:
        port = self.listen_on_loopback()
        a_id = load_or_create_identity(self.root / "a")
        a_trust = TrustStore(self.root / "a" / "servers.json")
        a_updates: queue.Queue[pn.PairingUpdate] = queue.Queue()
        client: list[pn.PairingClient] = []

        def start_a() -> None:
            # B 開好配對埠之後，A 才去連（模擬使用者在另一台電腦按「配對」）
            # 不能先試連配對埠：那一條連線本身就會佔用配對窗口
            self.assertTrue(self.window_open.wait(WAIT))
            c = pn.PairingClient(
                identity=a_id,
                trust=a_trust,
                name="Living room PC",
                host="127.0.0.1",
                port=pn.pairing_port(port),
                on_update=a_updates.put,
            )
            client.append(c)
            c.start()

        def digits_on_a() -> str:
            while True:
                update = a_updates.get(timeout=WAIT)
                if update.state == "show_digits":
                    assert update.digits is not None
                    return update.digits

        threading.Thread(target=start_a, daemon=True).start()
        with mock.patch.object(self.host, "trust_changed") as changed:
            self.assertTrue(h.pair(self.host, Script(digits_on_a), self.out))
        changed.assert_called_once()
        self.assertTrue(self.trust.is_trusted(a_id.spki))
        # A 的使用者確認後，A 也信任 B
        while a_updates.get(timeout=WAIT).state != "confirm":
            pass
        client[0].confirm(True)
        while (update := a_updates.get(timeout=WAIT)).state not in pn.TERMINAL_STATES:
            pass
        self.assertEqual(update.state, "done")
        self.assertTrue(a_trust.is_trusted(self.identity.spki))
        self.assertTrue(client[0].join(WAIT))

    def test_blank_digits_cancel(self) -> None:
        port = self.listen_on_loopback()
        a_id = load_or_create_identity(self.root / "a")
        a_updates: queue.Queue[pn.PairingUpdate] = queue.Queue()

        def start_a() -> None:
            # 不能先試連配對埠：那一條連線本身就會佔用配對窗口
            self.assertTrue(self.window_open.wait(WAIT))
            pn.PairingClient(
                identity=a_id,
                trust=TrustStore(self.root / "a" / "servers.json"),
                name="A",
                host="127.0.0.1",
                port=pn.pairing_port(port),
                on_update=a_updates.put,
            ).start()

        threading.Thread(target=start_a, daemon=True).start()
        self.assertFalse(h.pair(self.host, Script(""), self.out))
        self.assertEqual(self.trust.peers(), [])
        self.assertTrue(self.said("取消"))


class EnterDigitsTest(HostCase):
    def test_a_pairing_that_already_ended_stops_asking(self) -> None:
        # 2026-11-20 實機：配對在等輸入時逾時結束，終端機卻還卡在「請輸入 6 位數」；之後打的
        # 任何字（例如執行中的指令 p）都被當成數字，一直重問一個已經結束的配對
        ended = mock.Mock()
        ended.join.return_value = True  # 配對埠已關閉
        script = Script("p")
        update = pn.PairingUpdate("enter_digits", peer_name="A")
        self.assertTrue(h._enter_digits(ended, update, script, self.out))
        self.assertEqual(len(script.prompts), 1)
        ended.submit_digits.assert_not_called()

    def test_wrong_format_asks_again_while_still_pairing(self) -> None:
        waiting = mock.Mock()
        waiting.join.return_value = False
        script = Script("12", "123456")
        update = pn.PairingUpdate("enter_digits", peer_name="A")
        waiting.submit_digits.side_effect = [ValueError("6 digits"), None]
        self.assertTrue(h._enter_digits(waiting, update, script, self.out))
        self.assertEqual(waiting.submit_digits.call_count, 2)


class ServeTest(HostCase):
    def test_serve_lists_and_stops(self) -> None:
        port = self.listen_on_loopback()
        h.serve(self.host, Script("l", "d", "q"), self.out)
        self.assertTrue(self.said(f"127.0.0.1:{port}"))
        self.assertTrue(self.said("目前沒有電腦連線"))
        self.assertTrue(self.said("已停止提供運算"))
        self.assertIsNone(self.host.server)
        # 埠已經放開
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", port))

    def test_port_in_use(self) -> None:
        port = self.listen_on_loopback()
        with socket.socket() as busy:
            busy.bind(("127.0.0.1", port))
            busy.listen()
            h.serve(self.host, Script("q"), self.out)
        self.assertTrue(self.said("無法開始監聽"))


class FakeDriver:
    """記錄送出的請求；`models.list` 的內容由測試決定。"""

    def __init__(self, models: dict[str, Any], *, speakers: bool = False) -> None:
        self.models = models
        self.speakers = speakers
        self.requests: list[tuple[str, Any]] = []
        self.closed = False

    def __call__(self, out: Any) -> "FakeDriver":
        return self

    def request(self, method: str, params: Any = None) -> Any:
        self.requests.append((method, params))
        if method == "settings.apply_recommendation":
            return {
                "settings": {
                    "asr": {"fast_model": "small", "final_model": "large-v3-turbo"},
                    "speakers": {"enabled": self.speakers},
                    "translation": {
                        "enabled": True,
                        "fast_model": "qwen3.5:4b",
                        "final_model": "qwen3.5:9b",
                    },
                }
            }
        if method == "models.list":
            return self.models
        raise AssertionError(method)

    def download(self, method: str, params: Any, model: str) -> bool:
        self.requests.append((method, params))
        return True

    def close(self) -> None:
        self.closed = True


class PrepareModelsTest(HostCase):
    def models(self, **overrides: Any) -> dict[str, Any]:
        base: dict[str, Any] = {
            "asr": [
                {"id": "small", "downloaded": True},
                {"id": "large-v3-turbo", "downloaded": False},
            ],
            "gpu_runtime": {"id": "gpu-runtime", "supported": True, "installed": False},
            "speaker_model": {"id": "speaker-embedding", "installed": False},
            "translation": {"installed": ["qwen3.5:4b"]},
        }
        base.update(overrides)
        return base

    def test_downloads_only_what_is_missing(self) -> None:
        driver = FakeDriver(self.models(), speakers=True)
        ok = h.prepare_models(self.host, Script("y"), self.out, driver)
        self.assertTrue(ok)
        sent = driver.requests
        self.assertIn(
            ("models.download", {"model": "gpu-runtime", "accept_license": True}), sent
        )
        self.assertIn(("models.download", {"model": "large-v3-turbo"}), sent)
        self.assertNotIn(("models.download", {"model": "small"}), sent)
        self.assertIn(("models.download", {"model": "speaker-embedding"}), sent)
        self.assertIn(("models.pull_translation", {"model": "qwen3.5:9b"}), sent)
        self.assertNotIn(("models.pull_translation", {"model": "qwen3.5:4b"}), sent)
        self.assertTrue(driver.closed)

    def test_the_speaker_model_is_prepared_for_the_other_computers(self) -> None:
        # 說話者標籤是 A 的設定（隨 session 送來），這台自己沒開也要準備好模型
        driver = FakeDriver(self.models(), speakers=False)
        h.prepare_models(self.host, Script("y"), self.out, driver)
        self.assertIn(
            ("models.download", {"model": "speaker-embedding"}), driver.requests
        )
        installed = FakeDriver(
            self.models(speaker_model={"id": "speaker-embedding", "installed": True})
        )
        h.prepare_models(self.host, Script("y"), self.out, installed)
        self.assertNotIn(
            ("models.download", {"model": "speaker-embedding"}), installed.requests
        )

    def test_declining_the_nvidia_license_skips_the_gpu_runtime(self) -> None:
        driver = FakeDriver(self.models())
        ok = h.prepare_models(self.host, Script("n"), self.out, driver)
        self.assertFalse(ok)
        self.assertFalse(
            any(p and p.get("model") == "gpu-runtime" for _, p in driver.requests)
        )
        self.assertTrue(self.said("CPU"))

    def test_ollama_not_running(self) -> None:
        driver = FakeDriver(
            self.models(
                gpu_runtime={"supported": False},
                translation={"installed": None},
            )
        )
        self.assertFalse(h.prepare_models(self.host, Script(), self.out, driver))
        self.assertTrue(self.said("Ollama"))

    def test_engine_errors_are_reported(self) -> None:
        driver = FakeDriver(self.models())
        driver.request = mock.Mock(side_effect=RuntimeError("models.list: boom"))  # type: ignore[method-assign]
        self.assertFalse(h.prepare_models(self.host, Script(), self.out, driver))
        self.assertTrue(self.said("boom"))
        self.assertTrue(driver.closed)


class EngineDriverTest(HostCase):
    def test_the_engine_starts_from_the_saved_settings(self) -> None:
        # 回歸：driver 的引擎沒先讀設定檔，「準備模型」的 settings.apply_recommendation
        # 以預設值為底存檔，把 compute（監聽位址、提供運算）等全部蓋掉
        self.store.update(
            {
                "compute": {"listen_host": "127.0.0.1", "server_enabled": True},
                "speakers": {"enabled": True},
            }
        )
        driver = h._EngineDriver(self.out)
        try:
            settings = driver.request("settings.get")["settings"]
        finally:
            driver.close()
        self.assertEqual(settings["compute"]["listen_host"], "127.0.0.1")
        self.assertTrue(settings["compute"]["server_enabled"])
        self.assertTrue(settings["speakers"]["enabled"])


class FakeCapacity:
    """B 的能力：固定的模型清單（large-v3 未下載、medium 超出負荷）。"""

    def __init__(self) -> None:
        self.invalidated = 0
        self.info: dict[str, Any] = {
            "gpu": {"name": "NVIDIA GeForce GTX 1080 Ti", "vram_total_mb": 11264},
            "device": "cuda",
            "compute_type": "int8_float16",
            "current": {"fast": "base", "final": "small"},
            "recommended": {"fast": "base", "final": "large-v3"},
            "models": [
                {"id": "base", "downloaded": True, "need_mb": 450, "fit": "ok"},
                {"id": "small", "downloaded": True, "need_mb": 650, "fit": "ok"},
                {"id": "medium", "downloaded": True, "need_mb": 9000, "fit": "over"},
                {
                    "id": "large-v3",
                    "downloaded": False,
                    "need_mb": 2140,
                    "fit": "tight",
                },
            ],
            "translation_model": "qwen3.5:4b",
        }

    def host_info(self, *, detect: bool = True) -> dict[str, Any]:
        return self.info

    def downloaded_models(self) -> frozenset[str]:
        return frozenset(m["id"] for m in self.info["models"] if m["downloaded"])

    def invalidate(self) -> None:
        self.invalidated += 1


class ChooseModelTest(HostCase):
    """compute-capacity 六：選單「選擇模型」只換完整辨識模型（即時模型不變）。"""

    def setUp(self) -> None:
        super().setUp()
        self.capacity = FakeCapacity()
        self.host.capacity = self.capacity
        self.driver = FakeDriver({})

    def choose(self, *answers: str) -> bool:
        return h.choose_model(self.host, Script(*answers), self.out, self.driver)

    def asr(self) -> tuple[str, str]:
        asr = self.store.get().asr
        return asr.fast_model, asr.final_model

    def test_the_list_shows_need_fit_download_recommended_and_current(self) -> None:
        self.choose("")
        listing = "\n".join(self.lines)
        self.assertIn("GTX 1080 Ti", listing)
        line = next(x for x in self.lines if "large-v3" in x and ")" in x)
        for text in ("2.1 GB", "可能吃緊", "未下載", "建議"):
            self.assertIn(text, line)
        small = next(
            x for x in self.lines if " small " in x or x.rstrip().endswith("small")
        )
        self.assertIn("目前", small)
        self.assertIn("超出負荷", next(x for x in self.lines if "medium" in x))

    def test_pick_a_downloaded_model(self) -> None:
        before_fast = self.asr()[0]
        self.assertTrue(self.choose("1"))
        self.assertEqual(self.asr(), (before_fast, "base"))
        self.assertEqual(self.driver.requests, [])  # 不必下載

    def test_a_missing_model_is_downloaded_first_when_asked(self) -> None:
        self.assertTrue(self.choose("4", "y"))
        self.assertEqual(
            self.driver.requests, [("models.download", {"model": "large-v3"})]
        )
        self.assertEqual(self.asr()[1], "large-v3")
        self.assertTrue(self.driver.closed)

    def test_declining_the_download_changes_nothing(self) -> None:
        before = self.asr()
        self.assertFalse(self.choose("4", "n"))
        self.assertEqual(self.asr(), before)
        self.assertEqual(self.driver.requests, [])

    def test_a_failed_download_changes_nothing(self) -> None:
        self.driver.download = mock.Mock(return_value=False)  # type: ignore[method-assign]
        before = self.asr()
        self.assertFalse(self.choose("4", "y"))
        self.assertEqual(self.asr(), before)
        self.assertTrue(self.said("下載失敗"))

    def test_an_overloading_model_asks_again(self) -> None:
        before = self.asr()
        self.assertFalse(self.choose("3", "n"))
        self.assertEqual(self.asr(), before)
        self.assertTrue(self.choose("3", "y"))
        self.assertEqual(self.asr()[1], "medium")

    def test_bad_choices(self) -> None:
        before = self.asr()
        self.assertFalse(self.choose("99"))
        self.assertTrue(self.said("無效的選項"))
        self.assertFalse(self.choose(""))
        self.assertFalse(self.choose("abc"))
        self.assertEqual(self.asr(), before)

    def test_unknown_capacity_still_lists_the_models(self) -> None:
        # 偵測失敗：沒有顯示卡資訊、不知道跑不跑得動，但照樣能選
        self.capacity.info.update(gpu=None, recommended=None, device="cpu")
        for model in self.capacity.info["models"]:
            model["fit"] = None
        self.assertTrue(self.choose("2"))
        self.assertTrue(self.said("不確定"))
        self.assertEqual(self.asr()[1], "small")

    def test_the_menu_entry_comes_after_prepare_models(self) -> None:
        with mock.patch.object(h, "choose_model") as chosen:
            self.assertEqual(
                h.host_menu(Script("5", "8"), self.out, lambda: self.host), 0
            )
        self.assertTrue(chosen.called)
        menu = h.MENU.splitlines()
        prepare = next(i for i, line in enumerate(menu) if "準備模型" in line)
        self.assertIn("5) 選擇模型", menu[prepare + 1])


class LoadLineTest(HostCase):
    """compute-capacity 六：提供運算中按 s 顯示目前負荷一行。"""

    def server(self, metrics: Any, owner: bytes | None = b"x") -> SimpleNamespace:
        return SimpleNamespace(
            owner_spki=lambda: owner,
            current_metrics=lambda: metrics,
            stop=lambda: None,  # HostCase 收尾時呼叫 stop_server
        )

    def test_nobody_is_using_this_computer(self) -> None:
        self.assertIn("目前沒有電腦在用這台", h.load_line(self.host))
        self.host.server = self.server(None, owner=None)
        self.assertIn("目前沒有電腦在用這台", h.load_line(self.host))

    def test_still_loading(self) -> None:
        self.host.server = self.server(None)
        self.assertIn("載入", h.load_line(self.host))

    def test_busy_and_vram(self) -> None:
        self.host.server = self.server(SimpleNamespace(final_busy=0.45, backlog=1))
        self.host.vram = lambda: (6246, 11264)
        line = h.load_line(self.host)
        self.assertIn("45%", line)
        self.assertIn("待處理 1 句", line)
        self.assertIn("6.1／11.0 GB", line)

    def test_no_data_yet_and_no_gpu(self) -> None:
        self.host.server = self.server(SimpleNamespace(final_busy=None, backlog=0))
        self.host.vram = lambda: None
        line = h.load_line(self.host)
        self.assertIn("還沒有資料", line)
        self.assertNotIn("GB", line)

    def test_s_while_serving(self) -> None:
        self.listen_on_loopback()
        h.serve(self.host, Script("s", "q"), self.out)
        self.assertTrue(self.said("目前沒有電腦在用這台"))
        self.assertIn("s＝", h.SERVING_HELP)


class MenuTest(HostCase):
    def test_menu_shows_the_fingerprint_and_returns(self) -> None:
        code = h.host_menu(Script("8"), self.out, lambda: self.host)
        self.assertEqual(code, 0)
        self.assertTrue(self.said(h.fingerprint(self.identity.spki)))

    def test_unreadable_identity_is_reported(self) -> None:
        def broken() -> h.ComputeHost:
            raise IdentityError("damaged key")

        self.assertEqual(h.host_menu(Script(), self.out, broken), 1)
        self.assertTrue(self.said("damaged key"))

    def test_a_damaged_identity_can_be_reset_from_the_menu(self) -> None:
        tries: list[int] = []

        def damaged_once() -> h.ComputeHost:
            tries.append(1)
            if len(tries) == 1:
                raise IdentityError("damaged key")
            return self.host

        self.assertEqual(h.host_menu(Script("y", "8"), self.out, damaged_once), 0)
        self.assertEqual(len(tries), 2)
        self.assertTrue(self.said("重新配對"))

    def test_reset_keeps_the_identity_when_the_lists_cannot_be_cleared(self) -> None:
        # 先清配對清單再換身分：清不掉時身分不變，可以重試
        before = self.host.identity.spki
        with mock.patch.object(
            TrustStore, "clear", side_effect=PermissionError("locked")
        ):
            h.host_menu(Script("7", "y", "8"), self.out, lambda: self.host)
        self.assertEqual(self.host.identity.spki, before)
        self.assertEqual(
            load_or_create_identity(self.root / "b" / "compute").spki, before
        )
        self.assertTrue(self.said("locked"))

    def test_reset_identity_forgets_every_pairing(self) -> None:
        other = load_or_create_identity(self.root / "a")
        self.trust.add(other.cert_path.read_bytes(), "Living room PC")
        before = self.host.identity.spki
        # 不確定就不重建
        h.host_menu(Script("7", "n", "8"), self.out, lambda: self.host)
        self.assertEqual(self.host.identity.spki, before)
        self.assertEqual(len(self.trust.peers()), 1)
        h.host_menu(Script("7", "y", "8"), self.out, lambda: self.host)
        self.assertNotEqual(self.host.identity.spki, before)
        self.assertEqual(self.trust.peers(), [])
        self.assertTrue(self.said(h.fingerprint(self.host.identity.spki)))


if __name__ == "__main__":
    unittest.main()
