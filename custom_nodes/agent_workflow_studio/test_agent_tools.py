"""Agent tools: confined execution, failures, approval text."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from .agent_tools import approval_text, run_tool, sandboxed

UPPER = """
import os, json
def run(inputs):
    text = inputs["text"].upper()
    path = os.path.join(os.environ["TOOL_RUN_DIR"], "result.txt")
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return {"text": text, "files": ["result.txt"]}
"""
PROBE = """
import os, socket
def run(inputs):
    seen = {}
    for name, act in {
        "network": lambda: socket.create_connection(("1.1.1.1", 80), timeout=3),
        "outside": lambda: open("/private/tmp/aws-tool-escape.txt", "w").write("x"),
        "home": lambda: open(os.path.expanduser("~/.zshrc")).read(1),
    }.items():
        try:
            act()
            seen[name] = "allowed"
        except Exception:
            seen[name] = "blocked"
    return seen
"""


class AgentToolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.run_dir = Path(self.temp.name) / "run"

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_runs_and_writes_its_own_folder(self) -> None:
        self.assertEqual(
            run_tool(UPPER, {"text": "名寄せ ok"}, self.run_dir),
            {"text": "名寄せ OK", "files": ["result.txt"]},
        )
        self.assertEqual((self.run_dir / "result.txt").read_text(encoding="utf-8"), "名寄せ OK")

    @unittest.skipUnless(sandboxed(), "macOS sandbox only")
    def test_sandbox_blocks_network_outside_writes_and_home(self) -> None:
        self.assertEqual(
            run_tool(PROBE, {}, self.run_dir),
            {"network": "blocked", "outside": "blocked", "home": "blocked"},
        )
        self.assertFalse(Path("/private/tmp/aws-tool-escape.txt").exists())

    @unittest.skipUnless(sandboxed(), "macOS sandbox only")
    def test_reads_but_cannot_change_the_input_folder(self) -> None:
        data = Path(self.temp.name) / "data"
        data.mkdir()
        (data / "a.csv").write_text("x,y\n")
        code = (
            "import os\n"
            "def run(i):\n"
            "    d = os.environ['TOOL_INPUT_DIR']\n"
            "    text = open(os.path.join(d, 'a.csv')).read()\n"
            "    try:\n"
            "        open(os.path.join(d, 'b.csv'), 'w').write('no')\n"
            "        wrote = True\n"
            "    except OSError:\n"
            "        wrote = False\n"
            "    return {'text': text, 'wrote': wrote}\n"
        )
        self.assertEqual(
            run_tool(code, {}, self.run_dir, read_dir=data), {"text": "x,y\n", "wrote": False}
        )
        self.assertEqual(run_tool(PROBE, {}, self.run_dir, read_dir=data)["home"], "blocked")

    def test_failures_are_reported(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "time limit"):
            run_tool("def run(i):\n    while True: pass\n", {}, self.run_dir, timeout=2)
        with self.assertRaisesRegex(RuntimeError, "ZeroDivisionError"):
            run_tool("def run(i):\n    return {'x': 1 / 0}\n", {}, self.run_dir)
        with self.assertRaisesRegex(RuntimeError, "must return a dict"):
            run_tool("def run(i):\n    return 3\n", {}, self.run_dir)

    def test_approval_shows_the_exact_code(self) -> None:
        text = approval_text(
            "upper",
            "Uppercase text",
            [{"name": "text", "kind": "text"}],
            [{"name": "text", "kind": "text"}],
            UPPER,
        )
        self.assertIn("TOOL: upper", text)
        self.assertIn("TAKES: text (text)", text)
        self.assertTrue(text.rstrip().endswith('return {"text": text, "files": ["result.txt"]}'))


class ApprovedToolTests(unittest.TestCase):
    WORKFLOW = {"id": "5d9a308c-81ae-4aab-a0bd-5145aa83e56e", "nodes": []}

    def setUp(self) -> None:
        from types import SimpleNamespace
        from unittest.mock import patch

        from . import agent_tool_node
        from .agent_tool_node import AgentToolNode
        from .agent_tools import code_hash
        from .project_repo import ProjectRepo

        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.repo = ProjectRepo(root / "projects", self.WORKFLOW["id"])
        self.repo.save_tool(
            "upper",
            UPPER,
            {
                "name": "upper",
                "description": "d",
                "inputs": [],
                "outputs": [],
                "sha256": code_hash(UPPER),
            },
        )
        self.node = AgentToolNode
        self.patches = [
            patch.object(agent_tool_node, "projects_root", return_value=root / "projects"),
            patch.object(agent_tool_node, "report_node_progress"),
            patch.object(
                AgentToolNode,
                "hidden",
                SimpleNamespace(unique_id="9", extra_pnginfo={"workflow": self.WORKFLOW}),
                create=True,
            ),
            patch("folder_paths.get_output_directory", return_value=str(root / "output")),
            patch("folder_paths.get_input_directory", return_value=str(root / "input")),
        ]
        for item in self.patches:
            item.start()

    def tearDown(self) -> None:
        for item in reversed(self.patches):
            item.stop()
        self.temp.cleanup()

    def test_node_runs_the_approved_tool_and_records_it(self) -> None:
        output = self.node.execute("upper", "nayose")
        self.assertEqual(output.args[0]["text"], "NAYOSE")
        self.assertEqual(len(output.args[0]["_run_folders"]), 1)
        self.assertEqual(output.args[1], "NAYOSE")
        self.assertIn("Files in upper-", output.ui["text"][0])
        log = self.repo.log()
        self.assertEqual(log[0]["message"], "Run · Agent tool upper #9 · succeeded")
        self.assertEqual(self.repo.record(log[0]["sha"])["files"], ["result.txt"])

    def test_input_folder_must_be_inside_the_input_directory(self) -> None:
        root = Path(self.temp.name)
        (root / "input" / "nayose").mkdir(parents=True)
        self.assertEqual(self.node.execute("upper", "x", input_folder="nayose").args[1], "X")
        self.assertEqual(self.repo.record(self.repo.log()[0]["sha"])["input_folder"], "nayose")
        for bad in ("../projects", "/etc", "missing"):
            with self.assertRaisesRegex(ValueError, "inside ComfyUI's input directory"):
                self.node.execute("upper", "x", input_folder=bad)

    def test_tools_chain_through_upstream_run_folders(self) -> None:
        from .agent_tools import code_hash

        reader = (
            "import os\n"
            "def run(i):\n"
            "    path = os.path.join(i['_run_folders'][-1], 'result.txt')\n"
            "    text = open(path, encoding='utf-8').read() + '!'\n"
            "    with open(os.path.join(os.environ['TOOL_RUN_DIR'], 'out.csv'), 'w') as f:\n"
            "        f.write('word,length\\n' + text + ',' + str(len(text)) + '\\n')\n"
            "    return {'text': text}\n"
        )
        self.repo.save_tool(
            "reader",
            reader,
            {
                "name": "reader",
                "description": "d",
                "inputs": [],
                "outputs": [],
                "sha256": code_hash(reader),
            },
        )
        first = self.node.execute("upper", "chain").args[0]
        second = self.node.execute("reader", data=first)
        self.assertEqual(second.args[1], "CHAIN!")
        # The node shows what the step did: the operation, then each file in and out.
        # The node shows what the step did: its own explanation first (missing here, so a warning),
        # then the files in and out, one sample, and the run details last.
        warning, flow, sample, details = second.ui["aws_tables"]
        self.assertTrue(warning["title"].startswith("⚠ This tool does not say what it did"))
        self.assertEqual(flow["rows"], [["out", "out.csv", "1", "2"]])
        self.assertEqual(sample["rows"], [["CHAIN!", "6"]])
        self.assertIn(["tool", "reader"], details["rows"])
        with self.assertRaisesRegex(ValueError, "own tool runs"):
            self.node.execute("reader", data={"_run_folders": [str(Path(self.temp.name))]})

    def test_a_tool_that_explains_itself_shows_it_first(self) -> None:
        from .agent_tools import code_hash

        code = (
            "def run(i):\n"
            "    return {'text': 'ok', 'explain': {\n"
            "        'steps': [{'what': 'Converted half-width kana to full-width', 'count': 5396}],\n"
            "        'examples': [{'before': {'kana': 'ｽｽﾞｷ'}, 'after': {'kana': 'スズキ'}, 'why': 'width'}]}}\n"
        )
        self.repo.save_tool(
            "explain",
            code,
            {
                "name": "explain",
                "description": "d",
                "inputs": [],
                "outputs": [],
                "sha256": code_hash(code),
            },
        )
        what, examples = self.node.execute("explain").ui["aws_tables"][:2]
        self.assertEqual(what["title"], "What this step did")
        self.assertEqual(what["rows"], [["1", "Converted half-width kana to full-width", "5,396"]])
        self.assertEqual(examples["rows"], [["ｽｽﾞｷ", "スズキ", "width"]])

    def test_refuses_unknown_or_changed_tools(self) -> None:
        with self.assertRaisesRegex(ValueError, "No approved tool"):
            self.node.execute("missing")
        (self.repo.tree / "tools" / "upper.py").write_text(UPPER + "\nimport os\n")
        with self.assertRaisesRegex(ValueError, "changed since it was approved"):
            self.node.execute("upper", "x")


if __name__ == "__main__":
    unittest.main()
