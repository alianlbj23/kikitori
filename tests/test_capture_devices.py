"""capture 對外入口的測試：平台分派、裝置 id 解析、輸入驗證、套件可在任何平台匯入。

所有後端都以假物件替換，不碰真實裝置。
"""

import subprocess
import sys
import unittest
from typing import Any
from unittest import mock

from kikitori import capture
from kikitori.capture import _screencapturekit_backend as sck
from kikitori.capture import _soundcard_backend as soundcard
from kikitori.capture import _wasapi_backend as wasapi
from kikitori.capture import devices
from kikitori.capture.errors import BackendUnavailableError, DeviceNotFoundError
from kikitori.capture.types import DeviceInfo


class FakeMic:
    def __init__(self, id: str, name: str, isloopback: bool) -> None:
        self.id = id
        self.name = name
        self.isloopback = isloopback
        self.channels = 2


class FakeSoundcard:
    def __init__(self) -> None:
        self.mics = [
            FakeMic("sink.monitor", "Monitor of Sink", True),
            FakeMic("mic", "Built-in Mic", False),
        ]

    def all_microphones(self, include_loopback: bool = False) -> list[FakeMic]:
        return [m for m in self.mics if include_loopback or not m.isloopback]

    def default_speaker(self) -> Any:
        return type("Spk", (), {"id": "sink"})()

    def default_microphone(self) -> Any:
        return type("Mic", (), {"id": "mic"})()


class PackageImportTest(unittest.TestCase):
    def test_import_does_not_load_platform_libraries(self) -> None:
        code = (
            "import sys, kikitori.capture\n"
            "bad = [m for m in ('soundcard', 'pyaudiowpatch', 'objc', 'ScreenCaptureKit')"
            " if m in sys.modules]\n"
            "print(','.join(bad))\n"
        )
        out = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            timeout=60,
            check=True,
        )
        self.assertEqual(out.stdout.strip(), "")

    def test_public_names(self) -> None:
        for name in capture.__all__:
            self.assertTrue(hasattr(capture, name), name)


class ListDevicesTest(unittest.TestCase):
    def test_windows_uses_wasapi(self) -> None:
        expected = [DeviceInfo("wasapi:loopback:x", "x", "loopback", True)]
        with (
            mock.patch.object(wasapi, "load_pyaudio", return_value="pa") as load,
            mock.patch.object(wasapi, "list_devices", return_value=expected) as lst,
        ):
            self.assertEqual(devices.list_devices(platform="win32"), expected)
        load.assert_called_once_with()
        lst.assert_called_once_with("pa")

    def test_linux_uses_soundcard(self) -> None:
        with mock.patch.object(
            soundcard, "load_soundcard", return_value=FakeSoundcard()
        ):
            result = devices.list_devices(platform="linux")
        self.assertEqual(
            [(d.id, d.kind, d.is_default) for d in result],
            [
                ("soundcard:loopback:sink.monitor", "loopback", True),
                ("soundcard:microphone:mic", "microphone", True),
            ],
        )

    def test_macos_adds_screencapturekit_when_supported(self) -> None:
        for supported, expected_first in (
            (True, sck.DEVICE_ID),
            (False, "soundcard:microphone:mic"),
        ):
            with (
                self.subTest(supported=supported),
                mock.patch.object(
                    soundcard, "load_soundcard", return_value=FakeSoundcard()
                ),
                mock.patch.object(sck, "is_supported", return_value=supported),
            ):
                result = devices.list_devices(platform="darwin")
                self.assertEqual(result[0].id, expected_first)
                self.assertNotIn(
                    "soundcard:loopback:sink.monitor", [d.id for d in result]
                )

    def test_unsupported_platform(self) -> None:
        with self.assertRaises(BackendUnavailableError):
            devices.list_devices(platform="freebsd")

    def test_backend_unavailable_propagates(self) -> None:
        with (
            mock.patch.object(
                soundcard,
                "load_soundcard",
                side_effect=BackendUnavailableError("no libpulse"),
            ),
            self.assertRaises(BackendUnavailableError),
        ):
            devices.list_devices(platform="linux")


class OpenSourceTest(unittest.TestCase):
    def test_validation(self) -> None:
        for args in ((None, "speaker"), ("", "loopback"), (123, "loopback")):
            with self.subTest(args=args), self.assertRaises(ValueError):
                devices.open_source(*args, platform="win32")  # type: ignore[arg-type]

    def test_malformed_or_foreign_ids(self) -> None:
        for device_id, plat in (
            ("nonsense", "win32"),
            ("wasapi:speaker:x", "win32"),
            ("wasapi:loopback:", "win32"),
            ("soundcard:loopback:sink.monitor", "win32"),
            ("wasapi:loopback:x", "linux"),
            (sck.DEVICE_ID, "linux"),
            ("unknown:loopback:x", "darwin"),
        ):
            with (
                self.subTest(device_id=device_id, plat=plat),
                self.assertRaises(DeviceNotFoundError),
            ):
                devices.open_source(device_id, "loopback", platform=plat)

    def test_windows_default_and_named(self) -> None:
        src = devices.open_source(None, "microphone", platform="win32")
        self.assertIsInstance(src, wasapi.WasapiSource)
        assert isinstance(src, wasapi.WasapiSource)
        self.assertEqual((src.kind, src.native_id), ("microphone", None))
        src = devices.open_source(
            "wasapi:loopback:Speakers [Loopback]", "loopback", platform="win32"
        )
        assert isinstance(src, wasapi.WasapiSource)
        self.assertEqual((src.kind, src.native_id), ("loopback", "Speakers [Loopback]"))

    def test_device_id_kind_wins_over_requested_kind(self) -> None:
        with self.assertLogs("kikitori.capture.devices", "INFO"):
            src = devices.open_source(
                "wasapi:microphone:Virtual Cable", "loopback", platform="win32"
            )
        assert isinstance(src, wasapi.WasapiSource)
        self.assertEqual(src.kind, "microphone")

    def test_linux_resolves_soundcard_microphone(self) -> None:
        with mock.patch.object(
            soundcard, "load_soundcard", return_value=FakeSoundcard()
        ):
            default = devices.open_source(None, "loopback", platform="linux")
            named = devices.open_source(
                "soundcard:microphone:mic", "microphone", platform="linux"
            )
            with self.assertRaises(DeviceNotFoundError):
                devices.open_source(
                    "soundcard:microphone:gone", "microphone", platform="linux"
                )
        assert isinstance(default, soundcard.SoundcardSource)
        assert isinstance(named, soundcard.SoundcardSource)
        self.assertEqual(default.microphone.id, "sink.monitor")
        self.assertEqual(named.microphone.id, "mic")

    def test_macos_loopback(self) -> None:
        with mock.patch.object(sck, "is_supported", return_value=True):
            self.assertIsInstance(
                devices.open_source(None, "loopback", platform="darwin"),
                sck.ScreenCaptureKitSource,
            )
            self.assertIsInstance(
                devices.open_source(sck.DEVICE_ID, "loopback", platform="darwin"),
                sck.ScreenCaptureKitSource,
            )
        with (
            mock.patch.object(sck, "is_supported", return_value=False),
            self.assertRaises(BackendUnavailableError) as ctx,
        ):
            devices.open_source(None, "loopback", platform="darwin")
        self.assertIn("BlackHole", str(ctx.exception))

    def test_macos_microphone_uses_soundcard(self) -> None:
        with mock.patch.object(
            soundcard, "load_soundcard", return_value=FakeSoundcard()
        ):
            src = devices.open_source(None, "microphone", platform="darwin")
        assert isinstance(src, soundcard.SoundcardSource)
        self.assertEqual(src.microphone.id, "mic")


if __name__ == "__main__":
    unittest.main()
