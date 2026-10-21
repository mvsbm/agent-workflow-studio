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
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from .standards import port_socket, port_types
from .standards import value_problems as type_problems

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


# A tool's ports are typed with the studio's type standard (standards.py), like a node's sockets, so
# a tool reads like a node and keeps its interface when it graduates into one: core types (TABLE is
# the rows themselves) and the file-stored dataset classes of any domain (by reference).
MAX_EXAMPLES = 8


def port_problems(side: str, ports: list) -> list[str]:
    """What is wrong with a tool's declared inputs or outputs (names and studio types)."""
    if not isinstance(ports, list):
        return [f"{side} must be a list of {{name, kind}}."]
    problems, seen = [], set()
    for port in ports:
        name, kind = (port or {}).get("name"), (port or {}).get("kind")
        if not isinstance(name, str) or not NAME.fullmatch(name):
            problems.append(f"{side}: {name!r} is not a short lowercase name.")
        elif name in seen:
            problems.append(f"{side}: {name} is declared twice.")
        seen.add(name)
        if port_socket(kind) is None:
            problems.append(
                f"{side}: {name} has kind {kind!r}; use a registered type: {', '.join(port_types())}."
            )
    return problems


def typed_ports(ports: list) -> list[dict]:
    """The ports with each kind written as its socket (old names and class names resolved)."""
    return [{**port, "kind": port_socket(port["kind"])} for port in ports]


def value_problems(side: str, values: dict, ports: list) -> list[str]:
    """Where values differ from the declared ports: a port missing, or of another kind."""
    problems = []
    for port in ports:
        if port["name"] not in values:
            problems.append(f"{side} {port['name']} ({port['kind']}) is missing")
        elif type_problems(port_socket(port["kind"]) or port["kind"], values[port["name"]]):
            got = type(values[port["name"]]).__name__
            problems.append(f"{side} {port['name']} should be {port['kind']}, got {got}")
    return problems


def _same(expected, actual) -> bool:
    if isinstance(expected, float) or isinstance(actual, float):
        return isinstance(actual, (int, float)) and math.isclose(expected, actual, rel_tol=1e-9)
    if isinstance(expected, dict) and isinstance(actual, dict):
        return expected.keys() == actual.keys() and all(
            _same(v, actual[k]) for k, v in expected.items()
        )
    if isinstance(expected, list) and isinstance(actual, list):
        return len(expected) == len(actual) and all(map(_same, expected, actual))
    return expected == actual


def run_examples(code: str, manifest: dict, folder: Path, timeout: float = 60) -> list[dict]:
    """A tool's own tests: each example's inputs run in the sandbox, and the outputs it names must
    come back equal, with every declared output of its declared kind."""
    results = []
    for n, example in enumerate(manifest.get("examples") or [], 1):
        try:
            outputs = run_tool(code, example.get("inputs") or {}, folder / f"example-{n}", timeout)
            problems = value_problems("output", outputs, manifest.get("outputs") or [])
            problems += [
                f"{key}: expected {json.dumps(want, ensure_ascii=False)[:120]}, got "
                f"{json.dumps(outputs.get(key), ensure_ascii=False)[:120]}"
                for key, want in (example.get("expect") or {}).items()
                if not _same(want, outputs.get(key))
            ]
        except RuntimeError as error:
            problems = [str(error)[:300]]
        results.append({"example": n, "passed": not problems, "problems": problems})
    return results


def tools_used(workflow: dict) -> list[str]:
    """The tools a workflow's Agent Tool nodes run, inside stages included (in order, once each)."""
    graphs = [workflow, *((workflow.get("definitions") or {}).get("subgraphs") or [])]
    names = [
        (node.get("widgets_values") or [""])[0]
        for graph in graphs
        for node in graph.get("nodes") or []
        if node.get("type") == "AgentWorkflowAgentTool"
    ]
    return list(dict.fromkeys(n for n in names if isinstance(n, str) and n))


def test_tools(repo, names: list[str], folder: Path) -> list[dict]:
    """Each tool's own tests (its examples), run on its approved code: the workflow's unit tests."""
    results = []
    for name in names:
        try:
            code, manifest = repo.load_tool(name)
        except ValueError as error:
            results.append({"tool": name, "passed": False, "problems": [str(error)]})
            continue
        if not manifest.get("examples"):
            problem = "has no examples (its tests): propose it again with examples"
            results.append({"tool": name, "passed": False, "problems": [problem]})
            continue
        runs = run_examples(code, manifest, folder / name)
        problems = [
            f"example {r['example']}: {'; '.join(r['problems'])}" for r in runs if not r["passed"]
        ]
        results.append(
            {"tool": name, "passed": not problems, "problems": problems, "examples": len(runs)}
        )
    return results


def example_problems(examples) -> list[str]:
    """What is wrong with the examples a proposal gives (they become the tool's tests)."""
    if not isinstance(examples, list) or not examples:
        return ["Give examples: [{inputs, expect}], at least one; they are the tool's tests."]
    if len(examples) > MAX_EXAMPLES:
        return [f"At most {MAX_EXAMPLES} examples: keep them small and telling."]
    return [
        f"Example {n}: give inputs and expect as objects (expect: the outputs it must return)."
        for n, e in enumerate(examples, 1)
        if not isinstance(e, dict)
        or not isinstance(e.get("inputs"), dict)
        or not isinstance(e.get("expect"), dict)
        or not e["expect"]
    ]


def approval_text(
    name: str,
    description: str,
    inputs: list,
    outputs: list,
    code: str,
    tests: list | None = None,
    why: str = "",
) -> str:
    ports = lambda items: ", ".join(f"{p['name']} ({p['kind']})" for p in items) or "none"  # noqa: E731
    checked = (
        [
            f"TESTS: {sum(t['passed'] for t in tests)}/{len(tests)} of its examples pass in the sandbox."
        ]
        if tests
        else []
    )
    return "\n".join(
        [
            f"TOOL: {name}",
            f"WHAT IT DOES: {description}",
            *([f"WHY CODE, NOT A NODE: {why}"] if why else []),
            f"TAKES: {ports(inputs)}",
            f"GIVES: {ports(outputs)}",
            *checked,
            f"SAFETY: {protections()} Time limit: {TIMEOUT_SECONDS} s.",
            "",
            "CODE (this exact code is what will run):",
            code,
        ]
    )


def run_folder(output_root: Path, workflow_id: str, name: str) -> Path:
    """A new folder for one run of a tool, created here: never shared with another run, even
    within the same millisecond."""
    base = output_root / "agent_tools" / workflow_id[:36]
    base.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S") + f"{int(time.time() * 1000) % 1000:03d}"
    for n in range(1000):
        folder = base / (f"{name}-{stamp}" + (f"-{n}" if n else ""))
        try:
            folder.mkdir()
            return folder
        except FileExistsError:
            continue
    raise RuntimeError("Could not create a run folder.")


__all__ = [
    "NAME",
    "approval_text",
    "code_hash",
    "example_problems",
    "port_problems",
    "run_examples",
    "value_problems",
    "protections",
    "run_folder",
    "run_tool",
    "sandboxed",
    "test_tools",
    "tools_used",
    "typed_ports",
]
