"""Evaluate the real chat agent (Pi, or Claude Code with --agent claude_code) on a few requests.
Makes real provider calls (costs money).

Usage (from the repo root):
  ~/.local/share/agent-workflow-studio/ComfyUI/.venv/bin/python tests/eval/run_eval.py \
      --provider opencode-go --model deepseek-v4-flash [--only id ...]
      [--agent claude_code --agent-model haiku]   # Claude Code chats; LLM nodes keep the Pi model

Each task runs in a fresh isolated ComfyUI. The chat uses Studio Keychain credentials;
the provider's saved key is also passed to that test instance so the Structured LLM
node uses the same model. Scores come from evidence: outputs, canvas and versions.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "e2e"))
from fixture import COMFY, REPO, ComfyFixture  # noqa: E402

sys.path.insert(0, str(COMFY))
from custom_nodes.agent_workflow_studio.pi_auth import provider_credential_environment  # noqa: E402

sys.path.insert(0, str(REPO / "custom_nodes/agent_workflow_studio/nayose_bench"))
import generate as nayose_data  # noqa: E402
import score as nayose_score  # noqa: E402

STUDIO_STATE = (
    Path(
        os.environ.get(
            "AGENT_WORKFLOW_STUDIO_HOME", Path.home() / ".local/share/agent-workflow-studio"
        )
    )
    / "state"
)


def score(task: dict, r: dict) -> tuple[bool, list[str]]:
    failures = []
    if r.get("error"):
        failures.append(f"harness: {r['error']}")
    texts = " | ".join(r.get("texts", []))
    if "expect_text" in task and task["expect_text"] not in texts:
        failures.append(f"output lacks {task['expect_text']!r}")
    if "expect_text_ci" in task and task["expect_text_ci"].lower() not in texts.lower():
        failures.append(f"output lacks {task['expect_text_ci']!r}")
    if r.get("images", 0) < task.get("expect_images", 0):
        failures.append(
            f"expected {task['expect_images']} saved image(s), got {r.get('images', 0)}"
        )
    if task.get("keep_setup_nodes") and not set(r.get("setup_ids", [])) <= set(
        r.get("node_ids", [])
    ):
        failures.append("existing nodes were not preserved")
    if task.get("expect_record") and not any(
        "Run · Pi CLI Structured LLM" in v for v in r.get("versions", [])
    ):
        failures.append("no LLM run record")
    kind = task.get("expect_kind")
    if kind and not any(f"Run · {kind}" in v for v in r.get("versions", [])):
        failures.append(f"no {kind} run record")
    for label, pattern in task.get("expect_chat", []):  # what the agent says it knows
        if not re.search(pattern, r.get("chat", ""), re.IGNORECASE):
            failures.append(f"does not explain: {label}")
    if task.get("expect_data_input") and not (r.get("contract") or {}).get("data_inputs"):
        failures.append("the API takes its data as text, not as an uploaded folder or file")
    # Workflow code: a tool the user approved, whose own tests passed in the workflow's Run checks.
    if task.get("expect_tool"):
        versions = r.get("versions", [])
        if not any(v.startswith("user: Tool approved: ") for v in versions):
            failures.append("no approved tool")
        if not any("Checks · " in v and "✓ tool tests" in v for v in versions):
            failures.append("its tool tests did not pass in Run checks")
    if task.get("expect_no_nodes") and r.get("node_ids"):
        failures.append("changed the canvas when asked not to")
    if not task.get("no_run") and "success" not in r.get("run_statuses", []):
        failures.append("no successful native run")
    if task.get("dataset"):
        f1 = ((r.get("nayose") or {}).get("pairwise") or {}).get("f1")
        if f1 is None:
            failures.append("no nayose_result.csv written by this run")
        elif f1 < task.get("expect_pairwise_f1", 0):
            failures.append(f"pairwise F1 {f1} below {task['expect_pairwise_f1']}")
    if task.get("expect_file"):
        check = r.get("file_check") or {}
        if not check.get("path"):
            failures.append(f"no {task['expect_file']['name']} written by this run")
        elif check["matched"] < check["needed"]:
            failures.append(
                f"{task['expect_file']['name']}: {check['matched']}/{check['total']} expected rows "
                f"(need {check['needed']}); missing e.g. {check['missing'][:2]}"
            )
    return not failures, failures


def _norm(value: object) -> str:
    """Compare cells loosely: case, spaces, currency marks, thousands separators, trailing .0."""
    text = str(value or "").strip().lower().replace("¥", "").replace("円", "").replace(",", "")
    try:
        number = float(text)
        return str(int(number)) if number == int(number) else str(number)
    except ValueError:
        return text


def check_file(output: Path, expect: dict, started: float) -> dict:
    """The newest file of that name written by this run, and how many expected rows it holds."""
    import csv
    import io

    found = sorted(
        (
            p
            for p in output.rglob(f"{Path(expect['name']).stem}*{Path(expect['name']).suffix}")
            if p.stat().st_mtime >= started
        ),
        key=lambda p: p.stat().st_mtime,
    )
    needed = expect.get("min_match", len(expect["rows"]))
    if not found:
        return {
            "path": None,
            "matched": 0,
            "total": len(expect["rows"]),
            "needed": needed,
            "missing": [],
        }
    text = found[-1].read_text(encoding="utf-8-sig", errors="replace")
    rows = [
        {k.strip().lower(): _norm(v) for k, v in row.items() if k}
        for row in csv.DictReader(io.StringIO(text))
    ]
    missing = [
        want
        for want in expect["rows"]
        if not any(all(row.get(k.lower()) == _norm(v) for k, v in want.items()) for row in rows)
    ]
    return {
        "path": str(found[-1]),
        "matched": len(expect["rows"]) - len(missing),
        "total": len(expect["rows"]),
        "needed": needed,
        "missing": missing,
    }


class Studio:
    """The user's running studio (8188), used in place of an isolated instance with --studio."""

    url = "http://127.0.0.1:8188"

    def __init__(self) -> None:
        self.input, self.output, self.user = (
            STUDIO_STATE / f"comfy-{d}" for d in ("input", "output", "user")
        )
        self.log = STUDIO_STATE.parent / "logs/comfyui.log"

    def __enter__(self) -> Studio:
        return self

    def __exit__(self, *_exc) -> None:
        pass


def isolated(comfy: ComfyFixture) -> ComfyFixture:
    comfy.input, comfy.output, comfy.user = (comfy.root / d for d in ("input", "output", "user"))
    comfy.log = comfy.root / "comfy.log"
    return comfy


def publish(comfy: ComfyFixture, task: dict, result: dict) -> list[str]:
    """Copy the finished workflow, its project history, outputs and data into the user's studio.

    Never overwrites: anything already there is left alone and reported.
    """
    workflow_id, workflow = result.get("workflow_id"), result.get("workflow")
    if not workflow_id or not workflow:
        return ["nothing to publish"]
    copies = [
        (
            comfy.root / "user/agent-projects" / workflow_id,
            STUDIO_STATE / "comfy-user/agent-projects" / workflow_id,
        ),
        (
            comfy.root / "user/agent-projects" / f"{workflow_id}.git",
            STUDIO_STATE / "comfy-user/agent-projects" / f"{workflow_id}.git",
        ),
        (
            comfy.root / "output/agent_tools" / workflow_id,
            STUDIO_STATE / "comfy-output/agent_tools" / workflow_id,
        ),
    ]
    if task.get("dataset"):
        copies.append(
            (comfy.root / "input" / task["dataset"], STUDIO_STATE / "comfy-input" / task["dataset"])
        )
        bench = Path("nayose_benchmark") / task["dataset"]  # the Score node's hidden key
        copies.append((comfy.root / "output" / bench, STUDIO_STATE / "comfy-output" / bench))
    notes = []
    for source, target in copies:
        if not source.exists():
            continue
        if target.exists():
            notes.append(f"kept existing {target}")
            continue
        shutil.copytree(source, target, symlinks=True)
    name = (
        STUDIO_STATE
        / "comfy-user/default/workflows"
        / f"Eval - {task['id']} {time.strftime('%Y%m%d-%H%M')}.json"
    )
    if not name.exists():
        name.parent.mkdir(parents=True, exist_ok=True)
        name.write_text(json.dumps(workflow))
        notes.append(f"workflow: {name.name}")
    return notes


RETRIED = ("comfy_propose_contract", "comfy_propose_design", "comfy_build_step")


def retries(r: dict) -> dict[str, int]:
    """Failed calls before the first success, per proposal tool: how well the agent's first try
    meets the standard (each failure costs the user a turn and the agent tokens)."""
    failed: dict[str, int] = {}
    succeeded: set[str] = set()
    for line in r.get("tools", []):
        state, _, tool = line.partition(" · ")
        if tool not in RETRIED or tool in succeeded:
            continue
        failed.setdefault(tool, 0)
        if state == "Failed":
            failed[tool] += 1
        else:
            succeeded.add(tool)
    return {t.replace("comfy_", ""): n for t, n in failed.items()}


def process(r: dict) -> dict[str, bool]:
    """Did the agent follow the studio's procedure? Reported beside the outcome score."""
    versions = r.get("versions", [])  # newest first
    approved = [i for i, v in enumerate(versions) if v.startswith("user: Contract approved")]
    designed = [i for i, v in enumerate(versions) if v.startswith("user: Design approved")]
    agent_work = [i for i, v in enumerate(versions) if v.startswith("agent:")]
    tools = [t.split(" · ", 1)[1] for t in r.get("tools", []) if t.startswith("Done · ")]
    built = bool(r.get("node_ids")) or any(t in ("write", "edit") for t in tools)
    checks = {
        "contract": bool(approved) or not built,
        # Contract, then design, then the agent's builds (newest first: smaller index is later).
        "plan_first": not agent_work
        or (bool(approved) and bool(designed) and max(agent_work) < max(designed) < max(approved)),
        "in_stages": not r.get("loose"),
        "aligned": (r.get("contract") or {}).get("aligned", not built),
        "ran": "success" in r.get("run_statuses", []),
        "gates": "comfy_check_gates" in tools,
        # The design pipeline: an HLD with how/why/pattern per step, an API by design, examples run.
        "designed": bool((r.get("contract") or {}).get("designed")),
        "api": bool((r.get("contract") or {}).get("api")),
        "accepted": any(
            v.startswith("agent: Acceptance") and "examples passed" in v for v in versions
        ),
        # The workflow's CI ran fully green (design incl. the API standard, Checks, examples).
        "checks_green": any(
            v.startswith("agent: Checks · ") and "✗" not in v and "○" not in v for v in versions
        ),
        # Loop engineering: improvements as measured, journaled iterations.
        "looped": any(v.startswith("agent: Loop ") for v in versions),
    }
    return checks


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--provider", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--only", nargs="*")
    parser.add_argument("--agent", choices=["pi", "claude_code"], default="pi")
    parser.add_argument(
        "--agent-model", default="haiku", help="Claude Code's model (with --agent claude_code)"
    )
    parser.add_argument(
        "--tasks", default=str(HERE / "tasks.json"), help="task file (default: tasks.json)"
    )
    parser.add_argument(
        "--studio",
        action="store_true",
        help="run in the user's studio on 8188 (visible there: saved as 'Eval - <task> <time>')",
    )
    parser.add_argument(
        "--publish",
        action="store_true",
        help="copy each finished workflow (with its history and data) into the studio on 8188",
    )
    args = parser.parse_args()
    tasks = [
        t for t in json.loads(Path(args.tasks).read_text()) if not args.only or t["id"] in args.only
    ]
    key_env = provider_credential_environment(args.provider)
    if not key_env:
        sys.exit(f"No Studio Keychain credential saved for {args.provider}.")
    config = {
        "version": 1,
        "provider": args.provider,
        "model": args.model,
        "thinking_level": "low",
        "credential_source": "studio_keychain",
    }
    if args.agent == "claude_code":  # the chat agent only; Structured LLM nodes keep the Pi model
        config = {"version": 1, "agent": "claude_code", "model": args.agent_model}
    out = Path(tempfile.mkdtemp(prefix="aws-eval-"))
    env = {
        "PI_PROVIDER": args.provider,
        "PI_MODEL": args.model,
        "PI_REASONING_LEVEL": "low",
        **key_env,
    }
    results = []
    for task in tasks:
        with Studio() if args.studio else isolated(ComfyFixture(env)) as comfy:
            key_dir = out / f"{task['id']}-key"
            if task.get("template"):  # the template's own export nodes write the data and key
                key_dir = comfy.output / "nayose_benchmark" / task["dataset"] / "key"
            elif task.get("dataset") == "nayose":
                stats = nayose_data.generate(comfy.input / "nayose", key_dir)
                print(
                    f"     data: {stats['policies']} policies, {stats['claims']} claims, "
                    f"{stats['payments']} payments, {stats['mentions']} mentions",
                    flush=True,
                )
            for rel, content in (task.get("input_files") or {}).items():
                target = comfy.input / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content, encoding="utf-8")
            started = time.time()
            subprocess.run(
                ["node", str(HERE / "eval.cjs")],
                cwd=REPO,
                timeout=task.get("timeout_minutes", 7) * 60 + 180,
                check=False,
                env={
                    **os.environ,
                    "BASE": comfy.url,
                    "OUT": str(out),
                    "TASK": json.dumps(task),
                    "PI_CONFIG": json.dumps(config),
                    **(
                        {"SAVE_AS": f"Eval - {task['id']} {time.strftime('%Y%m%d-%H%M')}"}
                        if args.studio
                        else {}
                    ),
                },
            )
            result = json.loads((out / f"{task['id']}.json").read_text())
            # Keep what explains a failure; the instance is deleted on exit.
            if not args.studio:
                shutil.copy(comfy.log, out / f"{task['id']}-comfy.log")
                projects = comfy.user / "agent-projects"
                if projects.exists():
                    shutil.copytree(projects, out / f"{task['id']}-projects", symlinks=True)
            if task.get("dataset") and result.get("workflow_id"):
                # A tool's run folder or a Write table node's file: whichever this run wrote last.
                found = sorted(
                    (
                        p
                        # Saved like ComfyUI's save nodes: nayose_result_00001_.csv, … (or a tool's own file).
                        for p in comfy.output.rglob("nayose_result*.csv")
                        if p.stat().st_mtime >= started
                    ),
                    key=lambda p: p.stat().st_mtime,
                )
                if found:
                    shutil.copy(found[-1], out / f"{task['id']}-result.csv")
                    result["nayose"] = nayose_score.score(key_dir, found[-1])
            if task.get("expect_file"):
                result["file_check"] = check_file(comfy.output, task["expect_file"], started)
            if args.publish and not args.studio:
                result["published"] = publish(comfy, task, result)
        passed, failures = score(task, result)
        steps = process(result)
        results.append({**result, "passed": passed, "failures": failures, "process": steps})
        print(
            f"{'PASS' if passed else 'FAIL'} {task['id']} ({result.get('seconds')}s) {'; '.join(failures)}",
            flush=True,
        )
        contract = result.get("contract") or {}
        marks = " · ".join(f"{name} {'✓' if ok else '✗'}" for name, ok in steps.items())
        print(f"     process: {marks} · {contract.get('progress', 'no contract')}", flush=True)
        tries = retries(result)
        results[-1]["retries"] = tries
        if tries:
            first = "✓" if not any(tries.values()) else "✗"
            print(
                f"     retries: first_try {first} · "
                + " · ".join(f"{t} {n}" for t, n in tries.items()),
                flush=True,
            )
        for drift in contract.get("drift") or []:
            print(f"     drift: {drift}", flush=True)
        if result.get("nayose"):
            print(
                "     " + nayose_score.text(result["nayose"]).replace("\n", "\n     "), flush=True
            )
        for note in result.get("published") or []:
            print(f"     published: {note}", flush=True)
    (out / "summary.json").write_text(json.dumps(results, indent=1))
    print(f"{sum(r['passed'] for r in results)}/{len(results)} passed · details: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
