"""Real filesystem boundary checks; temporary data only, no model calls."""

import base64
import concurrent.futures
import os
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from .pi_chat_workspace import MAX_FILE, PiChatWorkspace, workspace_id_for_graph


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.workspace = PiChatWorkspace(self.base / "workspaces", str(uuid.uuid4()))
        self.workspace.perform("workspace_info", {})
        self.outside = self.base / "outside.txt"
        self.outside.write_text("preserve me")

    def write(self, path, text, expected=None, workspace=None):
        return (workspace or self.workspace).perform(
            "workspace_write",
            {
                "path": path,
                "data": base64.b64encode(text.encode()).decode(),
                "expected_sha256": expected,
            },
        )

    def test_native_graph_ids_map_to_safe_stable_workspaces(self):
        id = str(uuid.uuid4())
        self.assertEqual(workspace_id_for_graph(id), id)
        self.assertEqual(workspace_id_for_graph(id.upper()), id)
        for opaque in ("legacy-workflow", "../../not-a-path", "saved workflow 日本語"):
            with self.subTest(opaque=opaque):
                mapped = workspace_id_for_graph(opaque)
                self.assertEqual(mapped, str(uuid.UUID(mapped)))
                self.assertEqual(mapped, workspace_id_for_graph(opaque))
                self.assertNotEqual(mapped, workspace_id_for_graph(opaque + "-other"))
                workspace = PiChatWorkspace(self.workspace.root, mapped)
                self.assertEqual(workspace.path.parent, self.workspace.root)
        with self.assertRaisesRegex(ValueError, "Refresh ComfyUI"):
            workspace_id_for_graph(None)
        for invalid in ("", "bad\x00id", "x" * 513, 42):
            with self.assertRaises(ValueError):
                workspace_id_for_graph(invalid)

    def test_persistence_isolation_and_atomic_compare_and_swap(self):
        first = self.write("scripts/check.py", "first")
        same = PiChatWorkspace(self.workspace.root, self.workspace.id)
        self.assertEqual(
            base64.b64decode(same.perform("workspace_read", {"path": "scripts/check.py"})["data"]),
            b"first",
        )
        other = PiChatWorkspace(self.workspace.root, str(uuid.uuid4()))
        self.assertFalse(other.perform("workspace_stat", {"path": "scripts/check.py"})["exists"])
        with self.assertRaisesRegex(ValueError, "changed"):
            self.write("scripts/check.py", "overwrite without a read")
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:

            def compete(i):
                try:
                    self.write("scripts/check.py", str(i), first["sha256"], same)
                    return True
                except ValueError:
                    return False

            self.assertEqual(sum(pool.map(compete, range(4))), 1)
        old = same.perform("workspace_read", {"path": "scripts/check.py"})
        with (
            patch("os.replace", side_effect=OSError("simulated disk failure")),
            self.assertRaises(ValueError),
        ):
            self.write("scripts/check.py", "must not replace", old["sha256"])
        self.assertEqual(same.perform("workspace_read", {"path": "scripts/check.py"}), old)
        self.assertEqual(len(same.perform("workspace_info", {})["files"]), 1)

    def test_traversal_symlinks_hardlinks_devices_and_workflow_ids(self):
        for path in (
            "../outside.txt",
            str(self.outside),
            "a/../../outside.txt",
            "a\\..\\outside.txt",
            "a//x",
            "a/./x",
            "bad\x00path",
        ):
            with self.subTest(path=path), self.assertRaises(ValueError):
                self.write(path, "bad")
        (self.workspace.path / "link").symlink_to(self.outside)
        (self.workspace.path / "directory").symlink_to(self.base, target_is_directory=True)
        os.link(self.outside, self.workspace.path / "hardlink")
        os.mkfifo(self.workspace.path / "fifo")
        for path in ("link", "directory/outside.txt", "hardlink", "fifo"):
            for action in ("workspace_read", "workspace_stat"):
                with (
                    self.subTest(path=path, action=action),
                    self.assertRaises(ValueError),
                ):
                    self.workspace.perform(action, {"path": path})
            with self.subTest(path=path), self.assertRaises(ValueError):
                self.write(path, "bad")
        evil_id = str(uuid.uuid4())
        (self.workspace.root / evil_id).symlink_to(self.base, target_is_directory=True)
        with self.assertRaises(ValueError):
            PiChatWorkspace(self.workspace.root, evil_id).perform("workspace_info", {})
        with self.assertRaises(ValueError):
            PiChatWorkspace(self.workspace.root, "../../escape")
        self.assertEqual(self.outside.read_text(), "preserve me")

    def test_bounded_storage_invalid_payloads_and_no_then_run(self):
        self.write("ok.txt", "ok")
        for args in (
            {"path": "ok.txt", "data": "not base64", "expected_sha256": "0" * 64},
            {"path": "new.txt", "data": "?", "expected_sha256": None},
            {
                "path": "new.txt",
                "data": "",
                "expected_sha256": None,
                "then_run": {"command": "touch /tmp/bad"},
            },
        ):
            with self.assertRaises(ValueError):
                self.workspace.perform("workspace_write", args)
        with self.assertRaisesRegex(ValueError, "128 KiB"):
            self.write("too-big.txt", "x" * (MAX_FILE + 1))
        with (
            patch("custom_nodes.agent_workflow_studio.pi_chat_workspace.MAX_TOTAL", 2),
            self.assertRaisesRegex(ValueError, "storage quota"),
        ):
            self.write("quota.txt", "x")
        with (
            patch("custom_nodes.agent_workflow_studio.pi_chat_workspace.MAX_FILES", 1),
            self.assertRaisesRegex(ValueError, "entry quota"),
        ):
            self.write("quota.txt", "x")
        with (
            patch("custom_nodes.agent_workflow_studio.pi_chat_workspace.MAX_DIRS", 0),
            self.assertRaisesRegex(ValueError, "entry quota"),
        ):
            self.write("directory/new.txt", "x")
        self.assertFalse((self.workspace.path / "directory").exists())
        self.assertEqual(
            self.workspace.perform("workspace_info", {})["files"],
            [{"path": "ok.txt", "size": 2}],
        )


if __name__ == "__main__":
    unittest.main()
