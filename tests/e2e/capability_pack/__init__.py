"""Test-only nodes for the capability tests: typed sockets, a generic passthrough, a ui string.

Loaded only by tests/e2e/capabilities.py, never in the studio.
"""

from __future__ import annotations

from comfy_api.latest import ComfyExtension, io

Pairs, Decisions, Customers = io.Custom("PAIRS"), io.Custom("DECISIONS"), io.Custom("CUSTOMERS")
T = io.MatchType.Template("t", [Pairs, Decisions, Customers])


class MakePairs(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="CapMakePairs", category="capability", inputs=[], outputs=[Pairs.Output()]
        )

    @classmethod
    def execute(cls):
        return io.NodeOutput({"rows": [{"left": "a", "right": "b"}]})


class Passthrough(io.ComfyNode):
    """A stub: gives back what it gets, typed as what it gets."""

    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="CapPassthrough",
            category="capability",
            inputs=[io.MatchType.Input("value", template=T)],
            outputs=[io.MatchType.Output(template=T)],
        )

    @classmethod
    def execute(cls, value):
        return io.NodeOutput(value)


class TakeCustomers(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="CapTakeCustomers",
            category="capability",
            inputs=[Customers.Input("customers")],
            outputs=[],
        )

    @classmethod
    def execute(cls, customers):
        return io.NodeOutput()


class TakePairsOrDecisions(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="CapTakeUnion",
            category="capability",
            inputs=[io.MultiType.Input("value", types=[Pairs, Decisions])],
            outputs=[],
        )

    @classmethod
    def execute(cls, value):
        return io.NodeOutput()


class Report(io.ComfyNode):
    """An output node (like a Check) whose result must reach the frontend and history."""

    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="CapReport",
            category="capability",
            inputs=[io.MatchType.Input("value", template=T)],
            outputs=[],
            is_output_node=True,
        )

    @classmethod
    def execute(cls, value):
        rows = len((value or {}).get("rows", []))
        return io.NodeOutput(ui={"text": (f"rows {rows}",), "bare": "abc"})


class CapabilityExtension(ComfyExtension):
    async def get_node_list(self):
        return [MakePairs, Passthrough, TakeCustomers, TakePairsOrDecisions, Report]


async def comfy_entrypoint():
    return CapabilityExtension()
