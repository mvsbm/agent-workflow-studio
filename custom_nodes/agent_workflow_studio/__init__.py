"""Native ComfyUI V3 custom-node extension for Agent Workflow Studio."""

from __future__ import annotations

from comfy_api.latest import ComfyExtension
from typing_extensions import override

from .nodes import (
    NayoseGenerateDataset,
    NayoseGenerationControls,
    NayosePreviewGeneration,
    NayoseTrainModel,
    NayoseValidateAgentControls,
    OpenCodeGoStructuredAgent,
)

WEB_DIRECTORY = "./web"


class AgentWorkflowStudioExtension(ComfyExtension):
    @override
    async def get_node_list(self) -> list[type]:
        return [
            NayoseGenerationControls,
            NayosePreviewGeneration,
            NayoseGenerateDataset,
            NayoseTrainModel,
            NayoseValidateAgentControls,
            OpenCodeGoStructuredAgent,
        ]


async def comfy_entrypoint() -> AgentWorkflowStudioExtension:
    from .routes import install_routes

    install_routes()
    return AgentWorkflowStudioExtension()


__all__ = [
    "AgentWorkflowStudioExtension",
    "NayoseGenerateDataset",
    "NayoseGenerationControls",
    "NayosePreviewGeneration",
    "NayoseTrainModel",
    "NayoseValidateAgentControls",
    "OpenCodeGoStructuredAgent",
    "comfy_entrypoint",
]
