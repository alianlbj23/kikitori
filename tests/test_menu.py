"""評測選單與評測流程的測試：以模擬的 `input()` 驅動，用小型合成音軌跑通。

`experiments/` 不是套件，依檔案路徑載入；不需要網路與本機的版權音訊。
"""

import contextlib
import http.server
import importlib.util
import io
import json
import math
import tarfile
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import ModuleType
from unittest import mock

import numpy as np

from kikitori.evaluation import wav
from kikitori.vad import SAMPLE_RATE

EXPERIMENT_DIR = Path(__file__).resolve().parent.parent / "experiments" / "silero_reset"


def load(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        f"test_silero_reset_{name}", EXPERIMENT_DIR / f"{name}.py"
    )
    if spec is None or spec.loader is None:
        raise ImportError(name)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


menu = load("menu")
evaluate = menu.evaluate


def write_tiny_source(source_dir: Path) -> None:
    """兩段帶諧波的「人聲」（其實是合成音），夾在數位靜音之間；另附無人聲的音軌。"""
    source_dir.mkdir(parents=True)
    t = np.arange(SAMPLE_RATE) / SAMPLE_RATE
    voice = sum(
        np.sin(2 * np.pi * f * t) / k for k, f in enumerate((180, 360, 540, 720), 1)
    )
    voice = (4000 * voice / np.abs(voice).max()).astype(np.int16)
    silence = np.zeros(SAMPLE_RATE // 2, dtype=np.int16)
    audio = np.concatenate([silence, voice, silence, voice, silence])
    wav.write_wav(source_dir / "tiny.wav", audio)
    wav.write_wav(source_dir / "synthetic.wav", np.zeros(SAMPLE_RATE, dtype=np.int16))
    half = SAMPLE_RATE // 2
    truth = {
        "sample_rate": SAMPLE_RATE,
        "tracks": {
            "synthetic": {"samples": SAMPLE_RATE, "segments": []},
            "tiny": {
                "samples": len(audio),
                "segments": [
                    {
                        "file": "a",
                        "start_sample": half,
                        "end_sample": half + SAMPLE_RATE,
                        "gap_after_secs": 0.5,
                    },
                    {
                        "file": "b",
                        "start_sample": 2 * half + SAMPLE_RATE,
                        "end_sample": 2 * half + 2 * SAMPLE_RATE,
                        "gap_after_secs": 0.5,
                    },
                ],
            },
        },
    }
    (source_dir / "ground_truth.json").write_text(json.dumps(truth), encoding="utf-8")


def drive(answers: list[str], paths: object) -> str:
    out = io.StringIO()
    with (
        mock.patch("builtins.input", side_effect=[*answers, EOFError()]),
        contextlib.redirect_stdout(out),
    ):
        menu.main(paths)
    return out.getvalue()


class MenuFlowTest(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.paths = evaluate.Paths(
            source_dir=self.root / "source", data_dir=self.root / "data"
        )

    def test_invalid_input_asks_again_then_exits(self) -> None:
        output = drive(["0", "abc", "", "9", "²", str(len(menu.MENU))], self.paths)
        self.assertEqual(output.count("輸入錯誤"), 5)
        self.assertIn("離開", output)

    def test_end_of_input_exits(self) -> None:
        output = drive([], self.paths)
        self.assertIn("輸入已結束", output)

    def test_missing_source_tracks_shows_hint(self) -> None:
        with self.assertLogs("silero_reset.menu", level="ERROR"):
            output = drive(
                [self.choice("產生評測音軌"), self.choice("離開")], self.paths
            )
        self.assertIn("build_tracks.py", output)

    def test_evaluate_before_generate_is_reported(self) -> None:
        with self.assertLogs("silero_reset.menu", level="ERROR"):
            output = drive([self.choice("執行評測"), self.choice("離開")], self.paths)
        self.assertIn("產生評測音軌", output)

    def test_generate_and_evaluate_with_tiny_tracks(self) -> None:
        write_tiny_source(self.paths.source_dir)
        output = drive(
            [self.choice("產生評測音軌"), self.choice("執行評測"), self.choice("離開")],
            self.paths,
        )
        truth = json.loads(
            (self.paths.tracks_dir / "ground_truth.json").read_text(encoding="utf-8")
        )
        names = set(truth["tracks"])
        # 原始 + 長間隔兩種版面 ×（原始、白／粉紅各 1 種間隔填入 + 3 種 SNR）。
        self.assertEqual(len(names), 2 * (1 + 2 * 4))
        self.assertIn("tiny__original", names)
        self.assertIn("tiny_longgap__overlay-pink-snr5", names)
        self.assertNotIn("synthetic__original", names)
        self.assertIn("MUSAN 尚未下載", output)
        self.assertEqual(
            truth["tracks"]["tiny__gapfill-white"]["mix"]["mode"], "gap_fill"
        )

        results = json.loads(self.paths.results_json.read_text(encoding="utf-8"))
        self.assertEqual(len(results), len(names) * len(evaluate.RUN_CONFIGS))
        report = self.paths.results_md.read_text(encoding="utf-8")
        self.assertIn("## 彙總", report)
        self.assertIn("## 前文對照", report)
        self.assertIn("c_after_long_non_speech", report)
        self.assertIn("每視窗平均推論時間", report)

    def choice(self, label: str) -> str:
        return str([name for name, _ in menu.MENU].index(label) + 1)


class EvaluateUnitTest(unittest.TestCase):
    def test_relayout_moves_segments_to_long_gaps(self) -> None:
        audio = np.arange(30, dtype=np.int16)
        truth = {
            "samples": 30,
            "segments": [
                {"start_sample": 5, "end_sample": 10},
                {"start_sample": 20, "end_sample": 25},
            ],
        }
        out, new_truth = evaluate.relayout(audio, truth, [(5, 10), (20, 25)])
        lead = round(evaluate.LONG_LEAD_IN_SECS * SAMPLE_RATE)
        first = new_truth["segments"][0]
        self.assertEqual(first["start_sample"], lead)
        np.testing.assert_array_equal(out[lead : lead + 5], audio[5:10])
        second = new_truth["segments"][1]
        gap = round(evaluate.LONG_GAPS_SECS[0] * SAMPLE_RATE)
        self.assertEqual(second["start_sample"], lead + 5 + gap)
        np.testing.assert_array_equal(
            out[second["start_sample"] : second["end_sample"]], audio[20:25]
        )
        self.assertEqual(new_truth["samples"], len(out))

    def test_context_flag_marks_low_probability(self) -> None:
        def result(track: str, config: str, mean: float) -> object:
            return evaluate.RunResult(
                track,
                config,
                1,
                1,
                0,
                0.9,
                0.0,
                0.0,
                100.0,
                100.0,
                1,
                0,
                0,
                mean,
                0.0,
                0.1,
            )

        rows = evaluate.context_rows(
            [
                result("a", "a_stream_start", 0.8),
                result("a", "a_no_context", 0.3),
                result("b", "a_stream_start", 0.8),
                result("b", "a_no_context", 0.5),
            ]
        )
        self.assertEqual(
            [(t, low) for t, _, _, low in rows], [("a", True), ("b", False)]
        )

    def test_report_shows_undefined_metrics_as_dash(self) -> None:
        r = evaluate.RunResult(
            "x__original",
            "a_stream_start",
            0,
            0,
            0,
            math.nan,
            0.0,
            math.nan,
            math.nan,
            math.nan,
            0,
            0,
            0,
            math.nan,
            0.0,
            0.1,
        )
        report = evaluate.format_report([r])
        self.assertIn("| x__original | a_stream_start | — |", report)


musan = load("musan")


def make_archive(extra: list[tuple[str, bytes]] | None = None) -> bytes:
    """小型假 MUSAN 壓縮檔：music、noise、speech 各一個檔案，另附 README。"""
    members = [
        ("musan/README", b"readme"),
        ("musan/music/fma/song.wav", b"music-bytes" * 50),
        ("musan/noise/free-sound/hum.wav", b"noise-bytes" * 50),
        ("musan/speech/librivox/talk.wav", b"speech-bytes" * 50),
        *(extra or []),
    ]
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, data in members:
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


class FakeServer:
    """本機 HTTP 伺服器：支援 Range，可設定中斷、忽略 Range、延遲或錯誤狀態。"""

    def __init__(self, payload: bytes) -> None:
        self.payload = payload
        self.requests: list[dict[str, str]] = []
        self.status = 200
        self.ignore_range = False
        self.cut_first_after: int | None = None
        self.delay = 0.0
        server = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args: object) -> None:  # 測試時不輸出存取紀錄
                pass

            def do_GET(self) -> None:
                server.requests.append(dict(self.headers))
                if server.delay:
                    time.sleep(server.delay)
                if server.status != 200:
                    self.send_error(server.status)
                    return
                start = 0
                range_header = self.headers.get("Range")
                if range_header and not server.ignore_range:
                    start = int(range_header.removeprefix("bytes=").split("-")[0])
                    self.send_response(206)
                    total = len(server.payload)
                    self.send_header(
                        "Content-Range", f"bytes {start}-{total - 1}/{total}"
                    )
                else:
                    self.send_response(200)
                body = server.payload[start:]
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                if server.cut_first_after is not None:
                    # 第一次只送一部分就斷線，模擬下載中斷。
                    self.wfile.write(body[: server.cut_first_after])
                    server.cut_first_after = None
                    self.close_connection = True
                    return
                self.wfile.write(body)

        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.httpd.daemon_threads = True
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}/musan.tar.gz"

    def close(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()


class MusanTestCase(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.data_dir = Path(tmp.name) / "data"
        self.payload = make_archive()
        self.server = FakeServer(self.payload)
        self.addCleanup(self.server.close)
        self.source = musan.MusanSource(
            url=self.server.url,
            size_bytes=len(self.payload),
            extracted_bytes_estimate=1000,
        )

    def run_menu(self, answers: list[str], **kwargs: object) -> str:
        out = io.StringIO()
        with (
            mock.patch("builtins.input", side_effect=[*answers, EOFError()]),
            contextlib.redirect_stdout(out),
        ):
            musan.download_menu(self.data_dir, self.source, timeout=2.0, **kwargs)
        return out.getvalue()


class MusanDownloadMenuTest(MusanTestCase):
    def test_shows_details_and_does_nothing_without_confirmation(self) -> None:
        for answer in ("n", "", "no"):
            with self.subTest(answer=answer):
                output = self.run_menu([answer])
                self.assertIn(self.server.url, output)
                self.assertIn("CC BY 4.0", output)
                self.assertIn("已取消", output)
                self.assertEqual(self.server.requests, [])
                self.assertFalse((self.data_dir / musan.ARCHIVE_NAME).exists())

    def test_default_source_describes_size_and_license(self) -> None:
        text = musan.describe(musan.MusanSource())
        self.assertIn("https://www.openslr.org/resources/17/musan.tar.gz", text)
        self.assertIn("11.09 GB", text)
        self.assertIn("CC BY 4.0", text)

    def test_confirmed_download_extracts_only_music_and_noise(self) -> None:
        output = self.run_menu(["y", "n"])
        musan_dir = self.data_dir / "musan"
        self.assertTrue(musan.is_ready(musan_dir))
        self.assertEqual(
            (musan_dir / "music" / "fma" / "song.wav").read_bytes(),
            b"music-bytes" * 50,
        )
        self.assertTrue((musan_dir / "noise" / "free-sound" / "hum.wav").is_file())
        self.assertFalse((musan_dir / "speech").exists())
        self.assertFalse((musan_dir / "README").exists())
        attribution = (musan_dir / musan.ATTRIBUTION_FILE).read_text(encoding="utf-8")
        self.assertIn("CC BY 4.0", attribution)
        self.assertIn("Snyder", attribution)
        self.assertIn(self.server.url, attribution)
        self.assertTrue((self.data_dir / musan.ARCHIVE_NAME).is_file())
        self.assertIn("完成", output)

    def test_archive_deleted_when_requested(self) -> None:
        self.run_menu(["y", "y"])
        self.assertFalse((self.data_dir / musan.ARCHIVE_NAME).exists())
        self.assertTrue(musan.is_ready(self.data_dir / "musan"))

    def test_already_ready_skips_download(self) -> None:
        self.run_menu(["y", "n"])
        requests = len(self.server.requests)
        output = self.run_menu([])
        self.assertIn("已就緒", output)
        self.assertEqual(len(self.server.requests), requests)

    def test_insufficient_disk_space_refuses(self) -> None:
        output = self.run_menu(["y"], free_bytes=lambda _path: 10)
        self.assertIn("磁碟空間不足", output)
        self.assertEqual(self.server.requests, [])


class MusanDownloadTest(MusanTestCase):
    def dest(self) -> Path:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        return self.data_dir / "musan.tar.gz"

    def download(self, dest: Path, **kwargs: object) -> None:
        options: dict[str, object] = {
            "expected_size": len(self.payload),
            "timeout": 2.0,
            "attempts": 3,
            **kwargs,
        }
        musan.download(self.source.url, dest, **options)

    def test_resumes_from_partial_file(self) -> None:
        dest = self.dest()
        dest.with_name(dest.name + ".part").write_bytes(self.payload[:100])
        self.download(dest)
        self.assertEqual(dest.read_bytes(), self.payload)
        self.assertEqual(self.server.requests[0].get("Range"), "bytes=100-")

    def test_restarts_when_server_ignores_range(self) -> None:
        dest = self.dest()
        dest.with_name(dest.name + ".part").write_bytes(b"garbage-garbage")
        self.server.ignore_range = True
        self.download(dest)
        self.assertEqual(dest.read_bytes(), self.payload)

    def test_interrupted_transfer_is_resumed(self) -> None:
        dest = self.dest()
        self.server.cut_first_after = cut = len(self.payload) // 2
        with self.assertLogs("silero_reset.musan", level="WARNING"):
            self.download(dest)
        self.assertEqual(dest.read_bytes(), self.payload)
        self.assertEqual(len(self.server.requests), 2)
        self.assertEqual(self.server.requests[1].get("Range"), f"bytes={cut}-")

    def test_timeout_is_reported_with_url_and_path(self) -> None:
        dest = self.dest()
        self.server.delay = 1.5
        with (
            self.assertLogs("silero_reset.musan", level="WARNING"),
            self.assertRaises(musan.DownloadError) as ctx,
        ):
            self.download(dest, timeout=0.3, attempts=2)
        self.assertIn(self.source.url, str(ctx.exception))
        self.assertIn(str(dest), str(ctx.exception))
        self.assertEqual(len(self.server.requests), 2)

    def test_http_error_is_not_retried(self) -> None:
        dest = self.dest()
        self.server.status = 404
        with self.assertRaises(musan.DownloadError) as ctx:
            self.download(dest)
        self.assertIn("404", str(ctx.exception))
        self.assertIn(self.source.url, str(ctx.exception))
        self.assertEqual(len(self.server.requests), 1)

    def test_attempts_must_be_positive(self) -> None:
        with self.assertRaises(ValueError):
            self.download(self.dest(), attempts=0)
        self.assertEqual(self.server.requests, [])

    def test_size_mismatch_is_rejected(self) -> None:
        dest = self.dest()
        with self.assertRaises(musan.DownloadError):
            self.download(dest, expected_size=len(self.payload) + 5, attempts=1)
        self.assertFalse(dest.exists())


class MusanExtractTest(unittest.TestCase):
    def test_unsafe_member_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp) / "musan.tar.gz"
            archive.write_bytes(make_archive([("musan/music/../../evil.txt", b"x")]))
            with self.assertRaises(ValueError):
                musan.extract_subset(archive, Path(tmp) / "musan")
            self.assertFalse((Path(tmp) / "evil.txt").exists())
            self.assertFalse(musan.is_ready(Path(tmp) / "musan"))

    def test_corrupt_archive_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp) / "musan.tar.gz"
            archive.write_bytes(b"not a gzip file")
            with self.assertRaises(ValueError) as ctx:
                musan.extract_subset(archive, Path(tmp) / "musan")
            self.assertIn("delete", str(ctx.exception))

    def test_truncated_archive_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp) / "musan.tar.gz"
            data = make_archive()
            archive.write_bytes(data[: len(data) * 2 // 3])
            with self.assertRaises(ValueError):
                musan.extract_subset(archive, Path(tmp) / "musan")
            self.assertFalse(musan.is_ready(Path(tmp) / "musan"))


class MenuMusanOptionTest(MusanTestCase):
    def test_menu_offers_musan_download(self) -> None:
        labels = [name for name, _ in menu.MENU]
        self.assertEqual(labels[0], "下載 MUSAN")
        paths = evaluate.Paths(source_dir=self.data_dir / "src", data_dir=self.data_dir)
        answers = ["1", "y", "n", str(len(labels))]
        out = io.StringIO()
        with (
            mock.patch("builtins.input", side_effect=[*answers, EOFError()]),
            contextlib.redirect_stdout(out),
        ):
            menu.main(paths, musan_source=self.source)
        self.assertTrue(musan.is_ready(paths.musan_dir))

    def test_generate_adds_musan_variants_when_ready(self) -> None:
        paths = evaluate.Paths(source_dir=self.data_dir / "src", data_dir=self.data_dir)
        write_tiny_source(paths.source_dir)
        rng = np.random.default_rng(0)
        for kind in ("music", "noise"):
            folder = paths.musan_dir / kind / "x"
            folder.mkdir(parents=True)
            for i in range(6):
                clip = (rng.standard_normal(SAMPLE_RATE * 8) * 1000).astype(np.int16)
                wav.write_wav(folder / f"{kind}-{i}.wav", clip)
        # 0–2 號標成有人聲演唱，其餘沒有標註 → 純音樂。
        (paths.musan_dir / "music" / "x" / "ANNOTATIONS").write_text(
            "music-0 pop Y a\nmusic-1 rock Y b\nmusic-2 jazz Y c\nmusic-3 jazz N d\n",
            encoding="utf-8",
        )
        (paths.musan_dir / musan.COMPLETE_MARKER).write_text("ok", encoding="utf-8")
        names = evaluate.generate_tracks(paths, log=lambda _msg: None)
        self.assertIn("tiny__gapfill-musan_music_vocal", names)
        self.assertIn("tiny__overlay-musan_music_inst-snr10", names)
        self.assertIn("tiny_longgap__overlay-musan_noise-snr5", names)
        # 白、粉紅、有人聲音樂、純音樂、雜音 = 5 種背景。
        self.assertEqual(len(names), 2 * (1 + 5 * 4))
        truth = json.loads(
            (paths.tracks_dir / "ground_truth.json").read_text(encoding="utf-8")
        )
        vocal = truth["tracks"]["tiny__gapfill-musan_music_vocal"]["mix"]["files"]
        inst = truth["tracks"]["tiny__gapfill-musan_music_inst"]["mix"]["files"]
        self.assertTrue(vocal)
        self.assertTrue(
            all(
                f.split("/")[-1] in {"music-0.wav", "music-1.wav", "music-2.wav"}
                for f in vocal
            )
        )
        self.assertTrue(all(f.startswith("music/") for f in inst))
        self.assertFalse(set(vocal) & set(inst))


if __name__ == "__main__":
    unittest.main()
