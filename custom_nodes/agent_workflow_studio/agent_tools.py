"""Agent-written tools: Python the agent writes, the user approves, and a node runs confined.

A tool is a stdlib-only Python file with ``run(inputs: dict) -> dict``. The agent writes it in
the workflow's workspace and proposes it; the user sees the full code and approves. Approval
copies the code into the workflow's project history (which the agent cannot write) with its
hash. The Agent Tool node runs only that approved copy, in a separate process with a time
limit, and on macOS inside a sandbox: no network, no other programs, no reading outside the
Python install, the tool and an optional input folder the user chose, writing only to the run's
own folder.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

NAME = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
RUNNER = Path(__file__).with_name("agent_tool_runner.py")
TIMEOUT_SECONDS = 600  # real data sets (thousands of records in pure Python) need minutes
MAX_OUTPUT = 4 * 1024 * 1024


def python_executable() -> Path:
    """The base interpreter (not the venv): tools are stdlib-only by design."""
    return Path(os.path.realpath(sys.executable))


def sandboxed() -> bool:
    return sys.platform == "darwin" and shutil.which("sandbox-exec") is not None


def protections() -> str:
    if sandboxed():
        return (
            "Runs in a separate process with a time limit and a macOS sandbox: no network, "
            "no other programs, no reading your files, writing only to its own run folder."
        )
    return "Runs in a separate process with a time limit only: this system has no sandbox, so review the code carefully."


def _profile(tool_dir: Path, run_dir: Path, reads_extra: list[Path]) -> str:
    python = python_executable()
    install = python.parent.parent
    reads = [
        install,
        tool_dir,
        run_dir,
        Path("/System"),
        Path("/usr/lib"),
        Path("/usr/share"),
        Path("/private/var/db/dyld"),
        Path("/dev"),
        Path("/private/etc"),
        Path("/Library/Preferences"),
    ] + reads_extra
    quote = lambda p: json.dumps(str(p))  # noqa: E731  (SBPL strings use the same escaping)
    return "\n".join(
        [
            "(version 1)",
            "(deny default)",
            "(allow process-fork)",
            f"(allow process-exec (literal {quote(python)}))",
            '(allow file-read* (literal "/") '
            + " ".join(f"(subpath {quote(p)})" for p in reads)
            + ")",
            f'(allow file-write* (subpath {quote(run_dir)}) (literal "/dev/null"))',
            "(allow sysctl-read)",
            '(allow mach-lookup (global-name "com.apple.system.opendirectoryd.libinfo"))',
        ]
    )


def run_tool(
    code: str,
    inputs: dict,
    run_dir: Path,
    timeout: float = TIMEOUT_SECONDS,
    read_dir: Path | None = None,
    upstream: list[Path] = (),
) -> dict:
    """Run tool code with JSON inputs; returns its JSON outputs or raises RuntimeError.

    ``read_dir`` is a folder the tool may read (not write), exposed as ``TOOL_INPUT_DIR``;
    ``upstream`` are earlier tools' run folders it may also read.
    """
    run_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="aws-tool-") as scratch:
        tool_dir = Path(scratch).resolve()  # the sandbox matches real paths (/var is a symlink)
        (tool_dir / "tool.py").write_text(code)
        # The runner sits beside the tool, so the sandbox needs no access to this package.
        shutil.copy(RUNNER, tool_dir / "runner.py")
        command = [
            str(python_executable()),
            "-I",
            str(tool_dir / "runner.py"),
            str(tool_dir / "tool.py"),
        ]
        if sandboxed():
            profile = tool_dir / "profile.sb"
            reads = [p.resolve() for p in [*([read_dir] if read_dir else []), *upstream]]
            profile.write_text(_profile(tool_dir, run_dir.resolve(), reads))
            command = ["sandbox-exec", "-f", str(profile), *command]
        env = {
            "TOOL_RUN_DIR": str(run_dir.resolve()),
            **({"TOOL_INPUT_DIR": str(read_dir.resolve())} if read_dir else {}),
            "PATH": "/usr/bin:/bin",
            "LANG": "C.UTF-8",
            "PYTHONIOENCODING": "utf-8",
        }
        try:
            done = subprocess.run(
                command,
                input=json.dumps(inputs, ensure_ascii=False),
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=timeout,
                cwd=run_dir,
                env=env,
            )
        except subprocess.TimeoutExpired:
            raise RuntimeError(f"Tool stopped after {timeout:.0f} s time limit.") from None
    if len(done.stdout) > MAX_OUTPUT:
        raise RuntimeError("Tool output is too large.")
    try:
        result = json.loads(done.stdout.strip().splitlines()[-1]) if done.stdout.strip() else None
    except ValueError:
        result = None
    if done.returncode or not isinstance(result, dict) or "error" in result and len(result) == 1:
        detail = (result or {}).get("error") or done.stderr.strip()[-800:] or "no output"
        raise RuntimeError(f"Tool failed: {detail}")
    return result["outputs"]


def code_hash(code: str) -> str:
    return hashlib.sha256(code.encode()).hexdigest()


def approval_text(name: str, description: str, inputs: list, outputs: list, code: str) -> str:
    ports = lambda items: ", ".join(f"{p['name']} ({p['kind']})" for p in items) or "none"  # noqa: E731
    return "\n".join(
        [
            f"TOOL: {name}",
            f"WHAT IT DOES: {description}",
            f"TAKES: {ports(inputs)}",
            f"GIVES: {ports(outputs)}",
            f"SAFETY: {protections()} Time limit: {TIMEOUT_SECONDS} s.",
            "",
            "CODE (this exact code is what will run):",
            code,
        ]
    )


def run_folder(output_root: Path, workflow_id: str, name: str) -> Path:
    stamp = time.strftime("%Y%m%d-%H%M%S")
    return output_root / "agent_tools" / workflow_id[:36] / f"{name}-{stamp}"


__all__ = [
    "NAME",
    "approval_text",
    "code_hash",
    "protections",
    "run_folder",
    "run_tool",
    "sandboxed",
]
