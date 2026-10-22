"""Export as Python: a published release becomes a repo whose command line runs the same steps as
the studio, without ComfyUI; its examples and stage Checks pass; a node with no Python export
blocks it with its name.

Verifies: R41
"""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from custom_nodes.agent_workflow_studio.core.contacts import COLUMNS
from custom_nodes.agent_workflow_studio.export_python import generate, readiness

SAMPLE = {
    "sample/contacts.csv": "CONT_ID,LAST_NAME_KANJI,FIRST_NAME_KANJI,LAST_NAME_KANA,FIRST_NAME_KANA,BIRTH_DATE,GENDER,CONT_TYPE\n"
    "EX01,山田,太郎,ヤマダ,タロウ,1980-01-02,M,1\nEX02,山田,太郎,ﾔﾏﾀﾞ,ﾀﾛｳ,1980-01-02,M,1\n"
    "EX03,佐藤,次郎,サトウ,ジロウ,1990-03-03,M,1\nEX04,佐藤,花子,サトウ,ハナコ,1992-07-07,F,1\n",
    "sample/phones.csv": "CONT_ID,PHONE\nEX01,03-1234-5678\nEX02,0312345678\nEX03,045-111-2222\nEX04,045-111-2222\n",
    "sample/addresses.csv": "CONT_ID,ADDRESS_KANJI\nEX03,神奈川県横浜市港北区日吉1-2-3\nEX04,神奈川県横浜市港北区日吉1-2-3\n",
}
COLUMNS_NO_ROMAJI = COLUMNS.replace(", romaji: LAST_NAME_ROMAJI", "").replace(
    ", romaji: FIRST_NAME_ROMAJI", ""
)


def node(class_type, title, /, **inputs):
    return {"class_type": class_type, "inputs": inputs, "_meta": {"title": title}}


# What the browser publishes (graphToPrompt): stage nodes flattened to "stage:inner" ids.
PROMPT = {
    "1": node("AgentWorkflowDataFolder", "Input · exports", folder="sample"),
    "2:1": node("AgentWorkflowReadContacts", "Read contacts", folder=["1", 0], columns=COLUMNS_NO_ROMAJI),
    "2:2": node("AgentWorkflowCheck", "Check · contacts", value=["2:1", 0], kind="rows_at_least", expect="4", says="All contacts are read."),
    "3:1": node("AgentWorkflowStandardizeContacts", "Standardize", contacts=["2:1", 0], towns="", kana_ocr=True, readings=True, split_one_field=True, shop_alias=True, company_clean=True),
    "4:1": node("AgentWorkflowFindContactCandidates", "Find", records=["3:1", 0], exact=True, shared=True, locality=True, similar=True, exact_cap=50, shared_cap=20, similarity=0.5),
    "5:1": node("AgentWorkflowCompareContacts", "Compare", records=["3:1", 0], candidates=["4:1", 0]),
    "6:1": node("AgentWorkflowLearnFromReviews", "Learn", pairs=["5:1", 0], batch=10, audit=True, answer_key_file="", budget=1000, seed=0),
    "7:1": node("AgentWorkflowDecideByScore", "Decide", pairs=["6:1", 0], match_at=0.5, review_from=0.1, corroboration=False),
    "8:1": node("AgentWorkflowClusterGuard", "Group", data=["7:1", 0], max_size=12, scores_separate=True),
    "9:1": node("AgentWorkflowEntityPerContact", "Entities", records=["2:1", 0], clusters=["8:1", 0]),
    "10": node("AgentWorkflowApiOutput", "Output · entities", value=["9:1", 0], name="entities"),
}  # fmt: skip
RELEASE = {
    "name": "contact-matching",
    "semver": "1.0.0",
    "version": "abc123",
    "workflow_id": "wf",
    "description": "",
    "prompt": PROMPT,
    "workflow": {
        "extra": {
            "linearData": {
                "inputs": [
                    [1, "folder", {"description": "The folder with your contact exports."}],
                    [7, "match_at", {"description": "Merge pairs at least this likely to be the same."}],
                ]
            }
        }
    },
    "signature": {
        "inputs": [
            {"name": "exports", "node": "1", "widget": "folder", "targets": [["1", "folder"]], "type": "COMBO", "default": "sample"},
            {"name": "match_at", "node": "7:1", "widget": "match_at", "targets": [["7:1", "match_at"]], "type": "FLOAT", "default": 0.5},
        ],
        "outputs": [{"name": "entities", "node": "10"}],
    },
}  # fmt: skip
CONTRACT = {
    "title": "Contact matching",
    "purpose": "Give every contact one entity id.",
    "examples": [
        {
            "id": "E1",
            "covers": ["R1"],
            "given": {"exports": "sample"},
            "files": SAMPLE,
            "expect": {"entities": "EX01 EX01, EX02 EX01, EX03 EX03, EX04 EX04"},
        }
    ],
    "steps": [{"id": "read", "purpose": "Read the exports."}],
}


class ExportTests(unittest.TestCase):
    def test_a_release_becomes_a_repo_whose_examples_pass_without_comfyui(self) -> None:
        self.assertTrue(readiness(RELEASE, CONTRACT)["ready"])
        with tempfile.TemporaryDirectory() as tmp:
            repo = generate(RELEASE, CONTRACT, Path(tmp) / "contact-matching", Path(tmp))
            pipeline = (repo / "contact_matching" / "pipeline.py").read_text(encoding="utf-8")
            self.assertIn("steps.read_contacts(rt.ctx('read_contacts'), folder=n1[0]", pipeline)
            self.assertIn("match_at=values['match_at']", pipeline)
            self.assertIn('"numpy==', (repo / "pyproject.toml").read_text(encoding="utf-8"))
            # Windows: its CI runs there too, and the CLI prints UTF-8 whatever the console's code page.
            self.assertIn(
                "windows-latest", (repo / ".github/workflows/test.yml").read_text(encoding="utf-8")
            )
            cli_text = (repo / "contact_matching" / "cli.py").read_text(encoding="utf-8")
            self.assertIn('stream.reconfigure(encoding="utf-8", errors="replace")', cli_text)
            # The generated package runs on its own: its CLI checks the examples (no ComfyUI on the path).
            done = subprocess.run(
                [sys.executable, "-I", "-c", f"import sys; sys.path.insert(0, {str(repo)!r}); from contact_matching.cli import main; main(['check'])"],
                capture_output=True,
                text=True,
                timeout=300,
            )  # fmt: skip
            self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
            self.assertIn("1 example(s): all passed", done.stdout)
            # init writes a config with every input; run uses it, writes the outputs and a review batch.
            cli = [sys.executable, "-I", "-c", f"import sys; sys.path.insert(0, {str(repo)!r}); from contact_matching.cli import main; main(sys.argv[1:])"]  # fmt: skip
            subprocess.run(
                [*cli, "init", str(Path(tmp) / "config.yaml")],
                check=True,
                capture_output=True,
                timeout=60,
            )
            config = (Path(tmp) / "config.yaml").read_text(encoding="utf-8")
            self.assertIn(
                "# Merge pairs at least this likely to be the same.\nmatch_at: 0.5", config
            )
            data = repo / "contact_matching" / "examples" / "E1" / "sample"
            out = Path(tmp) / "out"
            ran = subprocess.run([*cli, "run", "--exports", str(data), "--out", str(out)], capture_output=True, text=True, timeout=300)  # fmt: skip
            self.assertEqual(ran.returncode, 0, ran.stdout + ran.stderr)
            self.assertIn("EX02,EX01", (out / "entities.csv").read_text(encoding="utf-8"))
            batch = (out / "review_batch.csv").read_text(encoding="utf-8-sig").splitlines()
            self.assertTrue(batch[0].startswith("id,decision,likely,why,a_name"))
            # Decisions filled in go back in, and are kept for the next run.
            pair = batch[1].split(",")[0]
            (Path(tmp) / "mine.csv").write_text(
                f"id,decision\n{pair},different\n", encoding="utf-8"
            )
            again = subprocess.run([*cli, "run", "--exports", str(data), "--out", str(out), "--reviews", str(Path(tmp) / "mine.csv")], capture_output=True, text=True, timeout=300)  # fmt: skip
            self.assertEqual(again.returncode, 0, again.stdout + again.stderr)
            self.assertIn("learned from 1 of your reviews", again.stdout)
            self.assertIn(f"{pair},different", (out / "reviews.csv").read_text(encoding="utf-8"))

    def test_a_node_without_a_python_export_blocks_with_its_name(self) -> None:
        release = json.loads(json.dumps(RELEASE))
        release["prompt"]["11"] = node("AgentWorkflowFuzzyNode", "Plan a summary")
        found = readiness(release, CONTRACT)
        self.assertFalse(found["ready"])
        self.assertEqual(
            found["blockers"],
            ["Plan a summary (AgentWorkflowFuzzyNode) cannot run outside ComfyUI yet."],
        )
        self.assertIn("Not published yet", readiness(None, CONTRACT)["blockers"][0])
        self.assertEqual(
            readiness(RELEASE, {})["todo"],
            ["The contract has no examples: the exported tests will run the stage Checks only."],
        )


if __name__ == "__main__":
    unittest.main()
