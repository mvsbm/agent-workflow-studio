"""core/ runs without ComfyUI: copied on its own (as "Export as Python" copies it) and imported with
ComfyUI off the path, it loads no ComfyUI module and no other part of the pack.

Verifies: R41
"""

import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

CORE = Path(__file__).resolve().parents[2] / "custom_nodes/agent_workflow_studio/core"
CHECK = """
import importlib, pkgutil, sys
import core
for m in pkgutil.iter_modules(core.__path__):
    importlib.import_module("core." + m.name)
bad = [m for m in sys.modules if m.split(".")[0] in ("comfy", "comfy_api", "comfy_execution", "folder_paths", "custom_nodes", "nodes")]
assert not bad, bad
print("ok", len([m for m in sys.modules if m.startswith("core.")]))
"""


class CoreIsolationTests(unittest.TestCase):
    def test_core_imports_alone(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            shutil.copytree(CORE, Path(tmp) / "core", ignore=shutil.ignore_patterns("__pycache__"))
            # -I: no PYTHONPATH, no current directory, no user site; only the copy and installed libraries.
            done = subprocess.run(
                [sys.executable, "-I", "-c", f"import sys; sys.path.insert(0, {tmp!r})\n{CHECK}"],
                capture_output=True,
                text=True,
                timeout=120,
            )
            self.assertEqual(done.returncode, 0, done.stderr)
            self.assertTrue(done.stdout.startswith("ok"))


if __name__ == "__main__":
    unittest.main()
