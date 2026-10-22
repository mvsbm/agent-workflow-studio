"""Agent Workflow Studio: a Pi-backed agent that builds and edits ComfyUI workflows."""

from __future__ import annotations

from typing import override

from comfy_api.latest import ComfyExtension

from .agent_node import PiStructuredAgent
from .agent_tool_node import AgentToolNode
from .check_node import NODES as CHECK_NODES
from .contacts_nodes import NODES as CONTACTS_NODES
from .decision_rules import DecisionRules
from .eda_node import NODES as EDA_NODES
from .fuzzy_node import FuzzyNode
from .matching_nodes import NODES as MATCHING_NODES
from .nayose_nodes import NODES as NAYOSE_NODES
from .pi_model_config import AgentWorkflowPiModelConfig
from .semantic_nodes import NODES as SEMANTIC_NODES
from .synth_nodes import NODES as SYNTH_NODES
from .table_calc import NODES as CALC_NODES
from .table_nodes import NODES as TABLE_NODES

WEB_DIRECTORY = "./web"


class AgentWorkflowStudioExtension(ComfyExtension):
    @override
    async def get_node_list(self) -> list[type]:
        return [
            AgentWorkflowPiModelConfig,
            PiStructuredAgent,
            FuzzyNode,
            AgentToolNode,
            DecisionRules,
            *CHECK_NODES,
            *EDA_NODES,
            *TABLE_NODES,
            *CALC_NODES,
            *SYNTH_NODES,
            *MATCHING_NODES,
            *CONTACTS_NODES,
            *SEMANTIC_NODES,
            *NAYOSE_NODES,
        ]


async def comfy_entrypoint() -> AgentWorkflowStudioExtension:
    from .pi_chat import install_pi_chat_routes
    from .project_repo import install_project_routes
    from .routes import install_routes
    from .studio_defaults import apply_to_default_user
    from .workflow_api import install_api_routes

    apply_to_default_user()
    install_routes()
    install_pi_chat_routes()
    install_project_routes()
    install_api_routes()
    return AgentWorkflowStudioExtension()


__all__ = [
    "AgentToolNode",
    "AgentWorkflowPiModelConfig",
    "AgentWorkflowStudioExtension",
    "FuzzyNode",
    "PiStructuredAgent",
    "comfy_entrypoint",
]
