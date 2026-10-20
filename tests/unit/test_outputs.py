"""Old run outputs: only what is past the kept days goes, the newest of each kind always stays, a dry
run removes nothing, answer keys are never touched, and tool runs never share a folder."""

# Verifies: R16

import os
import tempfile
import time
import unittest
from pathlib import Path

from custom_nodes.agent_workflow_studio.agent_tools import run_folder
from custom_nodes.agent_workflow_studio.outputs import cleanup

DAY = 86400


def touch(path: Path, age_days: float, now: float, size: int = 10) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.suffix:
        path.write_bytes(b"x" * size)
    else:
        path.mkdir(exist_ok=True)
        (path / "out.csv").write_bytes(b"x" * size)
    os.utime(path, (now - age_days * DAY, now - age_days * DAY))
    return path


class OutputsTests(unittest.TestCase):
    def test_only_old_outputs_go_and_the_newest_of_each_stays(self) -> None:
        now = time.time()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            old_run = touch(root / "agent_tools/wf/cluster_guard-20261001-101010", 40, now)
            touch(root / "agent_tools/wf/cluster_guard-20261019-101010123", 2, now)
            lone_old = touch(
                root / "agent_tools/wf/score-20260901-101010", 60, now
            )  # the newest of its tool
            old_file = touch(root / "nayose_reference/nayose_result_00001_.csv", 40, now)
            touch(root / "nayose_reference/nayose_result_00002_.csv", 35, now)
            key = touch(root / "nayose_benchmark/nayose_bench/key/answer_key_00001_.csv", 90, now)
            mine = touch(root / "my_notes.csv", 90, now)  # not made by a run

            preview = cleanup(root, 30, now, dry_run=True)
            self.assertEqual(
                preview["removed"],
                [
                    "agent_tools/wf/cluster_guard-20261001-101010",
                    "nayose_reference/nayose_result_00001_.csv",
                ],
            )
            self.assertTrue(old_run.exists(), "a dry run removes nothing")
            done = cleanup(root, 30, now, dry_run=False)
            self.assertEqual(done["count"], 2)
            self.assertFalse(old_run.exists() or old_file.exists())
            # The newest of each (even when old), answer keys and files no run made all stay.
            self.assertTrue(lone_old.exists() and key.exists() and mine.exists())
            self.assertEqual(
                cleanup(root, 0, now, dry_run=False)["count"], 0, "0 days keeps everything"
            )

    def test_tool_runs_never_share_a_folder(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            folders = {run_folder(Path(tmp), "wf", "score") for _ in range(5)}
            self.assertEqual(len(folders), 5)
            self.assertTrue(all(f.is_dir() for f in folders))


if __name__ == "__main__":
    unittest.main()
