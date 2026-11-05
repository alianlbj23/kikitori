"""VAD 模組的端到端檢查：真模型 + Segmenter，以及正式程式碼的依賴邊界。"""

import ast
import subprocess
import sys
import unittest
from pathlib import Path

import numpy as np

from kikitori.evaluation import noise
from kikitori.vad import ResetPolicy, Segmenter, SileroVad
from kikitori.vad.model import SAMPLE_RATE

SRC_DIR = Path(__file__).resolve().parent.parent / "src" / "kikitori"
FORBIDDEN = {"torch", "pipecat"}


def noise_track(kind: str, level: float, seed: int) -> np.ndarray:
    """靜音與噪音交錯的 30 秒音軌，讓 (c) 的長時間非人聲重置也會發生。"""
    gen = noise.white_noise if kind == "white" else noise.pink_noise
    silence = np.zeros(3 * SAMPLE_RATE, dtype=np.int16)
    parts = [
        silence,
        gen(12 * SAMPLE_RATE, level_dbfs=level, seed=seed),
        silence,
        gen(12 * SAMPLE_RATE, level_dbfs=level, seed=seed + 1),
    ]
    return np.concatenate(parts)


class NoiseTrackTest(unittest.TestCase):
    """白噪音、粉紅噪音的純噪音音軌在三種重置策略下都產生 0 句。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.vad = SileroVad()

    def test_noise_tracks_produce_no_utterance(self) -> None:
        for kind in ("white", "pink"):
            for level in (-30.0, -15.0):
                track = noise_track(kind, level, seed=11)
                for policy in ResetPolicy:
                    with self.subTest(kind=kind, level=level, policy=policy.name):
                        seg = Segmenter(self.vad, reset_policy=policy)
                        events = []
                        for i in range(0, len(track), 1600):
                            events += seg.process(track[i : i + 1600])
                        events += seg.flush()
                        self.assertEqual(events, [])


def imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names |= {alias.name.split(".")[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.add(node.module.split(".")[0])
    return names


class DependencyBoundaryTest(unittest.TestCase):
    def test_source_does_not_import_torch_or_pipecat(self) -> None:
        files = sorted(SRC_DIR.rglob("*.py"))
        self.assertGreater(len(files), 5)
        for path in files:
            with self.subTest(path=path.relative_to(SRC_DIR).as_posix()):
                self.assertFalse(imported_modules(path) & FORBIDDEN)

    def test_importing_vad_does_not_load_torch_or_pipecat(self) -> None:
        # 另開行程檢查實際載入的模組，涵蓋間接匯入（例如依賴套件再匯入 torch）。
        # 只匯入 VAD 與評測子套件：其他模組的執行期依賴（例如音訊裝置）由各自的測試負責，
        # 原始碼層級的檢查已由上一個測試涵蓋整個 src/kikitori。
        code = (
            "import importlib, pkgutil, sys\n"
            "import kikitori.evaluation, kikitori.vad\n"
            "for pkg in (kikitori.vad, kikitori.evaluation):\n"
            "    for m in pkgutil.walk_packages(pkg.__path__, pkg.__name__ + '.'):\n"
            "        importlib.import_module(m.name)\n"
            "kikitori.vad.SileroVad()\n"
            f"print(sorted(m for m in sys.modules if m.split('.')[0] in {FORBIDDEN!r}))"
        )
        result = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "[]")


if __name__ == "__main__":
    unittest.main()
