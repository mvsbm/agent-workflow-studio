"""Pi-backed structured agent node."""

from __future__ import annotations

from typing import Any

from comfy_api.latest import io

from .llm_harness import LLMRunHarness
from .node_progress import node_id_for
from .pi_cli import complete_json
from .pi_model_config import PiModelConfigSocket, validate_pi_model_config


class PiStructuredAgent(io.ComfyNode):
    @classmethod
    def define_schema(cls) -> io.Schema:
        return io.Schema(
            node_id="AgentWorkflowPiStructuredAgent",
            display_name="Pi CLI · Structured LLM",
            category="agent_workflow_studio/llm",
            search_aliases=["LLM", "Pi CLI", "agent", "chat completion", "JSON output"],
            description=(
                "Send connected prompt text to the configured Pi CLI model. "
                "The model fills the output schema through a typed result tool; "
                "all action tools are disabled."
            ),
            inputs=[
                io.String.Input(
                    "prompt",
                    display_name="Prompt",
                    multiline=True,
                    dynamic_prompts=True,
                    default="",
                    tooltip=(
                        "Connect a ComfyUI text/prompt node here, or type directly "
                        "and connect downstream."
                    ),
                ),
                io.String.Input(
                    "output_schema_json",
                    display_name="Output schema JSON",
                    multiline=True,
                    dynamic_prompts=False,
                    default=(
                        '{"type":"object","properties":{"answer":{"type":"string"}},'
                        '"required":["answer"],"additionalProperties":false}'
                    ),
                    tooltip="JSON Schema for the required object output. Remote $ref is disabled.",
                ),
                io.Float.Input(
                    "temperature",
                    display_name="Temperature",
                    default=0.0,
                    min=0.0,
                    max=2.0,
                    step=0.05,
                    tooltip=(
                        "Retained for workflow compatibility; Pi CLI uses model-level "
                        "generation settings."
                    ),
                ),
                io.Int.Input(
                    "max_tokens",
                    display_name="Max tokens",
                    default=2048,
                    min=1,
                    max=32768,
                    tooltip="Advisory output-size target; Pi CLI RPC has no per-request token cap.",
                ),
                io.String.Input(
                    "system_prompt",
                    display_name="System prompt",
                    multiline=True,
                    optional=True,
                    default="",
                    tooltip="Optional task instructions; private reasoning remains hidden.",
                ),
                PiModelConfigSocket.Input(
                    "pi_config",
                    display_name="Pi model config",
                    optional=True,
                    tooltip=(
                        "Connect Pi CLI · Model Config to select provider and model explicitly."
                    ),
                ),
            ],
            outputs=[io.Dict.Output(display_name="validated_result")],
            hidden=[io.Hidden.unique_id],
            is_output_node=True,
        )

    @classmethod
    def fingerprint_inputs(cls, **_kwargs: Any) -> float:
        # Provider requests are intentional side effects and must never be cached.
        return float("nan")

    @classmethod
    def execute(
        cls,
        prompt: str,
        output_schema_json: str,
        temperature: float,
        max_tokens: int,
        system_prompt: str = "",
        pi_config: Any = None,
    ) -> io.NodeOutput:
        unique_id = node_id_for(cls)
        result = LLMRunHarness(unique_id, "Pi CLI Structured Agent").run(
            complete_json,
            prompt,
            output_schema_json,
            temperature=temperature,
            max_tokens=max_tokens,
            system_prompt=system_prompt,
            pi_config=(validate_pi_model_config(pi_config) if pi_config is not None else None),
        )
        summary = (
            "STRUCTURED RESULT · SCHEMA VALIDATED\n"
            f"Top-level fields: {len(result)}\n"
            "Values remain on the typed DICT socket."
        )
        return io.NodeOutput(result, ui={"text": (summary,)})


__all__ = ["PiStructuredAgent"]
