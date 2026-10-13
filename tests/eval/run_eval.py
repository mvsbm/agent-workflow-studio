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
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "e2e"))
from fixture import COMFY, REPO, ComfyFixture  # noqa: E402

sys.path.insert(0, str(COMFY))
from custom_nodes.agent_workflow_studio.pi_auth import provider_credential_environment  # noqa: E402


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
    if "success" not in r.get("run_statuses", []):
        failures.append("no successful native run")
    return not failures, failures


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--provider", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--only", nargs="*")
    args = parser.parse_args()
    tasks = [
        t
        for t in json.loads((HERE / "tasks.json").read_text())
        if not args.only or t["id"] in args.only
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
        with ComfyFixture(env) as comfy:
            subprocess.run(
                ["node", str(HERE / "eval.cjs")],
                cwd=REPO,
                timeout=600,
                check=False,
                env={
                    **os.environ,
                    "BASE": comfy.url,
                    "OUT": str(out),
                    "TASK": json.dumps(task),
                    "PI_CONFIG": json.dumps(config),
                },
            )
        result = json.loads((out / f"{task['id']}.json").read_text())
        passed, failures = score(task, result)
        results.append({**result, "passed": passed, "failures": failures})
        print(
            f"{'PASS' if passed else 'FAIL'} {task['id']} ({result.get('seconds')}s) {'; '.join(failures)}",
            flush=True,
        )
    (out / "summary.json").write_text(json.dumps(results, indent=1))
    print(f"{sum(r['passed'] for r in results)}/{len(results)} passed · details: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
