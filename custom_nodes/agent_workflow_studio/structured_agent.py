"""Single-step OpenCode Go agent node with validated structured output."""

from __future__ import annotations

import json
from typing import Any

from .credential_store import CredentialStoreError, get_credential
from .opencode_go import complete_json


class OpenCodeGoStructuredAgent:
    @classmethod
    def INPUT_TYPES(cls) -> dict[str, Any]:
        return {
            "required": {
                "task": ("STRING", {"multiline": True, "default": ""}),
                "output_schema_json": (
                    "STRING",
                    {
                        "multiline": True,
                        "default": '{"type":"object","properties":{},"additionalProperties":false}',
                    },
                ),
            }
        }

    RETURN_TYPES = ("AGENT_STRUCTURED_RESULT",)
    RETURN_NAMES = ("validated_result",)
    FUNCTION = "run"
    OUTPUT_NODE = True
    CATEGORY = "agent_workflow_studio/agents"
    DESCRIPTION = (
        "Run a bounded OpenCode Go task. Only JSON output that validates against "
        "the supplied schema is emitted. Configure the API key from the canvas menu."
    )

    @classmethod
    def IS_CHANGED(cls, task: str, output_schema_json: str) -> float:
        # Calls the paid provider; never reuse cached LLM outputs.
        return float("nan")

    def run(self, task: str, output_schema_json: str):
        try:
            key = get_credential()
        except CredentialStoreError as exc:
            raise RuntimeError(str(exc)) from None
        if not key:
            raise RuntimeError("Configure an OpenCode Go API key from the canvas menu")
        result = complete_json(task, output_schema_json, key)
        return {
            "ui": {"text": [json.dumps(result, ensure_ascii=False, sort_keys=True)]},
            "result": (result,),
        }


__all__ = ["OpenCodeGoStructuredAgent"]
