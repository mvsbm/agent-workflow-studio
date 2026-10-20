"""Isolated ComfyUI instance for end-to-end tests.

A temp base directory gets its own ``custom_nodes/`` holding only this package, a
free loopback port and CPU execution, so tests never touch the user's ComfyUI.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import tempfile
import time
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
PACKAGE = REPO / "custom_nodes" / "agent_workflow_studio"
APP_HOME = Path(
    os.environ.get("AGENT_WORKFLOW_STUDIO_HOME", Path.home() / ".local/share/agent-workflow-studio")
)
COMFY = APP_HOME / "ComfyUI"
SETTINGS = {"Comfy.TutorialCompleted": True}


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


class ComfyFixture:
    def __init__(self, env: dict[str, str] | None = None, packs: tuple[Path, ...] = ()) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="aws-e2e-"))
        nodes = self.root / "custom_nodes"
        nodes.mkdir()
        (nodes / "agent_workflow_studio").symlink_to(PACKAGE)
        for pack in packs:  # test-only node packs (e.g. the capability tests')
            (nodes / pack.name).symlink_to(pack)
        for name in ("user/default/workflows", "output", "input", "temp", "models"):
            (self.root / name).mkdir(parents=True, exist_ok=True)
        weights = COMFY / "models" / "agent_workflow_studio"
        if (
            weights.is_dir()
        ):  # public model weights only (no user data): shared, not downloaded again
            (self.root / "models" / "agent_workflow_studio").symlink_to(weights)
        (self.root / "user/default/comfy.templates.json").write_text("[]")
        (self.root / "user/default/comfy.settings.json").write_text(json.dumps(SETTINGS))
        self.port = free_port()
        self.url = f"http://127.0.0.1:{self.port}"
        self._log = open(self.root / "comfy.log", "w")
        self.proc = subprocess.Popen(
            [
                str(COMFY / ".venv/bin/python"),
                "main.py",
                "--cpu",
                "--listen",
                "127.0.0.1",
                "--port",
                str(self.port),
                "--base-directory",
                str(self.root),
                "--user-directory",
                str(self.root / "user"),
                "--disable-api-nodes",
                "--database-url",
                f"sqlite:///{self.root}/user/comfyui.db",
            ],
            cwd=COMFY,
            stdout=self._log,
            stderr=subprocess.STDOUT,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1", **(env or {})},
        )
        self._wait_ready()

    def _wait_ready(self) -> None:
        for _ in range(240):
            if self.proc.poll() is not None:
                raise RuntimeError("ComfyUI exited during startup:\n" + self.log_tail())
            try:
                urllib.request.urlopen(self.url + "/object_info", timeout=2)
                return
            except OSError:
                time.sleep(0.5)
        raise RuntimeError("ComfyUI startup timed out:\n" + self.log_tail())

    def log_tail(self, lines: int = 60) -> str:
        self._log.flush()
        return "\n".join((self.root / "comfy.log").read_text().splitlines()[-lines:])

    def close(self, keep: bool = False) -> None:
        self.proc.terminate()
        try:
            self.proc.wait(10)
        except subprocess.TimeoutExpired:
            self.proc.kill()
        self._log.close()
        if not keep:
            shutil.rmtree(self.root, ignore_errors=True)

    def __enter__(self) -> ComfyFixture:
        return self

    def __exit__(self, *_exc) -> None:
        self.close()
