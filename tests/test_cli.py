"""`kikitori` 指令的測試：子指令分派與互動選單（以假 input 驅動，不開裝置）。"""

import io
import os
import tempfile
import unittest
from collections.abc import Iterator
from unittest import mock

from kikitori import cli
from kikitori.capture import BackendUnavailableError, DeviceInfo


def scripted(*answers: str) -> "mock.Mock":
    it: Iterator[str] = iter(answers)

    def fake_input(prompt: str) -> str:
        try:
            return next(it)
        except StopIteration:
            raise EOFError from None

    return mock.Mock(side_effect=fake_input)


class MenuTest(unittest.TestCase):
    def setUp(self) -> None:
        self.lines: list[str] = []

    def run_menu(self, *answers: str, engine_code: int = 0) -> int:
        engine = mock.Mock(return_value=engine_code)
        code = cli.menu(scripted(*answers), self.lines.append, engine)
        self.engine = engine
        return code

    def test_quit(self) -> None:
        self.assertEqual(self.run_menu("4"), 0)
        self.engine.assert_not_called()

    def test_eof_quits(self) -> None:
        self.assertEqual(self.run_menu(), 0)

    def test_start_engine_returns_its_exit_code(self) -> None:
        self.assertEqual(self.run_menu("1", engine_code=3), 3)
        self.engine.assert_called_once()

    def test_invalid_choice_then_quit(self) -> None:
        self.assertEqual(self.run_menu("9", "4"), 0)
        self.assertTrue(any("無效" in line for line in self.lines))

    def test_show_settings_path(self) -> None:
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.dict(os.environ, {"KIKITORI_CONFIG_DIR": tmp}),
        ):
            self.run_menu("3", "4")
        self.assertTrue(
            any(tmp in line and "settings.json" in line for line in self.lines)
        )

    def test_list_devices(self) -> None:
        devices = [DeviceInfo("x:loopback:1", "Speakers", "loopback", True)]
        with mock.patch("kikitori.capture.list_devices", return_value=devices):
            self.run_menu("2", "4")
        self.assertTrue(
            any("Speakers" in line and "x:loopback:1" in line for line in self.lines)
        )

    def test_list_devices_error_is_shown(self) -> None:
        with mock.patch(
            "kikitori.capture.list_devices",
            side_effect=BackendUnavailableError("no PulseAudio"),
        ):
            self.assertEqual(self.run_menu("2", "4"), 0)
        self.assertTrue(any("no PulseAudio" in line for line in self.lines))


class MainTest(unittest.TestCase):
    def test_engine_subcommand(self) -> None:
        with mock.patch.object(cli, "_run_engine", return_value=0) as run:
            self.assertEqual(cli.main(["engine"]), 0)
        run.assert_called_once()

    def test_unknown_arguments(self) -> None:
        with mock.patch("sys.stderr", new_callable=io.StringIO) as err:
            self.assertEqual(cli.main(["--help"]), 2)
        self.assertIn("usage", err.getvalue())

    def test_no_arguments_shows_menu(self) -> None:
        with mock.patch.object(cli, "menu", return_value=0) as menu:
            self.assertEqual(cli.main([]), 0)
        menu.assert_called_once()


if __name__ == "__main__":
    unittest.main()
