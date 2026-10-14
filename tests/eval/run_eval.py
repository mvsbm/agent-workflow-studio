"""Evaluate the real Pi agent on a few requests. Makes real provider calls (costs money).

Usage (from the repo root):
  ~/.local/share/agent-workflow-studio/ComfyUI/.venv/bin/python tests/eval/run_eval.py \
      --provider opencode-go --model deepseek-v4-flash [--only id ...]

Each task runs in a fresh isolated ComfyUI. The chat uses Studio Keychain credentials;
the provider's saved key is also passed to that test instance so the Structured LLM
node uses the same model. Scores come from evidence: outputs, canvas and versions.
"""

from __future__ import annotations

import argparse
import json
import os
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
    if "success" not in r.get("run_statuses", []):
        failures.append("no successful native run")
    if task.get("dataset"):
        f1 = ((r.get("nayose") or {}).get("pairwise") or {}).get("f1")
        if f1 is None:
            failures.append("no nayose_result.csv written by this run")
        elif f1 < task.get("expect_pairwise_f1", 0):
            failures.append(f"pairwise F1 {f1} below {task['expect_pairwise_f1']}")
    return not failures, failures


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


def process(r: dict) -> dict[str, bool]:
    """Did the agent follow the studio's procedure? Reported beside the outcome score."""
    versions = r.get("versions", [])  # newest first
    approved = [i for i, v in enumerate(versions) if v.startswith("user: Contract approved")]
    agent_work = [i for i, v in enumerate(versions) if v.startswith("agent:")]
    tools = [t.split(" · ", 1)[1] for t in r.get("tools", []) if t.startswith("Done · ")]
    built = bool(r.get("node_ids")) or any(t in ("write", "edit") for t in tools)
    checks = {
        "contract": bool(approved) or not built,
        "plan_first": not agent_work or (bool(approved) and max(agent_work) < max(approved)),
        "tagged": not r.get("untagged"),
        "aligned": (r.get("contract") or {}).get("aligned", not built),
        "ran": "success" in r.get("run_statuses", []),
        "gates": "comfy_check_gates" in tools,
    }
    return checks


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--provider", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--only", nargs="*")
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
                        for p in comfy.output.rglob("nayose_result.csv")
                        if p.stat().st_mtime >= started
                    ),
                    key=lambda p: p.stat().st_mtime,
                )
                if found:
                    shutil.copy(found[-1], out / f"{task['id']}-result.csv")
                    result["nayose"] = nayose_score.score(key_dir, found[-1])
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
