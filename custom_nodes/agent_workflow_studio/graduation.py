"""Graduation: a workflow tool that proved itself becomes a studio node. The trust contract changes
(sandboxed, one workflow → in-process, every workflow), so a person reviews it: this writes the brief
a developer or coding agent works from, and the change goes through the studio's own PR and CI.

The tool already has a node's interface (typed ports from the studio's types) and its tests (its
examples), so the brief carries both over unchanged: the node keeps the port names and kinds, and
each example becomes a unit test. Swapping the tool node for the new node in a workflow is a normal
agent edit, verified by that workflow's Run checks.
"""

from __future__ import annotations

import json

from .standards import port_socket, resolve

# The V3 socket each core socket becomes on the graduated node; a dataset class's socket comes from
# the type standard (standards.custom).
CORE_SOCKETS = {
    "STRING": "io.String",
    "INT": "io.Int",
    "FLOAT": "io.Float",
    "BOOLEAN": "io.Boolean",
    "DICT": "io.Dict",
    "TABLE": 'custom("TABLE")',
}


def socket_code(kind: str) -> str:
    """The V3 socket a port of this registered type becomes (an old name resolved)."""
    socket = port_socket(kind)
    spec = resolve(socket) if socket else None
    if socket in CORE_SOCKETS:
        return CORE_SOCKETS[socket]
    return f'custom("{spec.name}")' if spec else "?"


def stages_running(workflow: dict | None, name: str) -> list[str]:
    """The stages of a saved workflow whose Agent Tool node runs tool `name` ("title (step id)")."""
    definitions = {
        d.get("id"): d for d in ((workflow or {}).get("definitions") or {}).get("subgraphs", [])
    }
    found = []
    for node in (workflow or {}).get("nodes") or []:
        step = (node.get("properties") or {}).get("aws_stage")
        inner = (definitions.get(node.get("type")) or {}).get("nodes") or []
        if step and any(
            n.get("type") == "AgentWorkflowAgentTool"
            and (n.get("widgets_values") or [""])[0] == name
            for n in inner
        ):
            title = node.get("title") or definitions[node["type"]].get("name") or step
            found.append(f"{title} (step `{step}`)")
    return found


def brief(name: str, code: str, manifest: dict, workflow: str, stages: list[str] = ()) -> str:
    """The graduation brief for one approved tool, as Markdown."""

    def ports(side: str) -> str:
        rows = [
            f"| {p['name']} | {p['kind']} | {socket_code(p['kind'])} | {p.get('description', '')} |"
            for p in manifest.get(side) or []
        ]
        return "\n".join(rows) or "| (none) | | | |"

    examples = (
        "\n".join(
            f"- inputs `{json.dumps(e['inputs'], ensure_ascii=False)}` → expect "
            f"`{json.dumps(e['expect'], ensure_ascii=False)}`"
            for e in manifest.get("examples") or []
        )
        or "- (none: write the tests before graduating)"
    )
    return "\n".join(
        [
            f"# Graduate tool `{name}` into a studio node",
            "",
            f"From workflow `{workflow}`, approved code sha256 `{manifest.get('sha256', '')[:12]}`.",
            "",
            f"**What it does:** {manifest.get('description', '')}",
            "",
            f"**Why it was code, not a node:** {manifest.get('why_not_a_node', '(not recorded)')}",
            "",
            "## Interface (keep the names and kinds)",
            "",
            "| Input | Kind | V3 socket | What it is |",
            "| --- | --- | --- | --- |",
            ports("inputs"),
            "",
            "| Output | Kind | V3 socket | What it is |",
            "| --- | --- | --- | --- |",
            ports("outputs"),
            "",
            "## Its tests (each becomes a unit test of the node)",
            "",
            examples,
            "",
            "## Checklist",
            "",
            "1. A V3 node (`io.ComfyNode`, written like this pack's own, e.g. `table_nodes.py`) with the ports above, a "
            "description and a tooltip per port, in the module it belongs to; add it to `NODES`.",
            "2. Its logic from the code below, reviewed for in-process use: it now runs inside "
            "ComfyUI for every workflow, with no sandbox or time limit.",
            "3. `tests/unit`: one test per example above, with a `Verifies:` line.",
            "4. Regenerate the Info-tab docs (`python -m custom_nodes.agent_workflow_studio.node_docs`).",
            "5. Open a PR; merge on green CI.",
            f"6. In `{workflow}`, have the agent rebuild each stage that runs `{name}` with "
            "comfy_build_step, the new node where the Agent Tool node was (same port names, same "
            "parts of the step realized): "
            + (", ".join(stages) or "no stage runs it in the latest version")
            + ". Then Run checks: the workflow's tests must stay green (its tool tests level then "
            "says the workflow has no code of its own).",
            "",
            "## Approved code",
            "",
            "```python",
            code.rstrip(),
            "```",
            "",
        ]
    )
