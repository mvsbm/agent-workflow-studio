"""Store a built workflow's contract natively (intent in extra, each step on its stage, the HLD
sentence as the stage's subgraph description) and tag each stage's nodes with what they realize.

Used by build_reference.cjs after it saves the reference: python native_reference.py <workflow.json>
"""

from __future__ import annotations

import json
import sys

from fixture import COMFY

sys.path.insert(0, str(COMFY))
from custom_nodes.agent_workflow_studio.native_layers import embed, infer_realizes  # noqa: E402
from custom_nodes.agent_workflow_studio.workflow_contract import hld, validate_contract  # noqa: E402


def main(path: str) -> None:
    with open(path, encoding="utf-8") as file:
        workflow = json.load(file)
    contract = validate_contract(workflow["extra"].pop("aws_contract"))  # the builder's joined one
    sentences = {d["id"]: d["description"] for d in hld(contract)}
    workflow = infer_realizes(embed(contract, workflow, sentences))
    with open(path, "w", encoding="utf-8") as file:
        file.write(json.dumps(workflow, indent=1, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    main(sys.argv[1])
