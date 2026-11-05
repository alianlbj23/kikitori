"""ScreenCaptureKit 後端的測試：純 Python 部分（版本判斷、解碼、錯誤對應、回呼處理）與
以假 API 驗證的生命週期。不需要 macOS；真正的 PyObjC 呼叫要在 Mac 上手動驗證。
"""

import sys
import unittest
from typing import Any

import numpy as np

from kikitori.capture import _screencapturekit_backend as backend
from kikitori.capture.buffer import ChunkBuffer
from kikitori.capture.errors import (
    BackendUnavailableError,
    CaptureError,
    DeviceLostError,
    PermissionDeniedError,
)

AUDIO = 1
SCREEN = 0


class FakeNSError:
    def __init__(self, code: int, description: str) -> None:
        self._code = code
        self._description = description

    def code(self) -> int:
        return self._code

    def localizedDescription(self) -> str:
        return self._description


class FakeApi:
    audio_output_type = AUDIO

    def __init__(
        self,
        *,
        content_error: BaseException | None = None,
        start_error: BaseException | None = None,
    ) -> None:
        self.content_error = content_error
        self.start_error = start_error
        self.handler: Any = None
        self.stopped_with: Any = None
        self.timeouts: list[float] = []

    def shareable_content(self, timeout: float) -> Any:
        self.timeouts.append(timeout)
        if self.content_error is not None:
            raise self.content_error
        return "content"

    def start_stream(self, content: Any, handler: Any, timeout: float) -> Any:
        assert content == "content"
        if self.start_error is not None:
            raise self.start_error
        self.handler = handler
        return "stream"

    def stop_stream(self, stream: Any, timeout: float) -> None:
        self.stopped_with = stream

    def extract_audio(self, sample_buffer: Any) -> tuple[bytes, int]:
        if isinstance(sample_buffer, BaseException):
            raise sample_buffer
        samples = np.asarray(sample_buffer, dtype=np.float32)
        return samples.tobytes(), len(samples)


class SupportTest(unittest.TestCase):
    def test_version_gate(self) -> None:
        self.assertTrue(backend.is_supported("darwin", "13.0"))
        self.assertTrue(backend.is_supported("darwin", "15.4.1"))
        self.assertFalse(backend.is_supported("darwin", "12.7.6"))
        self.assertFalse(backend.is_supported("linux", "14.0"))
        with self.assertLogs("kikitori.capture._screencapturekit_backend", "WARNING"):
            self.assertFalse(backend.is_supported("darwin", ""))

    def test_device_entry(self) -> None:
        d = backend.system_audio_device()
        self.assertEqual(
            (d.id, d.kind, d.is_default), (backend.DEVICE_ID, "loopback", True)
        )

    @unittest.skipIf(sys.platform == "darwin", "PyObjC 在 macOS 上可用")
    def test_real_api_unavailable_off_macos(self) -> None:
        with self.assertRaises(BackendUnavailableError):
            backend._PyObjCApi()


class DecodeTest(unittest.TestCase):
    def test_mono_float32(self) -> None:
        raw = np.array([0.5, -0.25], dtype=np.float32).tobytes()
        out = backend.decode_mono_float32(raw, 2)
        self.assertEqual(out.shape, (2, 1))
        np.testing.assert_array_equal(out[:, 0], [0.5, -0.25])

    def test_length_mismatch_raises(self) -> None:
        raw = np.zeros(4, dtype=np.float32).tobytes()  # 例如意外收到立體聲
        with self.assertRaises(CaptureError):
            backend.decode_mono_float32(raw, 2)


class ErrorMappingTest(unittest.TestCase):
    def test_user_declined_is_permission_denied(self) -> None:
        err = backend.map_ns_error(-3801, "The user declined TCCs")
        self.assertIsInstance(err, PermissionDeniedError)
        self.assertIn("Screen Recording", str(err))

    def test_other_errors(self) -> None:
        self.assertIs(type(backend.map_ns_error(-3802, "failed")), CaptureError)
        self.assertIs(
            type(backend.map_ns_error(-3805, "display gone", while_running=True)),
            DeviceLostError,
        )


class StreamHandlerTest(unittest.TestCase):
    def make(self) -> tuple[backend._StreamHandler, ChunkBuffer]:
        buf = ChunkBuffer(16000)
        api = FakeApi()
        return backend._StreamHandler(buf, AUDIO, api.extract_audio), buf

    def test_audio_is_converted_and_screen_frames_ignored(self) -> None:
        handler, buf = self.make()
        handler.on_sample_buffer(np.full(960, 0.5), SCREEN)
        self.assertEqual(buf.queued_samples, 0)
        handler.on_sample_buffer(np.full(960, 0.5), AUDIO)
        out = buf.get(timeout=0)
        self.assertEqual((out.dtype, out.shape), (np.dtype(np.int16), (320,)))

    def test_callback_errors_are_forwarded_not_raised(self) -> None:
        handler, buf = self.make()
        with self.assertLogs("kikitori.capture._screencapturekit_backend", "ERROR"):
            handler.on_sample_buffer(RuntimeError("bad buffer"), AUDIO)
        with self.assertRaises(CaptureError):
            buf.get(timeout=0)

    def test_stop_with_error(self) -> None:
        for code, expected in (
            (-3801, PermissionDeniedError),
            (-3815, DeviceLostError),
        ):
            with self.subTest(code=code):
                handler, buf = self.make()
                with self.assertLogs(
                    "kikitori.capture._screencapturekit_backend", "ERROR"
                ):
                    handler.on_stopped(FakeNSError(code, "stopped"))
                with self.assertRaises(expected):
                    buf.get(timeout=0)


class ScreenCaptureKitSourceTest(unittest.TestCase):
    def test_lifecycle(self) -> None:
        api = FakeApi()
        src = backend.ScreenCaptureKitSource(api_factory=lambda: api, open_timeout=3.0)
        with self.assertRaises(CaptureError):
            src.read(timeout=0)
        src.start()
        self.assertEqual(api.timeouts, [3.0])
        api.handler.on_sample_buffer(np.zeros(4800), AUDIO)
        self.assertEqual(len(src.read(timeout=1.0)), 1600)
        self.assertEqual(src.read(timeout=0.01).shape, (0,))
        with self.assertRaises(CaptureError):
            src.start()
        src.stop()
        src.stop()
        self.assertEqual(api.stopped_with, "stream")
        self.assertEqual(src.read(timeout=1.0).shape, (0,))

    def test_permission_denied_from_start(self) -> None:
        api = FakeApi(content_error=backend.map_ns_error(-3801, "declined"))
        src = backend.ScreenCaptureKitSource(api_factory=lambda: api)
        with (
            self.assertLogs("kikitori.capture._screencapturekit_backend", "ERROR"),
            self.assertRaises(PermissionDeniedError),
        ):
            src.start()
        src.stop()
        self.assertIsNone(api.stopped_with)

    def test_backend_unavailable_from_factory(self) -> None:
        def factory() -> FakeApi:
            raise BackendUnavailableError("no pyobjc")

        src = backend.ScreenCaptureKitSource(api_factory=factory)
        with (
            self.assertLogs("kikitori.capture._screencapturekit_backend", "ERROR"),
            self.assertRaises(BackendUnavailableError),
        ):
            src.start()

    def test_unexpected_pyobjc_error_becomes_capture_error(self) -> None:
        cause = AttributeError("'SCStream' object has no attribute 'foo'")
        api = FakeApi(start_error=cause)
        src = backend.ScreenCaptureKitSource(api_factory=lambda: api)
        with (
            self.assertLogs("kikitori.capture._screencapturekit_backend", "ERROR"),
            self.assertRaises(CaptureError) as ctx,
        ):
            src.start()
        self.assertIs(ctx.exception.__cause__, cause)
        self.assertEqual(src.read(timeout=1.0).shape, (0,))

    def test_overflow_counts(self) -> None:
        api = FakeApi()
        src = backend.ScreenCaptureKitSource(
            api_factory=lambda: api, max_buffer_seconds=0.1
        )
        src.start()
        with self.assertLogs("kikitori.capture.buffer", "WARNING"):
            for _ in range(10):
                api.handler.on_sample_buffer(np.zeros(4800), AUDIO)  # 共 1 秒
        self.assertEqual(len(src.read(timeout=0)), 1600)
        self.assertEqual(src.dropped_samples, 16000 - 1600)
        src.stop()


if __name__ == "__main__":
    unittest.main()
