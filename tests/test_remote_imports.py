"""remote 模組可以各自第一個被匯入（不會循環匯入）。

`remote.server` 需要 `engine.events`，載入 engine 套件又會載入用到 `remote.client` 的
`engine.server`；如果 `remote.client` 反過來從 `remote.server` 取常數，「第一個匯入的是
`remote.server`」時就會 ImportError。同一個行程裡別的測試早就載入過 engine，看不出來，所以
每個模組都在全新的直譯器裡匯入。
"""

import subprocess
import sys
import unittest

MODULES = (
    "kikitori.remote.server",
    "kikitori.remote.client",
    "kikitori.remote.pair_net",
    "kikitori.remote.link",
    "kikitori.remote.protocol",
    "kikitori.engine.server",
)


class FreshImportTest(unittest.TestCase):
    def test_each_module_imports_first(self) -> None:
        for module in MODULES:
            with self.subTest(module=module):
                result = subprocess.run(
                    [sys.executable, "-c", f"import {module}"],
                    capture_output=True,
                    text=True,
                    timeout=120,
                    check=False,
                )
                self.assertEqual(result.returncode, 0, result.stderr[-2000:])


if __name__ == "__main__":
    unittest.main()
