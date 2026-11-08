"""預先量化的衍生快取（`asr/derived.py`）。

格式以 CTranslate2 官方的 `ModelSpec._serialize()` 產生的檔案對照（開發環境有 `ctranslate2.specs`；
打包的引擎沒有）；量化規則以獨立寫成的逐元素參考實作對照。真模型的檢查（本機快取有 `tiny`
時才跑，否則 skip）用 CTranslate2 實際載入，比對衍生檔與即時量化的輸出。
"""

import json
import os
import shutil
import struct
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

import numpy as np

from kikitori.asr import derived
from kikitori.asr.derived import (
    ActivityTracker,
    DerivationCancelled,
    DerivationJob,
    DerivedCacheWorker,
    DerivedKey,
    ModelFormatError,
    ProbeResult,
    build_derived,
    derived_dir,
    derived_key,
    failure_reason,
    find_derived,
    mark_failed,
    quantize_int8,
    read_model_index,
    write_quantized_model_bin,
)
from kikitori.asr.download import MARKER_NAME, _expected_marker, model_disk_bytes
from kikitori.asr.registry import ModelFile, ModelSpec, get_model

# 共用 test_asr_model_pool 的假 ctranslate2 與最小 tokenizer；用 `-m unittest tests.test_asr_derived`
# 單獨執行時 tests/ 不在 sys.path 上，所以先補上
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_asr_model_pool import (
    FakeCt2Whisper,
    fake_ctranslate2,
    tokenizer_data,
)

WAIT = 10.0


# --- 以官方序列化產生檔案 -------------------------------------------------------


def _official_serialize(variables: dict[str, Any], path: Path) -> None:
    """用 `ctranslate2.specs.model_spec.ModelSpec._serialize()` 寫出 `model.bin`。

    `variables` 的值是 numpy 陣列，或字串（別名，指向另一個變數名稱）。
    """
    from ctranslate2.specs import model_spec as ms

    class Layer(ms.LayerSpec):
        pass

    class Spec(ms.ModelSpec):
        @property
        def name(self) -> str:
            return "WhisperSpec"

        @property
        def revision(self) -> int:
            return 3

    spec = Spec()
    for name, value in variables.items():
        node: Any = spec
        *scopes, attr = name.split("/")
        for scope in scopes:
            child = node.__dict__.get(scope)
            if child is None:
                child = Layer()
                object.__setattr__(node, scope, child)
            node = child
        if not isinstance(value, str):
            value = ms.NumpyVariable(value)
        object.__setattr__(node, attr, value)
    spec._serialize(os.fspath(path))


def _reference_quantize(values: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """逐元素的參考實作（C++ cpu::quantize_s8，round_before_cast）。"""
    rows = values.reshape(-1, values.shape[-1]).astype(np.float32)
    out = np.zeros(rows.shape, dtype=np.int8)
    scales = np.zeros(rows.shape[0], dtype=np.float32)
    for i, row in enumerate(rows):
        amax = np.float32(max(abs(float(v)) for v in row))
        scale = np.float32(127.0) / amax if amax != 0 else np.float32(1.0)
        scales[i] = scale
        for j, v in enumerate(row):
            out[i, j] = int(np.rint(np.float32(v) * scale))
    return out.reshape(values.shape), scales


def _source_variables() -> dict[str, Any]:
    rng = np.random.default_rng(7)
    linear = rng.normal(size=(5, 8)).astype(np.float16)
    linear[1] = 0  # 全零列：scale 為 1
    linear[2] = np.array(
        [127, 0.5, 1.5, 2.5, -0.5, -1.5, -2.5, 3], np.float16
    )  # 就近取偶
    return {
        "encoder/conv1/weight": rng.normal(size=(4, 3, 3)).astype(np.float16),
        "encoder/conv1/bias": rng.normal(size=(4,)).astype(np.float16),
        "encoder/layer_0/ffn/linear_0/weight": linear,
        "encoder/layer_0/ffn/linear_0/bias": rng.normal(size=(5,)).astype(np.float16),
        "encoder/layer_norm/gamma": rng.normal(size=(8,)).astype(np.float16),
        "encoder/flag": np.array(1, dtype=np.int8),
        "decoder/num_heads": np.array(4, dtype=np.int16),
        "decoder/embeddings/weight": rng.normal(size=(6, 8)).astype(np.float16),
        "decoder/projection/weight": "decoder/embeddings/weight",
        "decoder/extra/weight": rng.normal(size=(3, 4)).astype(np.float32),
    }


QUANTIZED = (
    "encoder/layer_0/ffn/linear_0/weight",
    "decoder/embeddings/weight",
    "decoder/extra/weight",
)


class FormatTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.source = self.root / "source.bin"
        _official_serialize(_source_variables(), self.source)

    def test_reads_official_file(self) -> None:
        index = read_model_index(self.source)
        self.assertEqual(
            (index.binary_version, index.spec_name, index.spec_revision),
            (6, "WhisperSpec", 3),
        )
        names = [v.name for v in index.variables]
        self.assertEqual(names, sorted(names))
        by_name = {v.name: v for v in index.variables}
        self.assertEqual(by_name["encoder/conv1/weight"].shape, (4, 3, 3))
        self.assertEqual(by_name["decoder/num_heads"].shape, ())
        self.assertEqual(
            index.aliases, (("decoder/projection/weight", "decoder/embeddings/weight"),)
        )

    def test_output_matches_official_serializer_and_reference_quantization(
        self,
    ) -> None:
        expected_vars = dict(_source_variables())
        for name in QUANTIZED:
            values, scales = _reference_quantize(expected_vars[name])
            expected_vars[name] = values
            expected_vars[f"{name}_scale"] = scales
        expected = self.root / "expected.bin"
        _official_serialize(expected_vars, expected)

        out = self.root / "out.bin"
        stats = write_quantized_model_bin(self.source, out)
        self.assertEqual(out.read_bytes(), expected.read_bytes())
        self.assertEqual(stats.quantized, 3)
        self.assertEqual(stats.size, out.stat().st_size)

    def test_quantization_edge_cases(self) -> None:
        q, s = quantize_int8(
            np.array([[0, 0, 0], [127, 0.5, 1.5], [254, -2.5, 3.0]], np.float16)
        )
        np.testing.assert_array_equal(s, np.array([1.0, 1.0, 0.5], np.float32))
        np.testing.assert_array_equal(q, [[0, 0, 0], [127, 0, 2], [127, -1, 2]])
        with self.assertRaises(ModelFormatError):
            quantize_int8(np.array([[1.0, np.nan]], np.float32))
        with self.assertRaises(ModelFormatError):
            quantize_int8(np.array([[1.0, np.inf]], np.float32))

    def test_chunked_quantization_matches_reference(self) -> None:
        rng = np.random.default_rng(3)
        values = rng.normal(size=(derived.QUANTIZE_CHUNK_ROWS + 5, 7)).astype(
            np.float16
        )
        q, s = quantize_int8(values)
        rq, rs = _reference_quantize(values)
        np.testing.assert_array_equal(q, rq)
        np.testing.assert_array_equal(s, rs)

    def test_stop_between_variables(self) -> None:
        calls = 0

        def stop() -> bool:
            nonlocal calls
            calls += 1
            return calls > 3

        with self.assertRaises(DerivationCancelled):
            write_quantized_model_bin(
                self.source, self.root / "out.bin", should_stop=stop
            )

    def test_refuses_existing_destination(self) -> None:
        out = self.root / "out.bin"
        out.write_bytes(b"x")
        with self.assertRaises(FileExistsError):
            write_quantized_model_bin(self.source, out)
        self.assertEqual(out.read_bytes(), b"x")

    def _broken(self, data: bytes) -> Path:
        path = self.root / "broken.bin"
        path.write_bytes(data)
        return path

    def test_malformed_files_are_rejected(self) -> None:
        good = self.source.read_bytes()
        header_end = 4 + 2 + len(b"WhisperSpec\0") + 4
        first = header_end + 4  # 第一個變數的名稱長度
        name_len = struct.unpack_from("<H", good, first)[0]
        rank_at = first + 2 + name_len
        rank = good[rank_at]
        dtype_at = rank_at + 1 + 4 * rank
        cases = {
            "binary version": struct.pack("<I", 5) + good[4:],
            "spec": good[:4] + struct.pack("<H", 12) + b"WhisperSpeX\0" + good[18:],
            "revision": good[:18] + struct.pack("<I", 2) + good[22:],
            "truncated": good[:-3],
            "trailing": good + b"\0",
            "too many variables": good[:header_end]
            + struct.pack("<I", derived.MAX_VARIABLES + 1)
            + good[header_end + 4 :],
            "empty string": good[:first] + struct.pack("<H", 0) + good[first + 2 :],
            "unterminated": good[: rank_at - 1] + b"x" + good[rank_at:],
            "rank": good[:rank_at]
            + bytes([derived.MAX_RANK + 1])
            + good[rank_at + 1 :],
            "zero dim": good[: rank_at + 1]
            + struct.pack("<I", 0)
            + good[rank_at + 5 :],
            "dtype": good[:dtype_at] + bytes([9]) + good[dtype_at + 1 :],
            "size": good[: dtype_at + 1]
            + struct.pack("<I", struct.unpack_from("<I", good, dtype_at + 1)[0] + 2)
            + good[dtype_at + 5 :],
        }
        # 每一維都不是 0 但乘積是 2^64：int64 會回繞成 0，size 0 就會被當成正確
        overflow = (
            good[:header_end]
            + struct.pack("<I", 1)
            + struct.pack("<H", 2)
            + b"w\0"
            + bytes([4])
            + struct.pack("<4I", *([65536] * 4))
            + bytes([derived._FLOAT32])
            + struct.pack("<I", 0)
            + struct.pack("<I", 0)
        )
        cases["shape overflow"] = overflow
        for label, data in cases.items():
            with self.subTest(label), self.assertRaises(ModelFormatError):
                read_model_index(self._broken(data))

    def test_file_without_float_weights_is_rejected(self) -> None:
        path = self.root / "int.bin"
        _official_serialize({"encoder/flag": np.array(1, np.int8)}, path)
        with self.assertRaises(ModelFormatError):
            write_quantized_model_bin(path, self.root / "out.bin")


# --- 快取配置與 build_derived ---------------------------------------------------


def _install(cache: Path, model_bin: bytes, model_id: str = "fake") -> ModelSpec:
    """在暫存快取裡放一個「已下載」的模型（含最小 tokenizer.json）。"""
    files = {
        "model.bin": model_bin,
        "config.json": b"{}",
        "tokenizer.json": json.dumps(tokenizer_data()).encode("utf-8"),
    }
    import hashlib

    spec = ModelSpec(
        id=model_id,
        repo_id="example/fake",
        revision="0" * 40,
        files=tuple(
            ModelFile(name, len(data), hashlib.sha256(data).hexdigest())
            for name, data in files.items()
        ),
        languages=("multilingual",),
        n_mels=80,
        speed_tier="fast",
        license="MIT",
        approx_cpu_ram_mb=1,
        approx_gpu_vram_mb=1,
        approx_gpu_vram_int8_mb=1,
    )
    directory = cache / spec.id / spec.revision[:12]
    directory.mkdir(parents=True)
    for name, data in files.items():
        (directory / name).write_bytes(data)
    (directory / MARKER_NAME).write_text(
        json.dumps(_expected_marker(spec)), encoding="utf-8"
    )
    return spec


class FakeProbeModel:
    """`probe_model()` 用到的介面；輸出由 `outputs[資料夾名稱]` 決定。"""

    n_mels = 80

    def __init__(self, tag: str) -> None:
        self.tag = tag

    def encode(self, features: Any, to_cpu: bool = False) -> np.ndarray:
        return np.full((1, 2, 3), 1.0 if self.tag == "same" else 2.0, np.float16)

    def generate(self, features: Any, prompts: Any, **kwargs: Any) -> list[Any]:
        result = mock.Mock(sequences_ids=[[1, 2]], scores=[-0.5], no_speech_prob=0.1)
        return [result]


class CacheLayoutCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.cache = Path(self._tmp.name) / "models"
        source = Path(self._tmp.name) / "source.bin"
        _official_serialize(_source_variables(), source)
        self.spec = _install(self.cache, source.read_bytes())
        self.key = derived_key(self.spec, "int8_float16", "4.8.5")
        assert self.key is not None
        self.job = DerivationJob(self.spec, self.cache, self.key, "cpu")
        self.created: list[tuple[Path, str, str]] = []

    def factory(self, differ: bool = False) -> Any:
        def create(path: Path, device: str, compute_type: str, threads: int) -> Any:
            self.created.append((path, device, compute_type))
            is_candidate = path.name.startswith(derived.TMP_PREFIX)
            return FakeProbeModel("diff" if differ and is_candidate else "same")

        return create

    def build(self, **kwargs: Any) -> Any:
        kwargs.setdefault("create_model", self.factory())
        kwargs.setdefault("storage", lambda array: array)
        return build_derived(self.job, **kwargs)

    def leftovers(self) -> list[str]:
        root = derived.derived_root(self.spec, self.cache)
        return sorted(p.name for p in root.iterdir()) if root.exists() else []


class KeyTest(CacheLayoutCase):
    def test_key_fields_and_non_int8_types(self) -> None:
        self.assertEqual(self.key.source_sha256, self.spec.files[0].sha256)
        self.assertEqual(
            self.key.dirname, f"{self.key.source_sha256[:16]}-int8_float16-ct2-4.8.5-f1"
        )
        for compute_type in ("float16", "float32", "int16", "bfloat16"):
            self.assertIsNone(derived_key(self.spec, compute_type, "4.8.5"))
        with self.assertRaises(ValueError):
            derived_key(self.spec, "int8", "../../x")

    def test_registry_models_have_keys(self) -> None:
        key = derived_key(get_model("large-v3"), "int8_float16", "4.8.5")
        assert key is not None
        self.assertEqual(key.model_id, "large-v3")


class BuildDerivedTest(CacheLayoutCase):
    def test_success_publishes_verified_copy(self) -> None:
        result = self.build()
        final = derived_dir(self.spec, self.cache, self.key)
        self.assertEqual(result.path, final)
        self.assertEqual(find_derived(self.spec, self.cache, self.key), final)
        self.assertEqual(self.leftovers(), [self.key.dirname])
        for file in self.spec.files:
            self.assertTrue((final / file.name).is_file())
        marker = json.loads((final / derived.DERIVED_MARKER).read_text("utf-8"))
        self.assertEqual(marker["key"], self.key.as_dict())
        # 原始檔沒有被動過；比對時先載入原始檔，再載入暫存的衍生檔
        source_dir = self.cache / self.spec.id / self.spec.revision[:12]
        self.assertEqual(self.created[0], (source_dir, "cpu", "int8_float16"))
        self.assertTrue(self.created[1][0].name.startswith(derived.TMP_PREFIX))
        # 已有可用的衍生檔時不重做
        self.created.clear()
        self.assertEqual(self.build().path, final)
        self.assertEqual(self.created, [])

    def test_mismatch_is_an_error_and_leaves_nothing(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "differs"):
            self.build(create_model=self.factory(differ=True))
        self.assertIsNone(find_derived(self.spec, self.cache, self.key))
        self.assertEqual(self.leftovers(), [])

    def test_cancel_leaves_nothing(self) -> None:
        with self.assertRaises(DerivationCancelled):
            self.build(should_stop=lambda: True)
        self.assertEqual(self.leftovers(), [])

    def test_invalid_published_folder_is_replaced(self) -> None:
        final = derived_dir(self.spec, self.cache, self.key)
        final.mkdir(parents=True)
        (final / derived.DERIVED_MARKER).write_text("{broken", encoding="utf-8")
        self.assertIsNone(find_derived(self.spec, self.cache, self.key))
        self.build()
        self.assertEqual(find_derived(self.spec, self.cache, self.key), final)

    def test_stale_temp_folders_are_removed(self) -> None:
        root = derived.derived_root(self.spec, self.cache)
        stale = root / f"{derived.TMP_PREFIX}old"
        fresh = root / f"{derived.TMP_PREFIX}other-process"
        for folder in (stale, fresh):
            folder.mkdir(parents=True)
            (folder / "model.bin").write_bytes(b"partial")
        old = time.time() - derived.STALE_TMP_SECS - 10
        os.utime(stale, (old, old))
        self.build()
        self.assertFalse(stale.exists())
        self.assertTrue(fresh.exists())  # 可能是另一個行程正在寫

    def test_find_derived_rejects_tampering(self) -> None:
        final = self.build().path
        self.assertIsNotNone(find_derived(self.spec, self.cache, self.key))
        other = DerivedKey(**{**self.key.__dict__, "ctranslate2_version": "9.9.9"})
        self.assertIsNone(find_derived(self.spec, self.cache, other))
        with (final / "model.bin").open("ab") as fh:
            fh.write(b"x")
        self.assertIsNone(find_derived(self.spec, self.cache, self.key))
        (final / "model.bin").unlink()
        self.assertIsNone(find_derived(self.spec, self.cache, self.key))

    def test_failure_marker(self) -> None:
        self.assertIsNone(failure_reason(self.spec, self.cache, self.key))
        mark_failed(self.spec, self.cache, self.key, "boom")
        self.assertEqual(failure_reason(self.spec, self.cache, self.key), "boom")
        marker = derived.derived_root(self.spec, self.cache) / (
            self.key.dirname + derived.FAILED_SUFFIX
        )
        marker.write_text("not json", encoding="utf-8")
        self.assertIsNotNone(failure_reason(self.spec, self.cache, self.key))

    def test_derived_files_count_in_disk_bytes(self) -> None:
        before = model_disk_bytes(self.spec, self.cache)
        assert before is not None
        result = self.build()
        after = model_disk_bytes(self.spec, self.cache)
        assert after is not None
        self.assertGreaterEqual(after - before, result.model_bin_bytes)

    def test_not_downloaded(self) -> None:
        shutil.rmtree(self.cache / self.spec.id / self.spec.revision[:12])
        with self.assertRaises(FileNotFoundError):
            self.build()

    def test_probe_comparison_is_bitwise(self) -> None:
        a = ProbeResult(np.zeros(2, np.float16), (1,), -0.5, 0.1)
        self.assertTrue(
            a.same_as(ProbeResult(np.zeros(2, np.float16), (1,), -0.5, 0.1))
        )
        # -0.0 與 0.0 數值相等但位元不同
        self.assertFalse(
            a.same_as(ProbeResult(-np.zeros(2, np.float16), (1,), -0.5, 0.1))
        )
        self.assertFalse(
            a.same_as(ProbeResult(np.zeros(2, np.float32), (1,), -0.5, 0.1))
        )
        self.assertFalse(
            a.same_as(ProbeResult(np.zeros(2, np.float16), (2,), -0.5, 0.1))
        )
        self.assertFalse(
            a.same_as(ProbeResult(np.zeros(2, np.float16), (1,), -0.5000001, 0.1))
        )


# --- 背景工作 -----------------------------------------------------------------


class ActivityTrackerTest(unittest.TestCase):
    def test_idle_wait(self) -> None:
        activity = ActivityTracker()
        self.assertTrue(activity.wait_idle(0.0, lambda: False))
        activity.begin()
        self.assertTrue(activity.active)
        stop = threading.Event()
        threading.Timer(0.2, stop.set).start()
        self.assertFalse(activity.wait_idle(0.0, stop.is_set))
        activity.end()
        self.assertFalse(activity.active)
        started = time.monotonic()
        self.assertTrue(activity.wait_idle(0.2, lambda: False))
        self.assertGreaterEqual(time.monotonic() - started, 0.15)
        with self.assertLogs("kikitori.asr.derived", "ERROR"):
            activity.end()  # 不會變成負數


class WorkerTest(CacheLayoutCase):
    def setUp(self) -> None:
        super().setUp()
        self.activity = ActivityTracker()
        self.calls: list[DerivationJob] = []
        self.behavior: list[Any] = []
        self.done = threading.Event()

    def make_worker(self) -> DerivedCacheWorker:
        def build(job: DerivationJob, *, should_stop: Any) -> Any:
            self.calls.append(job)
            action = self.behavior.pop(0) if self.behavior else None
            try:
                if callable(action):
                    return action(should_stop)
                if isinstance(action, BaseException):
                    raise action
                return derived.DerivationResult(Path("x"), 1, 0, 0)
            finally:
                self.done.set()

        worker = DerivedCacheWorker(self.activity, idle_secs=0.0, build=build)
        self.addCleanup(worker.shutdown, 2.0)
        return worker

    def wait_done(self) -> None:
        self.assertTrue(self.done.wait(WAIT))
        self.done.clear()

    def wait_until(self, condition: Any) -> None:
        deadline = time.monotonic() + WAIT
        while not condition():
            self.assertLess(time.monotonic(), deadline)
            time.sleep(0.01)

    def test_runs_when_idle_and_deduplicates(self) -> None:
        self.activity.begin()
        worker = self.make_worker()
        self.assertTrue(worker.request(self.job))
        self.assertFalse(worker.request(self.job))
        time.sleep(0.2)
        self.assertEqual(self.calls, [])  # 有模型開著：等
        self.activity.end()
        self.wait_done()
        self.assertEqual(self.calls, [self.job])
        self.wait_until(lambda: worker.pending() == [])

    def test_skips_failed_and_existing(self) -> None:
        worker = self.make_worker()
        mark_failed(self.spec, self.cache, self.key, "boom")
        self.assertFalse(worker.request(self.job))

    def test_failure_is_marked(self) -> None:
        self.behavior.append(ModelFormatError("bad"))
        worker = self.make_worker()
        with self.assertLogs("kikitori.asr.derived", "ERROR"):
            worker.request(self.job)
            self.wait_done()
            self.wait_until(
                lambda: failure_reason(self.spec, self.cache, self.key) is not None
            )
        self.assertIn("bad", failure_reason(self.spec, self.cache, self.key) or "")

    def test_failure_while_asr_is_in_use_is_retried_not_marked(self) -> None:
        def fails_during_session(should_stop: Any) -> Any:
            self.activity.begin()  # 例如驗證時 session 開始載入，顯示記憶體不足
            raise RuntimeError("CUDA failed with error out of memory")

        self.behavior.append(fails_during_session)
        worker = self.make_worker()
        with self.assertLogs("kikitori.asr.derived", "WARNING"):
            worker.request(self.job)
            self.wait_done()
            # pending() 也列出執行中的工作：要等它結束、真的放回佇列
            self.wait_until(
                lambda: worker._running is None and worker.pending() == [self.job]
            )
        self.assertIsNone(failure_reason(self.spec, self.cache, self.key))
        self.activity.end()
        self.wait_done()  # 閒置後重做（這次成功）
        self.assertEqual(len(self.calls), 2)
        self.assertIsNone(failure_reason(self.spec, self.cache, self.key))

    def test_session_start_interrupts_and_requeues(self) -> None:
        def interrupted(should_stop: Any) -> Any:
            self.activity.begin()  # session 開始
            self.assertTrue(should_stop())
            raise DerivationCancelled("session")

        self.behavior.append(interrupted)
        worker = self.make_worker()
        worker.request(self.job)
        self.wait_done()
        self.assertEqual(worker.pending(), [self.job])
        self.assertIsNone(failure_reason(self.spec, self.cache, self.key))
        self.activity.end()
        self.wait_done()
        self.assertEqual(len(self.calls), 2)

    def test_cancel_model_waits_for_running_job(self) -> None:
        entered = threading.Event()

        def slow(should_stop: Any) -> Any:
            entered.set()
            while not should_stop():
                time.sleep(0.01)
            raise DerivationCancelled("deleted")

        self.behavior.append(slow)
        worker = self.make_worker()
        worker.request(self.job)
        self.assertTrue(entered.wait(WAIT))
        self.assertTrue(worker.cancel_model(self.spec.id, timeout=WAIT))
        self.assertEqual(worker.pending(), [])
        self.assertIsNone(failure_reason(self.spec, self.cache, self.key))

    def test_cancel_model_times_out_when_job_ignores_stop(self) -> None:
        release = threading.Event()
        entered = threading.Event()

        def stuck(should_stop: Any) -> Any:
            entered.set()
            release.wait(WAIT)
            raise DerivationCancelled("late")

        self.behavior.append(stuck)
        worker = self.make_worker()
        worker.request(self.job)
        self.assertTrue(entered.wait(WAIT))
        self.assertFalse(worker.cancel_model(self.spec.id, timeout=0.1))
        release.set()
        self.wait_until(lambda: worker.pending() == [])

    def test_cancel_drops_pending_and_shutdown_refuses_new_jobs(self) -> None:
        self.activity.begin()
        self.addCleanup(self.activity.end)
        worker = self.make_worker()
        worker.request(self.job)
        self.assertTrue(worker.cancel_model(self.spec.id))
        self.assertEqual(worker.pending(), [])
        worker.shutdown(2.0)
        self.assertFalse(worker.request(self.job))
        self.assertEqual(self.calls, [])


# --- load_transcriber(derived_cache=True) -------------------------------------


class LoadTranscriberTest(CacheLayoutCase):
    """whisper.py 的整合：有衍生檔就從它載入；沒有就從原始檔載入並排入背景轉換。"""

    def setUp(self) -> None:
        super().setUp()
        from kikitori.asr import whisper as whisper_module
        from kikitori.asr.whisper import ModelPool

        self.whisper = whisper_module
        self.fake_cls = FakeCt2Whisper
        FakeCt2Whisper.created = []
        self.fail_paths: set[str] = set()
        fail_paths = self.fail_paths
        self.active_while_loading: list[bool] = []
        active_while_loading = self.active_while_loading

        class FailingWhisper(FakeCt2Whisper):
            def __init__(self, path: str, *args: Any, **kwargs: Any) -> None:
                # 載入中（借用紀錄還沒建立）背景轉換就要看到「使用中」
                active_while_loading.append(derived.ASR_ACTIVITY.active)
                if any(path.startswith(p) for p in fail_paths):
                    raise RuntimeError(f"cannot load {path}")
                super().__init__(path, *args, **kwargs)

        module = fake_ctranslate2()
        module.models.Whisper = FailingWhisper  # type: ignore[attr-defined]
        module.__version__ = "4.8.5"  # type: ignore[attr-defined]
        self.activity = ActivityTracker()
        self.worker = mock.Mock()
        self.pool = ModelPool()
        for patch in (
            mock.patch.dict("sys.modules", {"ctranslate2": module}),
            mock.patch.object(whisper_module, "MODEL_POOL", self.pool),
            mock.patch.object(derived, "DERIVED_CACHE", self.worker),
            mock.patch.object(derived, "ASR_ACTIVITY", self.activity),
        ):
            patch.start()
            self.addCleanup(patch.stop)
        self.cpu_key = derived_key(self.spec, "int8", "4.8.5")
        self.source_dir = self.cache / self.spec.id / self.spec.revision[:12]

    def load(self, **kwargs: Any) -> Any:
        from kikitori.asr.whisper import load_transcriber

        kwargs.setdefault("derived_cache", True)
        return load_transcriber(
            self.spec, cache_dir=self.cache, device="cpu", compute_type="int8", **kwargs
        )

    def publish(self) -> Path:
        assert self.cpu_key is not None
        job = DerivationJob(self.spec, self.cache, self.cpu_key, "cpu")
        return build_derived(
            job, create_model=self.factory(), storage=lambda array: array
        ).path

    def created_paths(self) -> list[str]:
        return [m.args[0] for m in self.fake_cls.created]

    def test_without_derived_loads_original_and_queues_job(self) -> None:
        transcriber = self.load()
        self.assertEqual(self.created_paths(), [os.fspath(self.source_dir)])
        (job,), _ = self.worker.request.call_args
        self.assertEqual(job.key, self.cpu_key)
        self.assertEqual(job.device, "cpu")
        self.assertEqual(self.active_while_loading, [True])
        self.assertTrue(self.activity.active)
        transcriber.close()
        self.assertFalse(self.activity.active)

    def test_uses_published_derived_copy(self) -> None:
        final = self.publish()
        self.load().close()
        self.assertEqual(self.created_paths(), [os.fspath(final)])
        self.worker.request.assert_not_called()

    def test_disabled_or_float_types_skip_the_cache(self) -> None:
        self.publish()
        self.load(derived_cache=False).close()
        from kikitori.asr.whisper import load_transcriber

        load_transcriber(
            self.spec,
            cache_dir=self.cache,
            device="cpu",
            compute_type="float32",
            derived_cache=True,
        ).close()
        self.assertEqual(self.created_paths(), [os.fspath(self.source_dir)] * 2)
        self.worker.request.assert_not_called()

    def test_failed_key_is_not_used_or_requeued(self) -> None:
        self.publish()
        assert self.cpu_key is not None
        mark_failed(self.spec, self.cache, self.cpu_key, "earlier failure")
        self.load().close()
        self.assertEqual(self.created_paths(), [os.fspath(self.source_dir)])
        self.worker.request.assert_not_called()

    def test_derived_load_failure_falls_back_and_marks_failed(self) -> None:
        final = self.publish()
        self.fail_paths.add(os.fspath(final))
        with self.assertLogs("kikitori.asr.whisper", "ERROR"):
            transcriber = self.load()
        self.assertEqual(self.created_paths(), [os.fspath(self.source_dir)])
        reason = failure_reason(self.spec, self.cache, self.cpu_key)  # type: ignore[arg-type]
        self.assertIn("cannot load", reason or "")
        transcriber.close()
        self.assertFalse(self.activity.active)

    def test_both_failing_raises_original_error_without_marking(self) -> None:
        final = self.publish()
        self.fail_paths.update({os.fspath(final), os.fspath(self.source_dir)})
        with (
            self.assertLogs("kikitori.asr.whisper", "ERROR"),
            self.assertRaisesRegex(
                RuntimeError, "cannot load .*" + self.spec.revision[:12]
            ),
        ):
            self.load()
        self.assertIsNone(failure_reason(self.spec, self.cache, self.cpu_key))  # type: ignore[arg-type]
        self.assertFalse(self.activity.active)


# --- 真模型（本機快取有 tiny 時） -------------------------------------------------


def _user_tiny() -> Path | None:
    from kikitori.asr import default_cache_dir, is_downloaded, model_dir

    spec = get_model("tiny")
    cache = default_cache_dir()
    try:
        return model_dir(spec, cache) if is_downloaded(spec, cache) else None
    except OSError:
        return None


@unittest.skipIf(_user_tiny() is None, "tiny is not in the local model cache")
class RealModelTest(unittest.TestCase):
    """以 CTranslate2 實際載入 tiny（CPU int8）：衍生檔的輸出與即時量化逐位元相同。"""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.cache = Path(self._tmp.name)
        self.spec = get_model("tiny")
        source = _user_tiny()
        assert source is not None
        target = self.cache / self.spec.id / self.spec.revision[:12]
        target.mkdir(parents=True)
        for file in [*self.spec.files, ModelFile(MARKER_NAME, 0, "")]:
            shutil.copyfile(source / file.name, target / file.name)
        key = derived_key(self.spec, "int8")
        assert key is not None
        self.key = key
        self.job = DerivationJob(self.spec, self.cache, key, "cpu", cpu_threads=2)

    def test_derived_model_matches_on_the_fly_quantization(self) -> None:
        result = build_derived(self.job)
        source = (self.cache / self.spec.id / self.spec.revision[:12]) / "model.bin"
        self.assertLess(result.model_bin_bytes, source.stat().st_size * 0.6)
        self.assertEqual(find_derived(self.spec, self.cache, self.key), result.path)

    def test_wrong_quantization_is_caught(self) -> None:
        real = derived.quantize_int8

        def off_by_one(values: Any) -> Any:
            q, s = real(values)
            q.reshape(-1)[0] = np.int8(max(-127, int(q.reshape(-1)[0]) - 1))
            return q, s

        with (
            mock.patch.object(derived, "quantize_int8", off_by_one),
            self.assertRaisesRegex(RuntimeError, "differs"),
        ):
            build_derived(self.job)
        self.assertIsNone(find_derived(self.spec, self.cache, self.key))
        root = derived.derived_root(self.spec, self.cache)
        self.assertEqual(list(root.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
