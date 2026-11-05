"""`packaging/engine/licenses.py` 的測試：以暫存目錄中的假 dist-info 驗證「來源檔 → 套件 → 授權檔」。"""

import importlib.util
import json
import sys
import tempfile
import unittest
from importlib import metadata
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parents[1]


def _load() -> ModuleType:
    name = "kikitori_build_licenses"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(
        name, ROOT / "packaging" / "engine" / "licenses.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


lic = _load()


class FakeSite:
    """暫存的 site-packages：可建立帶 RECORD 的假套件。"""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.dists: list[metadata.Distribution] = []

    def add(
        self,
        name: str,
        version: str,
        files: dict[str, str],
        *,
        license_expression: str | None = None,
        classifier: str | None = None,
    ) -> metadata.Distribution:
        info = f"{name.replace('-', '_')}-{version}.dist-info"
        meta = ["Metadata-Version: 2.4", f"Name: {name}", f"Version: {version}"]
        if license_expression:
            meta.append(f"License-Expression: {license_expression}")
        if classifier:
            meta.append(f"Classifier: License :: OSI Approved :: {classifier}")
        all_files = dict(files)
        all_files[f"{info}/METADATA"] = "\n".join(meta) + "\n"
        record = [f"{path},," for path in all_files] + [f"{info}/RECORD,,"]
        all_files[f"{info}/RECORD"] = "\n".join(record) + "\n"
        for relative, text in all_files.items():
            path = self.root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        dist = metadata.PathDistribution(self.root / info)
        self.dists.append(dist)
        return dist


class LicenseCollectionTest(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        base = Path(tmp.name)
        self.site = FakeSite(base / "site")
        self.python_root = base / "python"
        (self.python_root / "Lib").mkdir(parents=True)
        self.stdlib_module = self.python_root / "Lib" / "json.py"
        self.stdlib_module.write_text("", encoding="utf-8")
        self.python_license = self.python_root / "LICENSE.txt"
        self.python_license.write_text("PSF", encoding="utf-8")
        self.project = base / "project"
        (self.project / "src" / "kikitori").mkdir(parents=True)
        (self.project / "src" / "kikitori" / "cli.py").write_text("", encoding="utf-8")
        (self.project / "LICENSE").write_text("MIT kikitori", encoding="utf-8")
        models = self.project / "src" / "kikitori" / "vad" / "models"
        models.mkdir(parents=True)
        (models / "LICENSE-silero-vad").write_text("MIT silero", encoding="utf-8")
        self.overrides = base / "overrides"
        self.out = base / "out"
        self.work = base / "work"
        self.work.mkdir()

    def collect(self, sources: list[Path]) -> object:
        return lic.collect(
            [str(s) for s in sources],
            project_root=self.project,
            overrides_dir=self.overrides,
            distributions=self.site.dists,
            python_roots=[self.python_root],
            python_license=self.python_license,
            python_version="3.12.99",
            generated_roots=[self.work],
        )

    def test_maps_bundled_files_to_packages_and_copies_licenses(self) -> None:
        self.site.add(
            "numpy",
            "2.0.0",
            {
                "numpy/__init__.py": "",
                "numpy/core.pyd": "",
                "numpy-2.0.0.dist-info/licenses/LICENSE.txt": "BSD numpy",
                "numpy-2.0.0.dist-info/licenses/numpy/random/LICENSE.md": "random",
            },
            license_expression="BSD-3-Clause",
        )
        self.site.add(
            "onnxruntime",
            "1.0.0",
            {
                "onnxruntime/__init__.py": "",
                "onnxruntime/LICENSE": "MIT ort",
                "onnxruntime/ThirdPartyNotices.txt": "tp",
            },
            classifier="MIT License",
        )
        self.site.add(
            "unused", "1.0.0", {"unused/__init__.py": "", "unused/LICENSE": "x"}
        )
        base_zip = self.work / "base_library.zip"
        base_zip.write_text("", encoding="utf-8")
        report = self.collect(
            [
                self.site.root / "numpy" / "core.pyd",
                self.site.root / "onnxruntime" / "__init__.py",
                self.project / "src" / "kikitori" / "cli.py",
                self.stdlib_module,
                base_zip,
            ]
        )
        keys = [c.key for c in report.components]
        self.assertEqual(keys, ["kikitori", "python", "numpy", "onnxruntime"])
        self.assertEqual(report.unmapped, [])
        self.assertEqual(report.missing, [])
        numpy = report.components[2]
        self.assertEqual(numpy.license, "BSD-3-Clause")
        self.assertEqual(
            sorted(numpy.files),
            ["licenses/LICENSE.txt", "licenses/numpy/random/LICENSE.md"],
        )
        self.assertEqual(report.components[3].license, "MIT License")
        self.assertEqual(
            sorted(report.components[3].files),
            ["onnxruntime/LICENSE", "onnxruntime/ThirdPartyNotices.txt"],
        )

        lic.write(report, self.out)
        self.assertEqual(
            (
                self.out
                / "kikitori-unknown"
                / "src"
                / "kikitori"
                / "vad"
                / "models"
                / "LICENSE-silero-vad"
            ).read_text(encoding="utf-8"),
            "MIT silero",
        )
        # 假的 site-packages 沒有 kikitori 的 dist-info，所以版本是 unknown。
        self.assertEqual(
            (self.out / "kikitori-unknown" / "LICENSE").read_text(encoding="utf-8"),
            "MIT kikitori",
        )
        self.assertEqual(
            (self.out / "python-3.12.99" / "LICENSE.txt").read_text(encoding="utf-8"),
            "PSF",
        )
        self.assertEqual(
            (
                self.out
                / "numpy-2.0.0"
                / "licenses"
                / "numpy"
                / "random"
                / "LICENSE.md"
            ).read_text(encoding="utf-8"),
            "random",
        )
        manifest = json.loads((self.out / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual([c["key"] for c in manifest["components"]], keys)
        self.assertIn(
            "numpy 2.0.0  [BSD-3-Clause]",
            (self.out / "README.txt").read_text(encoding="utf-8"),
        )

    def test_package_without_license_uses_override_or_is_missing(self) -> None:
        self.site.add("ctranslate2", "4.8.5", {"ctranslate2/__init__.py": ""})
        source = self.site.root / "ctranslate2" / "__init__.py"
        report = self.collect([source])
        self.assertEqual(report.missing, ["ctranslate2"])

        override = self.overrides / "ctranslate2" / "third_party" / "ruy" / "LICENSE"
        override.parent.mkdir(parents=True)
        override.write_text("Apache", encoding="utf-8")
        report = self.collect([source])
        self.assertEqual(report.missing, [])
        ct2 = next(c for c in report.components if c.key == "ctranslate2")
        self.assertEqual(list(ct2.files), ["upstream/third_party/ruy/LICENSE"])

    def test_pyinstaller_is_always_listed_with_bootloader_exception(self) -> None:
        self.site.add(
            "pyinstaller",
            "6.22.3",
            {
                "PyInstaller/__init__.py": "",
                "pyinstaller-6.22.3.dist-info/licenses/COPYING.txt": "GPL + exception",
            },
            classifier="GNU General Public License v2 (GPLv2)",
        )
        report = self.collect([])
        pyi = next(c for c in report.components if c.key == "pyinstaller")
        self.assertIn("Bootloader-exception", pyi.license)
        self.assertEqual(list(pyi.files), ["licenses/COPYING.txt"])

    def test_unmapped_system_libraries_are_reported_with_known_notes(self) -> None:
        system = self.work.parent / "system"
        system.mkdir()
        vcruntime = system / "vcruntime140.dll"
        mystery = system / "libmystery.so.1"
        for path in (vcruntime, mystery):
            path.write_text("", encoding="utf-8")
        report = self.collect([vcruntime, mystery, system / "does-not-exist.dll"])
        self.assertEqual(
            [Path(p).name for p in report.unmapped],
            ["libmystery.so.1", "vcruntime140.dll"],
        )
        lic.write(report, self.out)
        readme = (self.out / "README.txt").read_text(encoding="utf-8")
        self.assertIn("vcruntime140.dll  [Microsoft Visual C++ Redistributable", readme)
        self.assertIn("libmystery.so.1  [UNKNOWN - review before release]", readme)
        manifest = json.loads((self.out / "manifest.json").read_text(encoding="utf-8"))
        self.assertIn(
            {"file": "libmystery.so.1", "license": None}, manifest["unmapped"]
        )

    def test_write_replaces_previous_output(self) -> None:
        stale = self.out / "stale.txt"
        stale.parent.mkdir(parents=True)
        stale.write_text("old", encoding="utf-8")
        lic.write(self.collect([]), self.out)
        self.assertFalse(stale.exists())
        self.assertTrue((self.out / "manifest.json").is_file())

    def test_system_library_notes(self) -> None:
        self.assertIn("GCC", lic.system_library_note("/usr/lib/libstdc++.so.6"))
        self.assertIn(
            "Visual C++", lic.system_library_note("C:/Windows/System32/MSVCP140.dll")
        )
        self.assertIsNone(lic.system_library_note("libfoo.so"))

    def test_normalize_name(self) -> None:
        self.assertEqual(lic.normalize_name("PyAudio_WPatch.x"), "pyaudio-wpatch-x")


class RealEnvironmentTest(unittest.TestCase):
    def test_python_license_file_exists_for_this_interpreter(self) -> None:
        path = lic.python_license_file()
        self.assertIsNotNone(path)
        assert path is not None
        self.assertIn(
            "PYTHON SOFTWARE FOUNDATION",
            path.read_text(encoding="utf-8", errors="replace").upper(),
        )

    def test_ctranslate2_override_covers_its_license(self) -> None:
        overrides = ROOT / "packaging" / "engine" / "license_overrides" / "ctranslate2"
        self.assertTrue((overrides / "LICENSE").is_file())
        self.assertTrue((overrides / "SOURCE.md").is_file())


if __name__ == "__main__":
    unittest.main()
