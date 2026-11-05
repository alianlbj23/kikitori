"""soundcard 後端的測試：以假的 soundcard 模組驗證裝置對應、預設裝置、擷取執行緒與錯誤對應。

不開啟任何真實裝置，可在沒有音效卡的 CI 機器上執行。
"""

import threading
import time
import unittest
from collections.abc import Callable
from typing import Any, Self

import numpy as np

from kikitori.capture import _soundcard_backend as backend
from kikitori.capture.errors import (
    BackendUnavailableError,
    CaptureError,
    DeviceLostError,
    DeviceNotFoundError,
)


class FakeRecorder:
    def __init__(
        self, mic: "FakeMic", samplerate: int, channels: int, blocksize: int
    ) -> None:
        self.mic = mic
        self.samplerate = samplerate
        self.channels = channels
        self.blocksize = blocksize
        self.entered = False
        self.exited = False

    def __enter__(self) -> Self:
        if self.mic.open_error is not None:
            raise self.mic.open_error
        if self.mic.open_delay:
            time.sleep(self.mic.open_delay)
        self.entered = True
        return self

    def __exit__(self, *exc: object) -> None:
        self.exited = True

    def record(self, numframes: int | None = None) -> np.ndarray:
        assert numframes is None
        return self.mic.next_block(self.channels)


class FakeMic:
    def __init__(
        self,
        id: str,
        name: str,
        *,
        isloopback: bool = False,
        channels: Any = 2,
        blocks: list[np.ndarray | BaseException] | None = None,
        open_error: BaseException | None = None,
        open_delay: float = 0.0,
    ) -> None:
        self.id = id
        self.name = name
        self.isloopback = isloopback
        self.channels = channels
        self.blocks = list(blocks or [])
        self.open_error = open_error
        self.open_delay = open_delay
        self.recorders: list[FakeRecorder] = []
        self.drained = threading.Event()

    def recorder(self, samplerate: int, channels: int, blocksize: int) -> FakeRecorder:
        rec = FakeRecorder(self, samplerate, channels, blocksize)
        self.recorders.append(rec)
        return rec

    def next_block(self, channels: int) -> np.ndarray:
        if self.blocks:
            item = self.blocks.pop(0)
            if isinstance(item, BaseException):
                raise item
            return item
        self.drained.set()
        time.sleep(0.005)  # 模擬裝置週期，沒資料時回傳空區塊
        return np.zeros((0, channels), dtype=np.float32)


class FakeSoundcard:
    """模擬 soundcard 的模組層級函式。"""

    def __init__(
        self,
        mics: list[FakeMic],
        default_speaker_id: str | None,
        default_mic_id: str | None,
    ) -> None:
        self.mics = mics
        self.default_speaker_id = default_speaker_id
        self.default_mic_id = default_mic_id
        self.include_loopback_calls: list[bool] = []

    def all_microphones(self, include_loopback: bool = False) -> list[FakeMic]:
        self.include_loopback_calls.append(include_loopback)
        return [m for m in self.mics if include_loopback or not m.isloopback]

    def default_speaker(self) -> Any:
        if self.default_speaker_id is None:
            raise IndexError("no soundcard with id None")
        return type("Speaker", (), {"id": self.default_speaker_id, "name": "spk"})()

    def default_microphone(self) -> Any:
        if self.default_mic_id is None:
            raise IndexError("no soundcard with id None")
        return type("Mic", (), {"id": self.default_mic_id})()


def linux_fake(**mic_kwargs: Any) -> FakeSoundcard:
    return FakeSoundcard(
        [
            FakeMic(
                "alsa_output.analog.monitor",
                "Monitor of Built-in",
                isloopback=True,
                **mic_kwargs,
            ),
            FakeMic("alsa_output.hdmi.monitor", "Monitor of HDMI", isloopback=True),
            FakeMic("alsa_input.analog", "Built-in Mic", channels=1),
        ],
        default_speaker_id="alsa_output.analog",
        default_mic_id="alsa_input.analog",
    )


class ListDevicesTest(unittest.TestCase):
    def test_linux_maps_monitors_and_microphones(self) -> None:
        sc = linux_fake()
        devices = backend.list_devices(sc, "linux")
        self.assertEqual(sc.include_loopback_calls, [True])
        by_id = {d.id: d for d in devices}
        self.assertEqual(
            sorted(by_id),
            [
                "soundcard:loopback:alsa_output.analog.monitor",
                "soundcard:loopback:alsa_output.hdmi.monitor",
                "soundcard:microphone:alsa_input.analog",
            ],
        )
        self.assertEqual(
            by_id["soundcard:loopback:alsa_output.hdmi.monitor"].name, "Monitor of HDMI"
        )
        self.assertEqual(
            by_id["soundcard:loopback:alsa_output.hdmi.monitor"].kind, "loopback"
        )
        self.assertEqual(
            by_id["soundcard:microphone:alsa_input.analog"].kind, "microphone"
        )

    def test_linux_default_loopback_is_monitor_of_default_sink(self) -> None:
        sc = FakeSoundcard(
            [
                FakeMic(
                    "alsa_output.pci.analog-stereo.monitor",
                    "Monitor of Built-in",
                    isloopback=True,
                ),
                FakeMic("alsa_output.hdmi.monitor", "Monitor of HDMI", isloopback=True),
                FakeMic("alsa_input.pci.analog-stereo", "Built-in Mic"),
            ],
            default_speaker_id="alsa_output.pci.analog-stereo",
            default_mic_id="alsa_input.pci.analog-stereo",
        )
        devices = {d.id: d for d in backend.list_devices(sc, "linux")}
        self.assertTrue(
            devices[
                "soundcard:loopback:alsa_output.pci.analog-stereo.monitor"
            ].is_default
        )
        self.assertFalse(
            devices["soundcard:loopback:alsa_output.hdmi.monitor"].is_default
        )
        self.assertTrue(
            devices["soundcard:microphone:alsa_input.pci.analog-stereo"].is_default
        )

    def test_macos_lists_microphones_without_requesting_loopback(self) -> None:
        sc = FakeSoundcard(
            [FakeMic("73", "MacBook Microphone", channels=1)], None, "73"
        )
        devices = backend.list_devices(sc, "darwin")
        self.assertEqual(sc.include_loopback_calls, [False])
        self.assertEqual([d.id for d in devices], ["soundcard:microphone:73"])
        self.assertTrue(devices[0].is_default)

    def test_no_default_devices_means_nothing_is_default(self) -> None:
        sc = linux_fake()
        sc.default_speaker_id = None
        sc.default_mic_id = None
        with self.assertLogs("kikitori.capture._soundcard_backend", "INFO"):
            devices = backend.list_devices(sc, "linux")
        self.assertFalse(any(d.is_default for d in devices))

    def test_enumeration_failure_is_wrapped(self) -> None:
        sc = linux_fake()

        def broken(include_loopback: bool = False) -> list[FakeMic]:
            raise RuntimeError("Connection refused")

        sc.all_microphones = broken  # type: ignore[method-assign]
        with self.assertRaises(CaptureError):
            backend.list_devices(sc, "linux")


class FindMicrophoneTest(unittest.TestCase):
    def test_default_loopback_and_microphone(self) -> None:
        sc = linux_fake()
        self.assertEqual(
            backend.find_microphone(sc, "linux", "loopback", None).id,
            "alsa_output.analog.monitor",
        )
        self.assertEqual(
            backend.find_microphone(sc, "linux", "microphone", None).id,
            "alsa_input.analog",
        )

    def test_by_native_id(self) -> None:
        sc = linux_fake()
        self.assertEqual(
            backend.find_microphone(
                sc, "linux", "loopback", "alsa_output.hdmi.monitor"
            ).name,
            "Monitor of HDMI",
        )

    def test_kind_must_match(self) -> None:
        sc = linux_fake()
        with self.assertRaises(DeviceNotFoundError):
            backend.find_microphone(
                sc, "linux", "microphone", "alsa_output.analog.monitor"
            )

    def test_unknown_id_is_not_fuzzy_matched(self) -> None:
        sc = linux_fake()
        with self.assertRaises(DeviceNotFoundError):
            backend.find_microphone(sc, "linux", "loopback", "Monitor")

    def test_no_default_loopback(self) -> None:
        sc = linux_fake()
        sc.default_speaker_id = None
        with self.assertRaises(DeviceNotFoundError):
            backend.find_microphone(sc, "linux", "loopback", None)

    def test_no_loopback_on_macos(self) -> None:
        sc = FakeSoundcard([FakeMic("73", "Mic")], None, "73")
        with self.assertRaises(DeviceNotFoundError):
            backend.find_microphone(sc, "darwin", "loopback", None)


class ErrorMappingTest(unittest.TestCase):
    def test_known_errors(self) -> None:
        cases: list[tuple[BaseException, type[CaptureError]]] = [
            (
                RuntimeError("Recording failed, stream is in status FAILED"),
                DeviceLostError,
            ),
            (IndexError("no soundcard with id x"), DeviceNotFoundError),
            (AssertionError(), CaptureError),
            (RuntimeError("invalid sample spec"), CaptureError),
        ]
        for exc, expected in cases:
            with self.subTest(exc=exc):
                mapped = backend.map_error(exc)
                self.assertIs(type(mapped), expected)
                self.assertIs(mapped.__cause__, exc)

    def test_capture_errors_pass_through(self) -> None:
        err = DeviceLostError("x")
        self.assertIs(backend.map_error(err), err)


class LoadSoundcardTest(unittest.TestCase):
    def test_import_failure_becomes_backend_unavailable(self) -> None:
        def failing_import(name: str) -> Any:
            raise OSError("cannot load library 'pulse'")

        with self.assertRaises(BackendUnavailableError):
            backend.load_soundcard(importer=failing_import)

    def test_no_pulseaudio_server_becomes_backend_unavailable(self) -> None:
        # soundcard 0.4.6 匯入時就連線；沒有伺服器時 context 不是 READY，它用 assert
        # 檢查，所以拋的是 AssertionError（容器實測：PULSE_SERVER 指到不存在的 socket）。
        def no_server_import(name: str) -> Any:
            raise AssertionError()

        with self.assertRaises(BackendUnavailableError):
            backend.load_soundcard(importer=no_server_import)


class OpenMicrophoneTest(unittest.TestCase):
    """PulseAudio／PipeWire 在裝置移除時會把錄音串流搬到預設 source（容器實測：
    拔掉輸出裝置後，monitor 的串流被搬到預設麥克風、繼續送資料、沒有任何錯誤）。
    `open_microphone()` 的來源要定期確認裝置還在，不在就拋 `DeviceLostError`。"""

    def endless_blocks(self, mic: FakeMic) -> None:
        # 讓假裝置一直有資料（模擬串流被搬到別的裝置後繼續送）。
        mic.next_block = lambda channels: (  # type: ignore[method-assign]
            time.sleep(0.005) or np.full((240, channels), 0.1, np.float32)
        )

    def test_resolves_default_and_explicit_devices(self) -> None:
        sc = linux_fake()
        default = backend.open_microphone(sc, "linux", "loopback", None)
        named = backend.open_microphone(sc, "linux", "microphone", "alsa_input.analog")
        self.assertIsInstance(default, backend.SoundcardSource)
        self.assertEqual(default.microphone.id, "alsa_output.analog.monitor")
        self.assertEqual(named.microphone.id, "alsa_input.analog")
        with self.assertRaises(DeviceNotFoundError):
            backend.open_microphone(sc, "linux", "microphone", "gone")

    def test_device_removed_while_capturing_raises_device_lost(self) -> None:
        sc = linux_fake()
        mic = sc.mics[0]
        self.endless_blocks(mic)
        src = backend.open_microphone(
            sc, "linux", "loopback", mic.id, presence_interval=0.02
        )
        src.start()
        try:
            self.assertGreater(len(src.read(timeout=1.0)), 0)
            sc.mics.remove(mic)  # 裝置從清單消失，但串流仍有資料
            with (
                self.assertLogs("kikitori.capture.threaded", "ERROR"),
                self.assertRaises(DeviceLostError),
            ):
                deadline = time.monotonic() + 5.0
                while time.monotonic() < deadline:
                    src.read(timeout=0.1)
        finally:
            src.stop()

    def test_present_device_keeps_capturing(self) -> None:
        sc = linux_fake()
        mic = sc.mics[0]
        self.endless_blocks(mic)
        src = backend.open_microphone(
            sc, "linux", "loopback", None, presence_interval=0.01
        )
        src.start()
        try:
            total = 0
            deadline = time.monotonic() + 0.3
            while time.monotonic() < deadline:
                total += len(src.read(timeout=0.1))
        finally:
            src.stop()
        self.assertGreater(total, 0)
        # 有確認過裝置還在（不只是建立時列舉一次）。
        self.assertGreater(len(sc.include_loopback_calls), 3)

    def run_with_listing(
        self, listing: "Callable[[bool], list[FakeMic]]", seconds: float
    ) -> tuple[int, BaseException | None]:
        """以自訂的 all_microphones 跑一段時間；回傳收到的樣本數與 read 拋出的錯誤。"""
        sc = linux_fake()
        mic = sc.mics[0]
        self.endless_blocks(mic)
        src = backend.open_microphone(
            sc, "linux", "loopback", mic.id, presence_interval=0.01
        )
        sc.all_microphones = listing  # type: ignore[method-assign]
        src.start()
        total, error = 0, None
        try:
            deadline = time.monotonic() + seconds
            while time.monotonic() < deadline:
                total += len(src.read(timeout=0.1))
        except CaptureError as exc:
            error = exc
        finally:
            src.stop()
        return total, error

    def test_single_missing_listing_is_tolerated(self) -> None:
        # 列舉偶爾漏掉一次（伺服器重新設定）不算裝置消失；連續消失才算。
        sc = linux_fake()
        calls = {"n": 0}

        def flaky(include_loopback: bool = False) -> list[FakeMic]:
            calls["n"] += 1
            return [] if calls["n"] % 3 == 0 else list(sc.mics)

        total, error = self.run_with_listing(flaky, 0.4)
        self.assertIsNone(error)
        self.assertGreater(total, 0)

    def test_listing_failure_is_not_device_loss(self) -> None:
        # 擷取中列舉失敗（例如 IndexError）不能被當成 DeviceNotFoundError／DeviceLostError
        # 結束 session；串流本身有問題時 record() 會自己報錯。
        def broken(include_loopback: bool = False) -> list[FakeMic]:
            raise IndexError("no soundcard with id 7")

        with self.assertLogs("kikitori.capture._soundcard_backend", "WARNING"):
            total, error = self.run_with_listing(broken, 0.3)
        self.assertIsNone(error)
        self.assertGreater(total, 0)

    def test_invalid_presence_interval(self) -> None:
        for value in (0.0, -1.0, float("nan")):
            with self.subTest(value=value), self.assertRaises(ValueError):
                backend.open_microphone(
                    linux_fake(), "linux", "loopback", None, presence_interval=value
                )


def wait_until(pred: Callable[[], bool], timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pred():
            return True
        time.sleep(0.005)
    return False


class SoundcardSourceTest(unittest.TestCase):
    def make_tone_blocks(self, count: int, frames: int = 960) -> list[np.ndarray]:
        t = np.arange(count * frames) / 48000
        sig = (0.5 * np.sin(2 * np.pi * 1000 * t)).astype(np.float32)
        stereo = np.stack([sig, sig], axis=1)
        return [stereo[i * frames : (i + 1) * frames] for i in range(count)]

    def read_until(self, src: backend.SoundcardSource, samples: int) -> np.ndarray:
        parts: list[np.ndarray] = []
        deadline = time.monotonic() + 5.0
        while sum(map(len, parts)) < samples and time.monotonic() < deadline:
            parts.append(src.read(timeout=0.1))
        return np.concatenate(parts)

    def test_delivers_16k_mono_int16_and_opens_with_48k_native_channels(self) -> None:
        mic = FakeMic(
            "alsa_output.analog.monitor",
            "Speakers",
            isloopback=True,
            blocks=self.make_tone_blocks(25),
        )
        src = backend.SoundcardSource(mic)
        src.start()
        try:
            out = self.read_until(src, 8000)
        finally:
            src.stop()
        self.assertEqual(out.dtype, np.int16)
        self.assertEqual(out.ndim, 1)
        self.assertEqual(len(out), 8000)  # 25 × 20 ms = 0.5 s
        rec = mic.recorders[0]
        self.assertEqual((rec.samplerate, rec.channels, rec.blocksize), (48000, 2, 960))
        self.assertTrue(rec.exited)
        rms = np.sqrt(np.mean(out[1600:].astype(np.float64) ** 2)) / 32768
        self.assertAlmostEqual(rms, 0.5 / np.sqrt(2), delta=0.01)

    def test_mono_device_opened_with_one_channel(self) -> None:
        mic = FakeMic(
            "{mic}", "Mic", channels=1, blocks=[np.zeros((960, 1), np.float32)]
        )
        src = backend.SoundcardSource(mic)
        src.start()
        try:
            self.assertEqual(len(self.read_until(src, 320)), 320)
        finally:
            src.stop()
        self.assertEqual(mic.recorders[0].channels, 1)

    def test_read_timeout_returns_empty(self) -> None:
        mic = FakeMic("{mic}", "Mic", channels=1)
        src = backend.SoundcardSource(mic)
        src.start()
        try:
            self.assertTrue(mic.drained.wait(5.0))
            out = src.read(timeout=0.02)
        finally:
            src.stop()
        self.assertEqual(out.shape, (0,))
        self.assertEqual(out.dtype, np.int16)

    def test_device_lost_mid_stream_raises_after_data(self) -> None:
        blocks: list[np.ndarray | BaseException] = [
            np.zeros((960, 2), np.float32),
            RuntimeError("Recording failed, stream is in status FAILED"),
        ]
        mic = FakeMic("{spk}", "Speakers", isloopback=True, blocks=blocks)
        src = backend.SoundcardSource(mic)
        with self.assertLogs("kikitori.capture.threaded", "ERROR"):
            src.start()
            self.assertTrue(wait_until(lambda: not src._thread_alive()))
        self.assertEqual(len(src.read(timeout=1.0)), 320)
        with self.assertRaises(DeviceLostError):
            src.read(timeout=1.0)
        src.stop()

    def test_open_failure_raises_from_start(self) -> None:
        mic = FakeMic("{mic}", "Mic", open_error=RuntimeError("Stream creation failed"))
        src = backend.SoundcardSource(mic)
        with (
            self.assertLogs("kikitori.capture.threaded", "ERROR"),
            self.assertRaises(CaptureError),
        ):
            src.start()

    def test_open_timeout(self) -> None:
        mic = FakeMic("{mic}", "Mic", open_delay=0.5)
        src = backend.SoundcardSource(mic, open_timeout=0.05, join_timeout=1.0)
        with self.assertRaises(CaptureError):
            src.start()
        self.assertTrue(wait_until(lambda: not src._thread_alive()))

    def test_bad_channel_count_is_reported(self) -> None:
        mic = FakeMic("{mic}", "Mic", channels=0)
        src = backend.SoundcardSource(mic)
        with (
            self.assertLogs("kikitori.capture.threaded", "ERROR"),
            self.assertRaises(CaptureError),
        ):
            src.start()

    def test_lifecycle_errors(self) -> None:
        mic = FakeMic("{mic}", "Mic", channels=1)
        src = backend.SoundcardSource(mic)
        with self.assertRaises(CaptureError):
            src.read(timeout=0)
        src.start()
        try:
            with self.assertRaises(CaptureError):
                src.start()
        finally:
            src.stop()
        src.stop()
        self.assertEqual(src.read(timeout=0.5).shape, (0,))
        self.assertFalse(src._thread_alive())

    def test_overflow_counts_dropped_samples(self) -> None:
        mic = FakeMic("{spk}", "Spk", isloopback=True, blocks=self.make_tone_blocks(50))
        src = backend.SoundcardSource(mic, max_buffer_seconds=0.1)
        with self.assertLogs("kikitori.capture.buffer", "WARNING"):
            src.start()
            self.assertTrue(mic.drained.wait(5.0))
        try:
            out = src.read(timeout=0.5)
        finally:
            src.stop()
        self.assertEqual(len(out), 1600)
        self.assertEqual(src.dropped_samples, 16000 - 1600)

    def test_invalid_options(self) -> None:
        mic = FakeMic("{mic}", "Mic")
        for kwargs in (
            {"capture_rate": 0},
            {"block_seconds": 0.0},
            {"max_buffer_seconds": -1.0},
            {"open_timeout": float("nan")},
        ):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                backend.SoundcardSource(mic, **kwargs)


if __name__ == "__main__":
    unittest.main()
