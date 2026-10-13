"""Pi-backed structured agent node."""

from __future__ import annotations

import json
import time
from typing import Any

from comfy_api.latest import io

from .llm_harness import LLMRunHarness, _failure_category
from .node_progress import node_id_for
from .pi_cli import _build_command, complete_json
from .pi_model_config import PiModelConfigSocket, validate_pi_model_config
from .project_repo import ProjectRepo, projects_root


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
            hidden=[io.Hidden.unique_id, io.Hidden.extra_pnginfo],
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
        config = validate_pi_model_config(pi_config) if pi_config is not None else None
        harness = LLMRunHarness(unique_id, "Pi CLI Structured Agent")
        started = time.time()
        result, error = None, None
        try:
            result = harness.run(
                complete_json,
                prompt,
                output_schema_json,
                temperature=temperature,
                max_tokens=max_tokens,
                system_prompt=system_prompt,
                pi_config=config,
            )
        except Exception as exc:  # recorded below, then re-raised unchanged
            error = exc
        recorded = _record_run(
            cls,
            unique_id,
            harness,
            started,
            {"prompt": prompt, "system_prompt": system_prompt, "output_schema": output_schema_json},
            config,
            result,
            error,
        )
        if error is not None:
            raise error
        summary = (
            "STRUCTURED RESULT · SCHEMA VALIDATED\n"
            f"Top-level fields: {len(result)}\n"
            f"Values remain on the typed DICT socket.\n{recorded}"
        )
        return io.NodeOutput(result, ui={"text": (summary,)})


def _record_run(cls, unique_id, harness, started, inputs, config, result, error) -> str:
    """Commit an auditable record of this run, with the workflow that ran, to its history."""
    hidden = getattr(cls, "hidden", None)
    workflow = (getattr(hidden, "extra_pnginfo", None) or {}).get("workflow")
    if not isinstance(workflow, dict) or not isinstance(workflow.get("id"), str):
        return "Run record: not saved (no workflow attached to this run)."
    try:
        _, provider, model, thinking = _build_command(config)
    except Exception:  # model resolution is best-effort metadata
        provider = model = thinking = None
    try:
        inputs = {**inputs, "output_schema": json.loads(inputs["output_schema"])}
    except ValueError:
        pass
    status = "failed" if error is not None else "succeeded"
    record = {
        "version": 1,
        "kind": "pi_structured_llm",
        "status": status,
        "node": {"id": unique_id, "class_type": "AgentWorkflowPiStructuredAgent"},
        "started": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(started)),
        "duration_seconds": round(time.time() - started, 3),
        # Credentials never appear here: config holds only provider/model/source names.
        "model": {
            "provider": provider,
            "model": model,
            "thinking_level": thinking,
            "credential_source": (config or {}).get("credential_source", "pi_managed"),
        },
        "input": inputs,
        "output": result,
        # Error text can carry provider details; keep only the category.
        "error": _failure_category(error) if error is not None else None,
        "telemetry": {
            "finish_reason": harness.finish_reason,
            "usage": harness.usage,
            "structured_output_validated": harness.structured_output_validated,
            "first_answer_after_seconds": harness.first_answer_after_seconds,
        },
    }
    try:
        sha = ProjectRepo(projects_root(), workflow["id"]).add_record(
            record, f"Run · Pi CLI Structured LLM #{unique_id} · {status}", workflow
        )
    except (OSError, RuntimeError, ValueError) as exc:
        return f"Run record: not saved ({type(exc).__name__})."
    return f"Run record: version {sha[:8]}."


__all__ = ["PiStructuredAgent"]
