"""ComfyUI graph contract for selecting a Pi provider and model."""

from __future__ import annotations

import os
import re
from typing import Any

from comfy_api.latest import io

PI_MODEL_CONFIG_TYPE = "PI_MODEL_CONFIG_V1"
PI_AUTH_SOURCES = ("pi_managed", "studio_keychain")
PI_THINKING_LEVELS = ("off", "minimal", "low", "medium", "high", "xhigh", "max")
_PI_MODEL_ID = re.compile(r"[A-Za-z0-9_./:+-]{1,160}\Z")
PiModelConfigSocket = io.Custom(PI_MODEL_CONFIG_TYPE)


def validate_pi_model_config(value: Any) -> dict[str, str | int]:
    if (
        not isinstance(value, dict)
        or type(value.get("version")) is not int
        or value.get("version") != 1
    ):
        raise TypeError("pi_config must be a PI_MODEL_CONFIG_V1 object")
    provider = _identifier(value.get("provider"), "provider")
    model = _identifier(value.get("model"), "model")
    thinking = value.get("thinking_level")
    if thinking not in PI_THINKING_LEVELS:
        raise ValueError("pi_config thinking_level is not supported")
    credential_source = value.get("credential_source")
    if credential_source not in PI_AUTH_SOURCES:
        raise ValueError("pi_config credential_source is not supported")
    return {
        "version": 1,
        "provider": provider,
        "model": model,
        "thinking_level": thinking,
        "credential_source": credential_source,
    }


def _identifier(value: Any, label: str) -> str:
    if not isinstance(value, str) or not _PI_MODEL_ID.fullmatch(value.strip()):
        raise ValueError(f"pi_config {label} must be a valid Pi ID")
    return value.strip()


def _default_thinking_level() -> str:
    value = os.environ.get("PI_REASONING_LEVEL", "medium")
    return value if value in PI_THINKING_LEVELS else "medium"


class AgentWorkflowPiModelConfig(io.ComfyNode):
    @classmethod
    def define_schema(cls) -> io.Schema:
        return io.Schema(
            node_id="AgentWorkflowPiModelConfig",
            display_name="Pi CLI · Model Config",
            category="agent_workflow_studio/llm",
            search_aliases=["Pi", "provider", "model", "thinking", "credentials"],
            description=(
                "Select the Pi provider, model, thinking level, and credential source "
                "for connected LLM nodes. Credentials are never workflow data."
            ),
            inputs=[
                io.String.Input(
                    "provider",
                    display_name="Provider ID",
                    default=os.environ.get("PI_PROVIDER", ""),
                    tooltip=(
                        "Provider ID from the Pi model catalog. Use the Pi Credentials "
                        "menu to browse providers."
                    ),
                ),
                io.String.Input(
                    "model",
                    display_name="Model ID",
                    default=os.environ.get("PI_MODEL", ""),
                    tooltip="Exact model ID from the Pi model catalog.",
                ),
                io.Combo.Input(
                    "thinking_level",
                    display_name="Thinking level",
                    options=list(PI_THINKING_LEVELS),
                    default=_default_thinking_level(),
                    tooltip="Select the model's configured reasoning effort.",
                ),
                io.Combo.Input(
                    "credential_source",
                    display_name="Credential source",
                    options=list(PI_AUTH_SOURCES),
                    default="pi_managed",
                    tooltip=(
                        "Studio Keychain resolves the selected provider's credential "
                        "environment variables; Pi-managed uses Pi's own auth "
                        "configuration."
                    ),
                ),
            ],
            outputs=[PiModelConfigSocket.Output(display_name="pi_model_config")],
        )

    @classmethod
    def execute(
        cls,
        provider: str,
        model: str,
        thinking_level: str,
        credential_source: str,
    ) -> io.NodeOutput:
        config = validate_pi_model_config(
            {
                "version": 1,
                "provider": provider,
                "model": model,
                "thinking_level": thinking_level,
                "credential_source": credential_source,
            }
        )
        summary = (
            "PI MODEL CONFIG · VALID\n"
            f"Provider: {config['provider']}\n"
            f"Model: {config['model']}\n"
            f"Thinking: {config['thinking_level']}\n"
            f"Credential source: {config['credential_source']}"
        )
        return io.NodeOutput(config, ui={"text": (summary,)})


__all__ = [
    "PI_AUTH_SOURCES",
    "PI_MODEL_CONFIG_TYPE",
    "PI_THINKING_LEVELS",
    "AgentWorkflowPiModelConfig",
    "PiModelConfigSocket",
    "validate_pi_model_config",
]
