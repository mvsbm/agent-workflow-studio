"""Agent tools: confined execution, failures, approval text."""

# Verifies: R4, R23
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from custom_nodes.agent_workflow_studio.agent_tools import approval_text, run_tool, sandboxed

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


ECHO = "def run(inputs):\n    return dict(inputs)\n"


class TypedToolTests(unittest.TestCase):
    """A tool's interface is the studio's own: typed ports, checked, with examples as its tests."""

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.run_dir = Path(self.temp.name) / "run"

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_studio_values_cross_the_sandbox_unchanged(self) -> None:
        import math

        table = {
            "name": "policies",
            "columns": ["id", "name", "birth", "premium", "note"],
            "rows": [
                {
                    "id": "P-001",
                    "name": "鈴木 恵子",
                    "birth": "S45.03.12",
                    "premium": 12000.5,
                    "note": None,
                },
                {"id": "P-002", "name": "ｽｽﾞｷ ｹｲｺ", "birth": "", "premium": 0, "note": "a\tb\n"},
            ],
        }
        pairs = {"counts": {"same": 3}, "_run_folders": ["/tmp/x"]}
        sent = {"table": table, "pairs": pairs, "n": 7, "on": True, "ratio": 0.25}
        self.assertEqual(run_tool(ECHO, sent, self.run_dir), sent)
        # Not-a-number survives as NaN (JSON's NaN extension on both sides).
        self.assertTrue(math.isnan(run_tool(ECHO, {"x": float("nan")}, self.run_dir)["x"]))

    def test_ports_use_the_studio_types(self) -> None:
        from custom_nodes.agent_workflow_studio.agent_tools import (
            port_problems,
            typed_ports,
            value_problems,
        )

        self.assertEqual(port_problems("inputs", [{"name": "rows", "kind": "TABLE"}]), [])
        self.assertEqual(
            port_problems(
                "outputs", [{"name": "shade", "kind": "colour"}, {"name": "Bad", "kind": "INT"}]
            ),
            [
                "outputs: shade has kind 'colour'; use a registered type: STRING, INT, FLOAT, BOOLEAN, DICT, TABLE, RECORD_PAIRS, PAIR_DECISIONS, RECORD_CLUSTERS.",
                "outputs: 'Bad' is not a short lowercase name.",
            ],
        )
        # Aliases and class names resolve to the registered socket, which the manifest keeps.
        ports = [
            {"name": "n", "kind": "number"},
            {"name": "pairs", "kind": "PAIRS"},
            {"name": "c", "kind": "record_clusters"},
        ]
        self.assertEqual(
            [p["kind"] for p in typed_ports(ports)], ["FLOAT", "RECORD_PAIRS", "RECORD_CLUSTERS"]
        )
        # A dataset passed in memory cannot cross into a tool.
        self.assertIn(
            "has kind 'synthetic_people'",
            port_problems("inputs", [{"name": "p", "kind": "synthetic_people"}])[0],
        )
        ports = [{"name": "table", "kind": "TABLE"}, {"name": "count", "kind": "INT"}]
        self.assertEqual(
            value_problems("output", {"table": {"rows": []}, "count": True}, ports),
            ["output table should be TABLE, got dict", "output count should be INT, got bool"],
        )

    def test_examples_are_the_tools_tests(self) -> None:
        from custom_nodes.agent_workflow_studio.agent_tools import example_problems, run_examples

        manifest = {
            "outputs": [{"name": "text", "kind": "STRING"}],
            "examples": [
                {"inputs": {"text": "名寄せ ok"}, "expect": {"text": "名寄せ OK"}},
                {"inputs": {"text": "a"}, "expect": {"text": "b"}},
            ],
        }
        first, second = run_examples(UPPER, manifest, self.run_dir)
        self.assertEqual(first, {"example": 1, "passed": True, "problems": []})
        self.assertEqual(second["problems"], ['text: expected "b", got "A"'])
        self.assertEqual(
            example_problems([]),
            ["Give examples: [{inputs, expect}], at least one; they are the tool's tests."],
        )
        self.assertEqual(len(example_problems([{"inputs": {}, "expect": {}}])), 1)


class ApprovedToolTests(unittest.TestCase):
    WORKFLOW = {"id": "5d9a308c-81ae-4aab-a0bd-5145aa83e56e", "nodes": []}

    def setUp(self) -> None:
        from types import SimpleNamespace
        from unittest.mock import patch

        from custom_nodes.agent_workflow_studio import agent_tool_node
        from custom_nodes.agent_workflow_studio.agent_tool_node import AgentToolNode
        from custom_nodes.agent_workflow_studio.agent_tools import code_hash
        from custom_nodes.agent_workflow_studio.project_repo import ProjectRepo

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
        from custom_nodes.agent_workflow_studio.agent_tools import code_hash

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
        from custom_nodes.agent_workflow_studio.agent_tools import code_hash

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

    def test_a_typed_tool_keeps_to_its_declared_ports(self) -> None:
        from custom_nodes.agent_workflow_studio.agent_tools import code_hash

        code = "def run(i):\n    return {'count': str(len(i['text']))}\n"
        self.repo.save_tool(
            "count",
            code,
            {
                "version": 2,
                "tier": "workflow",
                "name": "count",
                "description": "d",
                "inputs": [{"name": "text", "kind": "STRING"}],
                "outputs": [{"name": "count", "kind": "INT"}],
                "examples": [],
                "sha256": code_hash(code),
            },
        )
        with self.assertRaisesRegex(ValueError, r"input text \(STRING\) is missing"):
            self.node.execute("count")
        with self.assertRaisesRegex(
            RuntimeError, "broke its declared ports: output count should be INT, got str"
        ):
            self.node.execute("count", "abc")
        self.assertEqual(self.repo.log()[0]["message"], "Run · Agent tool count #9 · failed")

    def test_run_checks_tests_each_tool_the_workflow_runs(self) -> None:
        from custom_nodes.agent_workflow_studio.agent_tools import code_hash, test_tools, tools_used

        workflow = {
            "nodes": [{"id": 1, "type": "AgentWorkflowAgentTool", "widgets_values": ["upper", ""]}],
            "definitions": {
                "subgraphs": [
                    {"nodes": [{"type": "AgentWorkflowAgentTool", "widgets_values": ["typed"]}]}
                ]
            },
        }
        self.assertEqual(tools_used(workflow), ["upper", "typed"])
        manifest = {
            "version": 2,
            "name": "typed",
            "description": "d",
            "inputs": [{"name": "text", "kind": "STRING"}],
            "outputs": [{"name": "text", "kind": "STRING"}],
            "examples": [{"inputs": {"text": "a"}, "expect": {"text": "A"}}],
            "sha256": code_hash(UPPER),
        }
        self.repo.save_tool("typed", UPPER, manifest)
        old, typed, missing = test_tools(
            self.repo, ["upper", "typed", "gone"], Path(self.temp.name) / "t"
        )
        self.assertEqual(
            old["problems"], ["has no examples (its tests): propose it again with examples"]
        )
        self.assertTrue(typed["passed"])
        self.assertIn("No approved tool", missing["problems"][0])

    def test_a_tool_takes_and_gives_datasets_by_reference(self) -> None:
        from custom_nodes.agent_workflow_studio.agent_tools import code_hash
        from custom_nodes.agent_workflow_studio.datasets import ref

        upstream = (
            Path(self.temp.name) / "output" / "agent_tools" / self.WORKFLOW["id"] / "compare-1"
        )
        upstream.mkdir(parents=True)
        (upstream / "pairs.jsonl").write_text('{"id": "p1"}\n{"id": "p2"}\n', encoding="utf-8")
        given = ref(
            "record_pairs", "pairs", upstream / "pairs.jsonl", 2, run_folders=[str(upstream)]
        )
        code = (
            "import os, json\n"
            "def run(i):\n"
            "    rows = [json.loads(l) for l in open(i['pairs']['uri'], encoding='utf-8') if l.strip()]\n"
            "    out = os.path.join(os.environ['TOOL_RUN_DIR'], 'kept.jsonl')\n"
            "    open(out, 'w').write(json.dumps(rows[0]) + '\\n')\n"
            "    return {'kept': {'kind': 'studio#dataset', 'class': 'record_pairs', 'name': 'kept',\n"
            "                     'uri': out, 'format': 'jsonl', 'row_count': 1}}\n"
        )
        self.repo.save_tool(
            "keep_first",
            code,
            {
                "version": 2,
                "name": "keep_first",
                "description": "d",
                "inputs": [{"name": "pairs", "kind": "RECORD_PAIRS"}],
                "outputs": [{"name": "kept", "kind": "RECORD_PAIRS"}],
                "examples": [],
                "sha256": code_hash(code),
            },
        )
        result = self.node.execute("keep_first", data=given).args[0]
        self.assertEqual(
            (result["kept"]["class"], result["kept"]["row_count"]), ("record_pairs", 1)
        )
        # A plain dict where a dataset is declared breaks the tool's ports.
        with self.assertRaisesRegex(RuntimeError, "kept should be RECORD_PAIRS"):
            bad = code.replace("{'kind': 'studio#dataset',", "{'not': 'a reference',")
            self.repo.save_tool(
                "keep_bad",
                bad,
                {
                    **self.repo.load_tool("keep_first")[1],
                    "name": "keep_bad",
                    "sha256": code_hash(bad),
                },
            )
            self.node.execute("keep_bad", data=given)

    def test_refuses_unknown_or_changed_tools(self) -> None:
        with self.assertRaisesRegex(ValueError, "No approved tool"):
            self.node.execute("missing")
        (self.repo.tree / "tools" / "upper.py").write_text(UPPER + "\nimport os\n")
        with self.assertRaisesRegex(ValueError, "changed since it was approved"):
            self.node.execute("upper", "x")


if __name__ == "__main__":
    unittest.main()
