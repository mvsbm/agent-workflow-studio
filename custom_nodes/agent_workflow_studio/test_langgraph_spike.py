import os
import unittest
import uuid
from importlib.metadata import version
from typing import Any, TypedDict
from unittest.mock import patch

SPIKE_ALLOWED = os.environ.get("COMFYUI_LANGGRAPH_SPIKE_ALLOWED") == "1"
CANDIDATE_VERSION = "1.2.12"


class PlanningState(TypedDict):
    base_revision: int
    candidate_ref: str
    status: str


@unittest.skipUnless(
    SPIKE_ALLOWED, "Explicit isolated LangGraph spike approval required"
)
class LangGraphSpikeTests(unittest.TestCase):
    def setUp(self) -> None:
        if version("langgraph") != CANDIDATE_VERSION:
            raise RuntimeError("langgraph_spike_version_mismatch")
        environment = patch.dict(
            os.environ,
            {"LANGSMITH_TRACING": "false", "LANGCHAIN_TRACING_V2": "false"},
            clear=True,
        )
        environment.start()
        self.addCleanup(environment.stop)
        self.network_guards = []
        for target in (
            "socket.socket.connect",
            "socket.socket.connect_ex",
            "socket.getaddrinfo",
            "socket.create_connection",
        ):
            guard = patch(target, side_effect=AssertionError("spike_network_forbidden"))
            self.network_guards.append(guard.start())
            self.addCleanup(guard.stop)

        from langgraph.checkpoint.memory import InMemorySaver
        from langgraph.graph import END, START, StateGraph
        from langgraph.types import Command, interrupt

        self.command = Command
        self.interrupt = interrupt
        self.head_revision = 7
        self.calls = {"draft": 0, "clarify": 0}
        builder = StateGraph(PlanningState)
        builder.add_node("draft", self.draft)
        builder.add_node("clarify", self.clarify)
        builder.add_edge(START, "draft")
        builder.add_edge("draft", "clarify")
        builder.add_edge("clarify", END)
        self.graph = builder.compile(checkpointer=InMemorySaver())
        self.config = {
            "configurable": {"thread_id": uuid.uuid4().hex},
            "recursion_limit": 8,
        }

    def tearDown(self) -> None:
        for guard in self.network_guards:
            guard.assert_not_called()

    def draft(self, state: PlanningState) -> dict[str, str]:
        self.calls["draft"] += 1
        return {"candidate_ref": "fixture-candidate-1", "status": "draft"}

    def clarify(self, state: PlanningState) -> dict[str, str]:
        self.calls["clarify"] += 1
        if state["base_revision"] != self.head_revision:
            return {"status": "stale"}
        response = self.interrupt(
            {"kind": "clarification", "candidate_ref": state["candidate_ref"]}
        )
        self.validate_response(response)
        return {
            "status": "unapproved" if response["decision"] == "continue" else "rejected"
        }

    def validate_response(self, response: Any) -> None:
        if (
            type(response) is not dict
            or set(response) != {"decision"}
            or response["decision"] not in ("continue", "reject")
        ):
            raise ValueError("clarification_response_invalid")

    def start(self) -> dict[str, Any]:
        return self.graph.invoke(
            {"base_revision": 7, "candidate_ref": "", "status": "draft"},
            self.config,
            durability="sync",
        )

    def resume(self, response: Any) -> dict[str, Any]:
        self.validate_response(response)
        return self.graph.invoke(
            self.command(resume=response), self.config, durability="sync"
        )

    def test_interrupted_node_reruns_but_completed_predecessor_does_not(self) -> None:
        self.start()
        self.assertEqual(self.calls, {"draft": 1, "clarify": 1})
        result = self.resume({"decision": "continue"})
        self.assertEqual(self.calls, {"draft": 1, "clarify": 2})
        self.assertEqual(result["status"], "unapproved")
        self.assertNotIn("approved", result)
        self.assertNotIn("approval_token", result)

    def test_checkpoint_holds_data_not_permission(self) -> None:
        self.start()
        values = self.graph.get_state(self.config).values
        self.assertEqual(set(values), {"base_revision", "candidate_ref", "status"})
        self.assertEqual(values["status"], "draft")

    def test_forged_approval_is_rejected_before_framework_resume(self) -> None:
        self.start()
        with self.assertRaisesRegex(ValueError, "clarification_response_invalid"):
            self.resume({"approved": True, "approval_token": "forged"})
        self.assertEqual(self.calls["clarify"], 1)
        self.assertEqual(self.graph.get_state(self.config).values["status"], "draft")

    def test_rejection_never_becomes_execution(self) -> None:
        self.start()
        result = self.resume({"decision": "reject"})
        self.assertEqual(result["status"], "rejected")
        self.assertNotIn("execution", result)

    def test_source_change_blocks_clarification_continuation(self) -> None:
        self.start()
        self.head_revision = 8
        result = self.resume({"decision": "continue"})
        self.assertEqual(result["status"], "stale")
        self.assertNotIn("approved", result)
