"""Each node's help page for ComfyUI's own Info tab, generated from its V3 schema: what it does, each
input and output with its type and tooltip, and the source file that runs it. ComfyUI serves them
from this pack's web/docs/<NodeName>.md (the frontend asks /extensions/<pack>/docs/<NodeName>.md).

Regenerate after changing a node:  (from the ComfyUI folder)
    .venv/bin/python -m custom_nodes.agent_workflow_studio.node_docs
tests/unit/test_node_docs.py fails when a page is missing or stale.
"""

from __future__ import annotations

import inspect
from pathlib import Path

DOCS = Path(__file__).resolve().parent / "web" / "docs"


def _cell(text: object) -> str:
    return " ".join(str(text or "").split()).replace("|", "\\|")


def render(node: type) -> str:
    """The Markdown page for one node class."""
    schema = node.define_schema()
    source = Path(inspect.getsourcefile(node)).name
    lines = [f"# {schema.display_name or schema.node_id}", "", schema.description or "", ""]
    if schema.inputs:
        lines += ["## Inputs", "", "| Input | Type | What it is |", "| --- | --- | --- |"]
        for item in schema.inputs:
            kind = item.get_io_type() if hasattr(item, "get_io_type") else ""
            optional = " (optional)" if getattr(item, "optional", False) else ""
            default = getattr(item, "default", None)
            shown = f" Default: `{_cell(default)}`." if default not in (None, "") else ""
            lines.append(
                f"| {_cell(item.display_name or item.id)}{optional} | `{_cell(kind)}` | {_cell(item.tooltip)}{shown} |"
            )
        lines.append("")
    if schema.outputs:
        lines += ["## Outputs", "", "| Output | Type | What it is |", "| --- | --- | --- |"]
        for item in schema.outputs:
            kind = item.get_io_type() if hasattr(item, "get_io_type") else ""
            lines.append(
                f"| {_cell(item.display_name or item.id)} | `{_cell(kind)}` | {_cell(item.tooltip)} |"
            )
        lines.append("")
    lines += [
        "## Where it runs",
        "",
        f"`{schema.node_id}` in `{source}` (category `{schema.category}`).",
        "",
    ]
    return "\n".join(lines)


async def _nodes() -> list[type]:
    from . import AgentWorkflowStudioExtension

    return await AgentWorkflowStudioExtension().get_node_list()


def pages() -> dict[str, str]:
    """NodeName → its page, for every node this pack registers."""
    import asyncio

    return {node.define_schema().node_id: render(node) for node in asyncio.run(_nodes())}


if __name__ == "__main__":
    DOCS.mkdir(parents=True, exist_ok=True)
    wanted = pages()
    for stale in DOCS.glob("*.md"):
        if stale.stem not in wanted:
            stale.unlink()
    for name, text in wanted.items():
        (DOCS / f"{name}.md").write_text(text, encoding="utf-8")
    print(f"Wrote {len(wanted)} node help pages to {DOCS}")
