"""ComfyUI V3 nodes for local Nayose workflows and OpenCode Go agents."""

from __future__ import annotations

import json
from typing import Any

from comfy_api.latest import io

from .credential_store import CredentialStoreError, get_credential
from .nayose_api import NayoseAPI
from .opencode_go import complete_json

NayoseGenerationConfig = io.Custom("NAYOSE_GENERATION_CONFIG")
NayoseGenerationPreview = io.Custom("NAYOSE_GENERATION_PREVIEW")
NayoseGeneration = io.Custom("NAYOSE_GENERATION")
NayoseModelRun = io.Custom("NAYOSE_MODEL_RUN")


class NayoseGenerationControls(io.ComfyNode):
    @classmethod
    def define_schema(cls) -> io.Schema:
        return io.Schema(
            node_id="AgentWorkflowNayoseGenerationControls",
            display_name="Nayose · Generation Controls",
            category="agent_workflow_studio/nayose",
            description="Edit and pass validated-by-Nayose generation controls.",
            inputs=[
                io.String.Input(
                    "controls_json",
                    multiline=True,
                    dynamic_prompts=True,
                    default='{"profile":"smoke","difficulty":"realistic","seed":42}',
                    tooltip="Nayose generation request. This editable text input can also be connected.",
                )
            ],
            outputs=[NayoseGenerationConfig.Output(display_name="generation_config")],
        )

    @classmethod
    def execute(cls, controls_json: str) -> io.NodeOutput:
        try:
            controls = json.loads(controls_json)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid generation controls JSON: {exc.msg}") from exc
        if not isinstance(controls, dict):
            raise TypeError("generation controls must be a JSON object")
        return io.NodeOutput(controls)


class NayoseValidateAgentControls(io.ComfyNode):
    @classmethod
    def define_schema(cls) -> io.Schema:
        return io.Schema(
            node_id="AgentWorkflowNayoseValidateAgentControls",
            display_name="Nayose · Validate Agent Controls",
            category="agent_workflow_studio/nayose",
            description=(
                "Pass schema-checked LLM JSON through Nayose's domain preview gate "
                "and emit a typed generation-config socket."
            ),
            inputs=[io.Dict.Input("agent_result")],
            outputs=[NayoseGenerationConfig.Output(display_name="generation_config")],
        )

    @classmethod
    def execute(cls, agent_result: Any) -> io.NodeOutput:
        controls = _require_object(agent_result, "agent result")
        preview = NayoseAPI.from_environment().preview_generation(controls)
        return io.NodeOutput(
            controls,
            ui={
                "text": (
                    json.dumps(
                        {"config_hash": preview.get("config_hash")}, sort_keys=True
                    ),
                )
            },
        )


class NayosePreviewGeneration(io.ComfyNode):
    @classmethod
    def define_schema(cls) -> io.Schema:
        return io.Schema(
            node_id="AgentWorkflowNayosePreviewGeneration",
            display_name="Nayose · Preview Generation",
            category="agent_workflow_studio/nayose",
            description="Ask Nayose to validate controls and estimate generation without side effects.",
            inputs=[NayoseGenerationConfig.Input("generation_config")],
            outputs=[NayoseGenerationPreview.Output(display_name="preview")],
            is_output_node=True,
        )

    @classmethod
    def execute(cls, generation_config: Any) -> io.NodeOutput:
        controls = _require_object(generation_config, "generation config")
        result = NayoseAPI.from_environment().preview_generation(controls)
        summary = {
            "config_hash": result.get("config_hash"),
            "counts": result.get("counts", {}),
            "warnings": result.get("warnings", []),
        }
        return io.NodeOutput(
            result, ui={"text": (json.dumps(summary, ensure_ascii=False),)}
        )


class NayoseGenerateDataset(io.ComfyNode):
    @classmethod
    def define_schema(cls) -> io.Schema:
        return io.Schema(
            node_id="AgentWorkflowNayoseGenerateDataset",
            display_name="Nayose · Generate Dataset",
            category="agent_workflow_studio/nayose",
            description="Generate one immutable, fingerprinted Nayose dataset.",
            inputs=[NayoseGenerationConfig.Input("generation_config")],
            outputs=[NayoseGeneration.Output(display_name="generation")],
            is_output_node=True,
        )

    @classmethod
    def fingerprint_inputs(cls, **_kwargs: Any) -> float:
        return float("nan")

    @classmethod
    def execute(cls, generation_config: Any) -> io.NodeOutput:
        controls = _require_object(generation_config, "generation config")
        api = NayoseAPI.from_environment()
        api.preview_generation(controls)
        result = api.run_admin_job("generate", controls)
        artifact = {
            "generation_id": result.get("generation"),
            "fingerprint": result.get("fingerprint"),
            "records": result.get("records"),
        }
        if not artifact["generation_id"] or not artifact["fingerprint"]:
            raise RuntimeError(
                "Nayose generate job returned an incomplete artifact reference"
            )
        return io.NodeOutput(
            artifact,
            ui={"text": (json.dumps(artifact, ensure_ascii=False, sort_keys=True),)},
        )


class NayoseTrainModel(io.ComfyNode):
    @classmethod
    def define_schema(cls) -> io.Schema:
        return io.Schema(
            node_id="AgentWorkflowNayoseTrainModel",
            display_name="Nayose · Train Model",
            category="agent_workflow_studio/nayose",
            description="Train against the exact connected immutable generation.",
            inputs=[
                NayoseGeneration.Input("generation"),
                io.Int.Input("seed", default=42, min=0),
                io.Combo.Input(
                    "device", options=["auto", "cpu", "cuda", "mps"], default="auto"
                ),
                io.String.Input("model_family", default="off"),
            ],
            outputs=[NayoseModelRun.Output(display_name="model_run")],
            is_output_node=True,
        )

    @classmethod
    def fingerprint_inputs(cls, **_kwargs: Any) -> float:
        return float("nan")

    @classmethod
    def execute(
        cls, generation: Any, seed: int, device: str, model_family: str
    ) -> io.NodeOutput:
        generation_ref = _require_object(generation, "generation reference")
        if device == "auto":
            device = _default_device()
        generation_id = generation_ref.get("generation_id")
        fingerprint = generation_ref.get("fingerprint")
        if not isinstance(generation_id, str) or not isinstance(fingerprint, str):
            raise TypeError("generation reference is missing its ID or fingerprint")
        result = NayoseAPI.from_environment().run_admin_job(
            "train",
            {
                "generation_id": generation_id,
                "seed": seed,
                "device": device,
                "model_family": model_family,
            },
        )
        if result.get("fingerprint") != fingerprint:
            raise RuntimeError(
                "trained model fingerprint does not match the input generation"
            )
        artifact = {
            "run_id": result.get("run"),
            "fingerprint": result.get("fingerprint"),
            "metrics": result.get("metrics", {}),
        }
        if not artifact["run_id"]:
            raise RuntimeError(
                "Nayose train job returned an incomplete model reference"
            )
        return io.NodeOutput(
            artifact,
            ui={"text": (json.dumps(artifact, ensure_ascii=False, sort_keys=True),)},
        )


class OpenCodeGoStructuredAgent(io.ComfyNode):
    @classmethod
    def define_schema(cls) -> io.Schema:
        return io.Schema(
            node_id="AgentWorkflowOpenCodeGoStructuredAgent",
            display_name="OpenCode Go · Structured LLM",
            category="agent_workflow_studio/llm",
            search_aliases=["LLM", "OpenCode", "chat completion", "JSON output"],
            description=(
                "Send connected prompt text to OpenCode Go MiMo-V2.6-Flash. "
                "Only schema-valid JSON is emitted as a native Dict socket."
            ),
            inputs=[
                io.String.Input(
                    "prompt",
                    multiline=True,
                    dynamic_prompts=True,
                    default="",
                    tooltip="Connect a ComfyUI text/prompt node here, or type directly and connect downstream.",
                ),
                io.String.Input(
                    "output_schema_json",
                    multiline=True,
                    default='{"type":"object","properties":{"answer":{"type":"string"}},"required":["answer"],"additionalProperties":false}',
                    tooltip="JSON Schema for the required object output. Remote $ref is disabled.",
                ),
                io.Float.Input("temperature", default=0.0, min=0.0, max=2.0, step=0.05),
                io.Int.Input("max_tokens", default=2048, min=1, max=32768),
                io.String.Input(
                    "system_prompt", multiline=True, optional=True, default=""
                ),
            ],
            outputs=[io.Dict.Output(display_name="validated_result")],
            is_output_node=True,
        )

    @classmethod
    def fingerprint_inputs(cls, **_kwargs: Any) -> float:
        # Paid remote calls are intentional side effects and must never be cached.
        return float("nan")

    @classmethod
    def execute(
        cls,
        prompt: str,
        output_schema_json: str,
        temperature: float,
        max_tokens: int,
        system_prompt: str = "",
    ) -> io.NodeOutput:
        try:
            key = get_credential()
        except CredentialStoreError as exc:
            raise RuntimeError(str(exc)) from None
        if not key:
            raise RuntimeError("Configure an OpenCode Go API key from the canvas menu")
        result = complete_json(
            prompt,
            output_schema_json,
            key,
            temperature=temperature,
            max_tokens=max_tokens,
            system_prompt=system_prompt,
        )
        return io.NodeOutput(
            result,
            ui={"text": (json.dumps(result, ensure_ascii=False, sort_keys=True),)},
        )


def _default_device() -> str:
    try:
        import torch

        if torch.cuda.is_available():
            return "cuda"
        if torch.backends.mps.is_available():
            return "mps"
    except ImportError:
        pass
    return "cpu"


def _require_object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise TypeError(f"{label} must be a JSON object")
    return value


__all__ = [
    "NayoseGenerateDataset",
    "NayoseGenerationControls",
    "NayosePreviewGeneration",
    "NayoseTrainModel",
    "NayoseValidateAgentControls",
    "OpenCodeGoStructuredAgent",
]
