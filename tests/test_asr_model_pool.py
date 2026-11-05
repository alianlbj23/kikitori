"""ASR 模型物件池（memory_soak #2）：session 結束時卸載模型、放回池中，下一個 session 重用。

CTranslate2 4.8.5 實測：每建立一個新的 `ctranslate2.models.Whisper` 並在 GPU 上推論過，
`del` 之後仍漏約 8 MiB 顯示記憶體與 8–10 MiB 主記憶體（`unload_model()` 也一樣）；
重複使用同一個物件（`unload_model()` → `load_model()`）則不漏。這裡以假的 `ctranslate2`
模組計數建立／載入／卸載的次數，不載入真模型。
"""

import json
import tempfile
import threading
import types
import unittest
from pathlib import Path
from typing import Any, ClassVar
from unittest import mock

import numpy as np

from kikitori.asr import whisper as whisper_module
from kikitori.asr.tokenizer import bytes_to_unicode
from kikitori.asr.whisper import (
    FAST_DECODE,
    FINAL_DECODE,
    DecodeOptions,
    ModelPool,
    load_transcriber,
)

WAIT = 10.0
SPECIALS = [
    "<|endoftext|>",
    "<|startoftranscript|>",
    "<|en|>",
    "<|zh|>",
    "<|ja|>",
    "<|translate|>",
    "<|transcribe|>",
    "<|startoflm|>",
    "<|startofprev|>",
    "<|nospeech|>",
    "<|notimestamps|>",
]


def tokenizer_data() -> dict[str, Any]:
    """與 test_asr_whisper 相同的最小 tokenizer.json（位元組 BPE + 特殊 token）。"""
    vocab = {ch: i for i, ch in enumerate(bytes_to_unicode().values())}
    added = [
        {"id": 256 + i, "content": t, "special": True} for i, t in enumerate(SPECIALS)
    ]
    return {
        "model": {"type": "BPE", "vocab": vocab, "merges": []},
        "added_tokens": added,
    }


class FakeCt2Whisper:
    """`ctranslate2.models.Whisper` 的替身：記錄建立參數與 load／unload 次數。"""

    created: ClassVar[list["FakeCt2Whisper"]] = []

    def __init__(
        self,
        path: str,
        device: str,
        compute_type: str,
        intra_threads: int,
        inter_threads: int,
    ) -> None:
        self.args = (path, device, compute_type, intra_threads, inter_threads)
        self.loads = 0
        self.unloads = 0
        self.loaded = True
        self.holder: object | None = None
        self.fail_load = False
        self.fail_unload = False
        self.ignore_unload = False
        self.fail_encode: BaseException | None = None
        self.is_multilingual = True
        self.n_mels = 80
        FakeCt2Whisper.created.append(self)

    def load_model(self, keep_cache: bool = False) -> None:
        if self.fail_load:
            raise RuntimeError("CUDA failed with error out of memory")
        self.loads += 1
        self.loaded = True

    def unload_model(self, to_cpu: bool = False) -> None:
        if self.fail_unload:
            raise RuntimeError("unload failed")
        if self.ignore_unload:
            # CTranslate2 在還有進行中的 batch、或拿不到鎖時直接返回（不丟例外）
            return
        self.unloads += 1
        self.loaded = False

    @property
    def model_is_loaded(self) -> bool:
        return self.loaded

    def encode(self, features: Any, to_cpu: bool = False) -> Any:
        if self.fail_encode is not None:
            raise self.fail_encode
        return features

    def generate(self, features: Any, prompts: Any, **kwargs: Any) -> list[Any]:
        return []


def fake_ctranslate2(cuda_devices: int = 0) -> types.ModuleType:
    module = types.ModuleType("ctranslate2")
    module.models = types.SimpleNamespace(Whisper=FakeCt2Whisper)  # type: ignore[attr-defined]
    module.get_cuda_device_count = lambda: cuda_devices  # type: ignore[attr-defined]
    module.StorageView = types.SimpleNamespace(from_array=lambda array: array)  # type: ignore[attr-defined]
    return module


class PoolCase(unittest.TestCase):
    def setUp(self) -> None:
        FakeCt2Whisper.created = []
        self.pool = ModelPool(max_idle=2)
        patches = [
            mock.patch.dict("sys.modules", {"ctranslate2": fake_ctranslate2()}),
            mock.patch.object(whisper_module, "MODEL_POOL", self.pool),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def model_path(self, name: str = "model") -> Path:
        path = self.root / name
        if not path.exists():
            path.mkdir()
            for file in ("model.bin", "config.json"):
                (path / file).write_bytes(b"x")
            (path / "tokenizer.json").write_text(
                json.dumps(tokenizer_data()), encoding="utf-8"
            )
        return path

    def load(self, name: str = "model", **kwargs: Any) -> Any:
        kwargs.setdefault("device", "cpu")
        return load_transcriber(model_path=self.model_path(name), **kwargs)

    @staticmethod
    def model_of(transcriber: Any) -> FakeCt2Whisper:
        model: FakeCt2Whisper = transcriber._model
        return model


class ModelPoolTest(PoolCase):
    def test_closed_model_is_unloaded_and_reused(self) -> None:
        first = self.load(cpu_threads=4)
        model = self.model_of(first)
        first.close()
        self.assertEqual((model.unloads, model.loaded), (1, False))
        self.assertEqual(self.pool.idle_count(), 1)
        second = self.load(cpu_threads=4)
        # 同一個物件，load_model() 把權重載回；沒有建立新的模型物件
        self.assertIs(self.model_of(second), model)
        self.assertEqual((len(FakeCt2Whisper.created), model.loads), (1, 1))
        self.assertTrue(model.loaded)
        self.assertEqual(self.pool.idle_count(), 0)

    def test_key_includes_device_threads_and_workers(self) -> None:
        base = self.load(cpu_threads=4)
        base.close()
        for kwargs in (
            {"cpu_threads": 2},
            {"cpu_threads": 4, "workers": 2},
            {"cpu_threads": 4, "compute_type": "float32"},
        ):
            with self.subTest(kwargs=kwargs):
                other = self.load(**kwargs)
                self.assertIsNot(self.model_of(other), self.model_of(base))
                other.close()
        self.load("another-model", cpu_threads=4).close()
        self.assertEqual(len(FakeCt2Whisper.created), 5)

    def test_shared_instances_release_once_both_are_closed(self) -> None:
        fast = self.load(options=FAST_DECODE, workers=2)
        final = fast.with_options(FINAL_DECODE)
        model = self.model_of(fast)
        fast.close()
        self.assertEqual(model.unloads, 0)  # 完整路徑還在用
        final.close()
        self.assertEqual(model.unloads, 1)
        self.assertEqual(self.pool.idle_count(), 1)

    def test_close_is_idempotent(self) -> None:
        transcriber = self.load()
        transcriber.close()
        transcriber.close()
        self.assertEqual(self.model_of(transcriber).unloads, 1)
        self.assertEqual(self.pool.idle_count(), 1)

    def test_closed_transcriber_cannot_be_used(self) -> None:
        transcriber = self.load()
        transcriber.close()
        with self.assertRaisesRegex(RuntimeError, "closed"):
            transcriber.transcribe(np.zeros(16000, dtype=np.int16), language="ja")
        with self.assertRaisesRegex(RuntimeError, "closed"):
            transcriber.warmup()

    def test_busy_transcriber_is_not_returned_to_the_pool(self) -> None:
        transcriber = self.load()
        model = self.model_of(transcriber)
        transcriber._lock.acquire()  # 另一條執行緒卡在推論中
        try:
            with self.assertLogs("kikitori.asr.whisper", "WARNING"):
                transcriber.close(timeout=0.01)
        finally:
            transcriber._lock.release()
        # 不能在推論途中卸載：不放回池中，交給參照計數
        self.assertEqual((model.unloads, self.pool.idle_count()), (0, 0))

    def test_idle_objects_are_bounded_lru(self) -> None:
        transcribers = [self.load(f"m{i}") for i in range(3)]
        for transcriber in transcribers:
            transcriber.close()
        self.assertEqual(self.pool.idle_count(), 2)
        # 最近放回的仍可重用；最久沒用的 m0 已被丟掉，再用到時建立新的物件
        self.load("m2").close()
        self.assertEqual(len(FakeCt2Whisper.created), 3)
        self.load("m0").close()
        self.assertEqual(len(FakeCt2Whisper.created), 4)

    def test_default_pool_keeps_every_model_of_a_language_switching_user(self) -> None:
        # memory_soak 情境 2（v0.2.0 驗收）：4 組語言／模型輪流＝6 個不同的鍵
        # （tiny、base、small、medium、large-v3、large-v3-turbo）。上限 4 時每輪都把最舊的
        # 丟掉再重建，CTranslate2 每建一個物件就漏約 8 MiB 顯示記憶體（+10.7 MiB／session）。
        # 丟掉的物件本來就漏掉它佔的量，所以保留不比丟掉貴：上限要涵蓋實務上會用到的組合。
        pool = ModelPool()
        names = ("tiny", "base", "small", "medium", "large-v3", "large-v3-turbo")
        with mock.patch.object(whisper_module, "MODEL_POOL", pool):
            for _ in range(3):
                for name in names:
                    self.load(name).close()
        self.assertEqual(len(FakeCt2Whisper.created), len(names))
        self.assertEqual(pool.idle_count(), len(names))

    def test_evict_under_and_clear(self) -> None:
        self.load("a").close()
        self.load("b").close()
        self.assertEqual(self.pool.evict_under(self.root / "a"), 1)
        self.assertEqual(self.pool.idle_count(), 1)
        self.assertEqual(self.pool.evict_under(self.root / "nothing"), 0)
        self.assertEqual(self.pool.clear(), 1)
        self.assertEqual(self.pool.idle_count(), 0)
        self.load("a")
        self.assertEqual(len(FakeCt2Whisper.created), 3)

    def test_failed_unload_drops_the_object(self) -> None:
        transcriber = self.load()
        self.model_of(transcriber).fail_unload = True
        with self.assertLogs("kikitori.asr.whisper", "ERROR"):
            transcriber.close()
        self.assertEqual(self.pool.idle_count(), 0)

    def test_failed_reload_drops_the_object_and_creates_a_new_one(self) -> None:
        transcriber = self.load()
        model = self.model_of(transcriber)
        transcriber.close()
        model.fail_load = True
        # 重用的物件狀態不明：記錄後丟掉，改建立新的物件（與池中沒有物件時相同）
        with self.assertLogs("kikitori.asr.whisper", "ERROR"):
            fresh = self.load()
        self.assertIsNot(self.model_of(fresh), model)
        self.assertEqual((len(FakeCt2Whisper.created), self.pool.idle_count()), (2, 0))
        fresh.close()
        self.assertEqual(self.pool.idle_count(), 1)

    def test_failed_reload_and_failed_create_raises(self) -> None:
        transcriber = self.load()
        model = self.model_of(transcriber)
        transcriber.close()
        model.fail_load = True
        key = self.pool._idle[0][0]

        def broken_create() -> Any:
            raise RuntimeError("cannot create")

        with (
            self.assertLogs("kikitori.asr.whisper", "ERROR"),
            self.assertRaisesRegex(RuntimeError, "cannot create"),
        ):
            self.pool.acquire(key, broken_create)
        self.assertEqual(self.pool.idle_count(), 0)

    def test_unload_that_did_nothing_drops_the_object(self) -> None:
        # CTranslate2 的 unload_model() 在模型忙碌時直接返回：權重還在，不能當成空殼保留
        transcriber = self.load()
        self.model_of(transcriber).ignore_unload = True
        with self.assertLogs("kikitori.asr.whisper", "WARNING"):
            transcriber.close()
        self.assertEqual(self.pool.idle_count(), 0)

    def test_concurrent_sessions_never_share_an_object(self) -> None:
        errors: list[BaseException] = []
        start = threading.Barrier(6)

        def worker() -> None:
            try:
                start.wait(WAIT)
                for _ in range(30):
                    transcriber = self.load()
                    model = self.model_of(transcriber)
                    if model.holder is not None or not model.loaded:
                        raise AssertionError("model handed out twice or unloaded")
                    model.holder = transcriber
                    model.holder = None
                    transcriber.close()
            except BaseException as exc:  # noqa: BLE001 - 收集後由主執行緒斷言
                errors.append(exc)

        self.model_path()  # 先建好模型目錄，執行緒之間只競爭模型池
        threads = [threading.Thread(target=worker) for _ in range(6)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(WAIT)
        self.assertEqual(errors, [])
        self.assertLessEqual(len(FakeCt2Whisper.created), 6)
        self.assertLessEqual(self.pool.idle_count(), 2)

    def test_with_options_cannot_race_with_close(self) -> None:
        """`with_options()` 檢查「未關閉」與 `retain()` 之間 close() 跑完，模型就會在新實例
        還在用時被放回池中（可能借給下一個 session）。"""
        fast = self.load(options=FAST_DECODE, workers=2)
        model = self.model_of(fast)
        original_init = whisper_module.WhisperTranscriber.__init__
        closer = threading.Thread(target=fast.close)

        def init_then_close(this: Any, *args: Any, **kwargs: Any) -> None:
            # 新實例建立到一半時，另一條執行緒關閉原本的實例
            closer.start()
            closer.join(0.2)
            original_init(this, *args, **kwargs)

        final: Any = None
        with mock.patch.object(
            whisper_module.WhisperTranscriber, "__init__", init_then_close
        ):
            try:
                final = fast.with_options(FINAL_DECODE)
            except RuntimeError:
                pass  # 先被關閉：拒絕也是正確的結果
        closer.join(WAIT)
        if final is not None:
            # 新實例還在用：模型不能已經放回池中
            self.assertEqual((model.unloads, self.pool.idle_count()), (0, 0))
            final.close()
        self.assertEqual((model.unloads, self.pool.idle_count()), (1, 1))

    def test_max_idle_must_be_positive(self) -> None:
        for bad in (0, -1, 1.5, True):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                ModelPool(max_idle=bad)  # type: ignore[arg-type]


class CudaLoadTest(PoolCase):
    """CUDA 路徑：取得模型之後建構或暖機失敗時，借用紀錄要歸還（不能漏掉模型）。"""

    def setUp(self) -> None:
        super().setUp()
        patches = [
            mock.patch.dict("sys.modules", {"ctranslate2": fake_ctranslate2(1)}),
            mock.patch.object(whisper_module, "_cuda_failure", None),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        original = whisper_module._ModelLease.release
        self.released: list[bool] = []

        def spy(lease: Any, *, reusable: bool) -> None:
            self.released.append(reusable)
            original(lease, reusable=reusable)

        patch = mock.patch.object(whisper_module._ModelLease, "release", spy)
        patch.start()
        self.addCleanup(patch.stop)

    def fail_encode_on(self, device: str, error: BaseException) -> None:
        original = FakeCt2Whisper.__init__

        def failing_init(this: FakeCt2Whisper, *args: Any, **kwargs: Any) -> None:
            original(this, *args, **kwargs)
            if this.args[1] == device:
                this.fail_encode = error

        patch = mock.patch.object(FakeCt2Whisper, "__init__", failing_init)
        patch.start()
        self.addCleanup(patch.stop)

    def test_cuda_load_and_reuse(self) -> None:
        first = self.load(device="cuda")
        self.assertEqual(first.device, "cuda")
        model = self.model_of(first)
        first.close()
        self.assertIs(self.model_of(self.load(device="cuda")), model)

    def test_invalid_options_return_the_cuda_model_to_the_pool(self) -> None:
        for device in ("cuda", "auto"):
            with self.subTest(device=device):
                FakeCt2Whisper.created = []
                self.pool.clear()
                with self.assertRaises(ValueError):
                    self.load(
                        device=device, options=DecodeOptions(forced_language="xx")
                    )
                # 模型沒問題，只是參數不合法：放回池中，也不算 CUDA 失敗
                self.assertEqual(self.pool.idle_count(), 1)
                self.assertEqual(FakeCt2Whisper.created[0].unloads, 1)
                self.assertIsNone(whisper_module._cuda_failure)

    def test_warmup_failure_with_cuda_releases_the_lease(self) -> None:
        self.fail_encode_on("cuda", RuntimeError("cuBLAS failed"))
        with self.assertRaisesRegex(RuntimeError, "cuBLAS"):
            self.load(device="cuda")
        # GPU 上失敗的物件狀態不明：歸還借用紀錄，但不放回池中
        self.assertEqual(self.released, [False])
        self.assertEqual(self.pool.idle_count(), 0)
        self.assertIsNotNone(whisper_module._cuda_failure)

    def test_warmup_failure_with_auto_falls_back_to_cpu(self) -> None:
        self.fail_encode_on("cuda", OSError("cannot load cublas64_12.dll"))
        with self.assertLogs("kikitori.asr.whisper", "WARNING"):
            transcriber = self.load(device="auto")
        self.assertEqual(transcriber.device, "cpu")
        self.assertEqual(self.released, [False])
        transcriber.close()
        self.assertEqual(self.released, [False, True])
        self.assertEqual(self.pool.idle_count(), 1)


if __name__ == "__main__":
    unittest.main()
