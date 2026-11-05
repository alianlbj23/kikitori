"""Windows WASAPI 後端的測試：以假的 PyAudioWPatch 驗證裝置對應、預設裝置、補零與錯誤路徑。

不開啟任何真實裝置，可在任何作業系統的 CI 上執行。
"""

import time
import unittest
from collections.abc import Callable
from typing import Any

import numpy as np

from kikitori.capture import _wasapi_backend as backend
from kikitori.capture.errors import (
    BackendUnavailableError,
    CaptureError,
    DeviceLostError,
    DeviceNotFoundError,
)

PA_FLOAT32 = 1
PA_WASAPI = 13


def dev(
    index: int,
    name: str,
    *,
    inputs: int = 0,
    outputs: int = 0,
    rate: float = 48000.0,
    loopback: bool = False,
) -> dict[str, Any]:
    return {
        "index": index,
        "name": name,
        "maxInputChannels": inputs,
        "maxOutputChannels": outputs,
        "defaultSampleRate": rate,
        "isLoopbackDevice": loopback,
    }


class FakeStream:
    def __init__(self, script: list[Any], channels: int) -> None:
        # script 的每一項：int（可讀的 frame 數）或例外
        self.script = list(script)
        self.channels = channels
        self.pending = 0
        self.stopped = False
        self.closed = False
        self.counter = 0

    def get_read_available(self) -> int:
        if self.script:
            item = self.script.pop(0)
            if isinstance(item, BaseException):
                raise item
            self.pending = item
        else:
            self.pending = 0
        return self.pending

    def read(self, frames: int, exception_on_overflow: bool = True) -> bytes:
        assert exception_on_overflow is False
        assert frames == self.pending
        data = np.full((frames, self.channels), 0.25, dtype=np.float32)
        self.counter += frames
        return data.tobytes()

    def stop_stream(self) -> None:
        self.stopped = True

    def close(self) -> None:
        self.closed = True


class FakePyAudio:
    def __init__(self, module: "FakeModule") -> None:
        self.module = module
        self.terminated = False
        module.instances.append(self)

    def get_device_info_generator_by_host_api(self, *, host_api_type: int) -> Any:
        assert host_api_type == PA_WASAPI
        if self.module.wasapi_error is not None:
            raise self.module.wasapi_error
        yield from self.module.devices

    def get_device_count(self) -> int:
        return 64

    def get_default_wasapi_loopback(self) -> dict[str, Any]:
        if self.module.default_loopback is None:
            raise LookupError("No analogue is found")
        return self.module.devices_by_index[self.module.default_loopback]

    def get_default_wasapi_device(self, *, d_in: bool = False) -> dict[str, Any]:
        assert d_in
        if self.module.default_input is None:
            raise OSError(-9996, "Invalid device info")
        return self.module.devices_by_index[self.module.default_input]

    def open(self, **kwargs: Any) -> FakeStream:
        self.module.open_calls.append(kwargs)
        if self.module.open_error is not None:
            raise self.module.open_error
        stream = FakeStream(self.module.stream_script, kwargs["channels"])
        self.module.streams.append(stream)
        return stream

    def terminate(self) -> None:
        self.terminated = True


class FakeModule:
    paFloat32 = PA_FLOAT32
    paWASAPI = PA_WASAPI

    def __init__(self) -> None:
        self.devices = [
            dev(20, "Speakers (Realtek)", outputs=2),
            dev(21, "HDMI (NVIDIA)", outputs=8),
            dev(26, "Headset Mic", inputs=1, rate=16000.0),
            dev(27, "USB Interface", inputs=2),
            dev(28, "Speakers (Realtek) [Loopback]", inputs=2, loopback=True),
            dev(29, "HDMI (NVIDIA) [Loopback]", inputs=8, loopback=True),
            dev(30, "USB Interface", inputs=2),  # 同名裝置
        ]
        self.default_loopback: int | None = 28
        self.default_input: int | None = 27
        self.wasapi_error: BaseException | None = None
        self.open_error: BaseException | None = None
        self.stream_script: list[Any] = []
        self.instances: list[FakePyAudio] = []
        self.open_calls: list[dict[str, Any]] = []
        self.streams: list[FakeStream] = []

    @property
    def devices_by_index(self) -> dict[int, dict[str, Any]]:
        return {d["index"]: d for d in self.devices}

    def PyAudio(self) -> FakePyAudio:
        return FakePyAudio(self)


class ListDevicesTest(unittest.TestCase):
    def test_maps_loopback_and_inputs_skipping_outputs(self) -> None:
        mod = FakeModule()
        devices = backend.list_devices(mod)  # type: ignore[arg-type]
        self.assertEqual(
            [(d.id, d.kind, d.is_default) for d in devices],
            [
                ("wasapi:microphone:Headset Mic", "microphone", False),
                ("wasapi:microphone:USB Interface", "microphone", True),
                ("wasapi:loopback:Speakers (Realtek) [Loopback]", "loopback", True),
                ("wasapi:loopback:HDMI (NVIDIA) [Loopback]", "loopback", False),
                ("wasapi:microphone:USB Interface#2", "microphone", False),
            ],
        )
        self.assertEqual(devices[4].name, "USB Interface")
        self.assertTrue(all(p.terminated for p in mod.instances))

    def test_no_defaults(self) -> None:
        mod = FakeModule()
        mod.default_loopback = None
        mod.default_input = None
        with self.assertLogs("kikitori.capture._wasapi_backend", "INFO"):
            devices = backend.list_devices(mod)  # type: ignore[arg-type]
        self.assertFalse(any(d.is_default for d in devices))

    def test_wasapi_missing_is_backend_unavailable(self) -> None:
        mod = FakeModule()
        mod.wasapi_error = OSError(-9979, "Host API not found")
        with self.assertRaises(BackendUnavailableError):
            backend.list_devices(mod)  # type: ignore[arg-type]
        self.assertTrue(mod.instances[0].terminated)

    def test_import_failure_is_backend_unavailable(self) -> None:
        def failing(name: str) -> Any:
            raise ImportError("No module named 'pyaudiowpatch'")

        with self.assertRaises(BackendUnavailableError):
            backend.load_pyaudio(importer=failing)


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds
        time.sleep(0.001)  # 讓出 CPU，避免測試執行緒空轉


def wait_until(pred: Callable[[], bool], timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pred():
            return True
        time.sleep(0.005)
    return False


class WasapiSourceTest(unittest.TestCase):
    def make_source(
        self, mod: FakeModule, kind: str, native_id: str | None, **kwargs: Any
    ) -> backend.WasapiSource:
        return backend.WasapiSource(
            kind,  # type: ignore[arg-type]
            native_id,
            loader=lambda: mod,  # type: ignore[arg-type,return-value]
            **kwargs,
        )

    def test_default_loopback_opens_native_format(self) -> None:
        mod = FakeModule()
        mod.stream_script = [960] * 50
        # 時鐘固定不動：資料讀完後不會觸發補零，輸出只含真實資料。
        src = self.make_source(mod, "loopback", None, clock=lambda: 0.0)
        src.start()
        parts: list[np.ndarray] = []
        try:
            deadline = time.monotonic() + 5.0
            while sum(map(len, parts)) < 16000 and time.monotonic() < deadline:
                parts.append(src.read(timeout=0.2))
        finally:
            src.stop()
        out = np.concatenate(parts)
        call = mod.open_calls[0]
        self.assertEqual(
            (
                call["input_device_index"],
                call["channels"],
                call["rate"],
                call["format"],
            ),
            (28, 2, 48000, PA_FLOAT32),
        )
        self.assertTrue(call["input"])
        self.assertEqual(call["frames_per_buffer"], 960)
        self.assertEqual((src.device_sample_rate, src.device_channels), (48000, 2))
        self.assertEqual(out.dtype, np.int16)
        self.assertEqual(len(out), 16000)
        # 0.25 的直流訊號經 48k→16k 濾波後仍約為 0.25（8192）
        self.assertTrue(np.all(np.abs(out[-100:].astype(int) - 8192) <= 2))
        stream = mod.streams[0]
        self.assertTrue(stream.stopped and stream.closed)
        self.assertTrue(mod.instances[0].terminated)

    def test_named_microphone_with_16k_rate(self) -> None:
        mod = FakeModule()
        mod.stream_script = [320] * 5
        src = self.make_source(mod, "microphone", "Headset Mic")
        src.start()
        src.stop()
        call = mod.open_calls[0]
        self.assertEqual(
            (call["input_device_index"], call["channels"], call["rate"]), (26, 1, 16000)
        )

    def test_duplicate_name_resolves_by_ordinal(self) -> None:
        mod = FakeModule()
        src = self.make_source(mod, "microphone", "USB Interface#2")
        src.start()
        src.stop()
        self.assertEqual(mod.open_calls[0]["input_device_index"], 30)

    def test_unknown_device_or_wrong_kind_raises_from_start(self) -> None:
        for kind, native in (
            ("microphone", "Nope"),
            ("microphone", "Speakers (Realtek) [Loopback]"),
            ("loopback", "Headset Mic"),
        ):
            with self.subTest(kind=kind, native=native):
                mod = FakeModule()
                src = self.make_source(mod, kind, native)
                with (
                    self.assertLogs("kikitori.capture.threaded", "ERROR"),
                    self.assertRaises(DeviceNotFoundError),
                ):
                    src.start()
                self.assertEqual(mod.open_calls, [])
                self.assertTrue(mod.instances[0].terminated)

    def test_no_default_microphone(self) -> None:
        mod = FakeModule()
        mod.default_input = None
        src = self.make_source(mod, "microphone", None)
        with (
            self.assertLogs("kikitori.capture.threaded", "ERROR"),
            self.assertRaises(DeviceNotFoundError),
        ):
            src.start()

    def test_open_failure_mentions_privacy_settings_for_microphone(self) -> None:
        mod = FakeModule()
        mod.open_error = OSError(-9999, "Unanticipated host error")
        src = self.make_source(mod, "microphone", None)
        with (
            self.assertLogs("kikitori.capture.threaded", "ERROR"),
            self.assertRaises(CaptureError) as ctx,
        ):
            src.start()
        self.assertIn("privacy", str(ctx.exception))
        self.assertTrue(mod.instances[0].terminated)

    def test_device_lost_during_capture(self) -> None:
        mod = FakeModule()
        mod.stream_script = [960, OSError(-9999, "Unanticipated host error")]
        src = self.make_source(mod, "loopback", None)
        with self.assertLogs("kikitori.capture.threaded", "ERROR"):
            src.start()
            self.assertTrue(wait_until(lambda: not src._thread_alive()))
        self.assertEqual(len(src.read(timeout=1.0)), 320)
        with self.assertRaises(DeviceLostError):
            src.read(timeout=1.0)
        self.assertTrue(mod.streams[0].closed)
        src.stop()

    def test_lifecycle(self) -> None:
        mod = FakeModule()
        src = self.make_source(mod, "loopback", None)
        with self.assertRaises(CaptureError):
            src.read(timeout=0)
        src.start()
        with self.assertRaises(CaptureError):
            src.start()
        src.stop()
        src.stop()
        self.assertEqual(src.read(timeout=0.1).shape, (0,))


class StreamReaderTest(unittest.TestCase):
    def test_loopback_silence_is_zero_filled_by_elapsed_time(self) -> None:
        clock = FakeClock()
        stream = FakeStream([], channels=2)
        reader = backend._StreamReader(
            stream,
            48000,
            2,
            fill_silence=True,
            clock=clock.monotonic,
            sleep=clock.sleep,
        )
        total = 0
        blocks = 0
        while clock.now < 1.0:
            block = reader.read_block()
            self.assertEqual(block.shape[1], 2)
            self.assertFalse(np.any(block))
            total += len(block)
            blocks += 1
        # 1 秒的靜音應補上約 48000 個 frame（最多差一個補零門檻）
        self.assertGreater(total, 48000 - int(backend.SILENCE_FILL_AFTER * 48000) - 1)
        self.assertLessEqual(total, 48000)

    def test_microphone_is_not_zero_filled(self) -> None:
        clock = FakeClock()
        reader = backend._StreamReader(
            FakeStream([], channels=1),
            16000,
            1,
            fill_silence=False,
            clock=clock.monotonic,
            sleep=clock.sleep,
        )
        while clock.now < 0.5:
            self.assertEqual(len(reader.read_block()), 0)

    def test_data_resets_idle_timer(self) -> None:
        clock = FakeClock()
        stream = FakeStream([0] * 5 + [480], channels=2)
        reader = backend._StreamReader(
            stream,
            48000,
            2,
            fill_silence=True,
            clock=clock.monotonic,
            sleep=clock.sleep,
        )
        for _ in range(5):
            self.assertEqual(len(reader.read_block()), 0)  # 25 ms，未達補零門檻
        block = reader.read_block()
        self.assertEqual(block.shape, (480, 2))
        np.testing.assert_allclose(block, 0.25)
        self.assertIsNone(reader._idle_since)

    def test_read_error_becomes_device_lost(self) -> None:
        stream = FakeStream([OSError(-9988, "Invalid stream pointer")], channels=2)
        reader = backend._StreamReader(stream, 48000, 2, fill_silence=True)
        with self.assertRaises(DeviceLostError):
            reader.read_block()


class StreamFormatTest(unittest.TestCase):
    def test_rejects_bad_device_format(self) -> None:
        for info in (
            {"maxInputChannels": 0, "defaultSampleRate": 48000.0},
            {"maxInputChannels": 2, "defaultSampleRate": 0.0},
        ):
            with self.subTest(info=info), self.assertRaises(CaptureError):
                backend._stream_format(info)

    def test_rounds_fractional_rate(self) -> None:
        self.assertEqual(
            backend._stream_format(
                {"maxInputChannels": 2, "defaultSampleRate": 44100.0001}
            ),
            (44100, 2),
        )


class ErrorMappingTest(unittest.TestCase):
    def test_unknown_error_wrapped_with_cause(self) -> None:
        exc = OSError(-9999, "x")
        mapped = backend.map_error(exc)
        self.assertIs(type(mapped), CaptureError)
        self.assertIs(mapped.__cause__, exc)
        lost = DeviceLostError("y")
        self.assertIs(backend.map_error(lost), lost)


if __name__ == "__main__":
    unittest.main()
