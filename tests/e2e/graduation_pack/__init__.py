"""Test-only: the fake agent's `shout` tool, graduated into a node by following its graduation brief
(graduation.py) the way a developer would: the brief's interface (text: STRING in, text: STRING
out), its description, the tool's logic reviewed for in-process use, and its examples as tests
(tests/e2e/graduation.cjs checks them, then rebuilds the workflow's stage with this node and keeps
its tests green). Loaded only by that test, never in the studio.
"""

from __future__ import annotations

from comfy_api.latest import ComfyExtension, io


class Shout(io.ComfyNode):
    @classmethod
    def define_schema(cls) -> io.Schema:
        return io.Schema(
            node_id="GraduatedShout",
            display_name="Shout",
            category="text",  # a text node: in the agent's scope, as a studio node would be
            description="Uppercase and add an exclamation mark.",
            inputs=[io.String.Input("text", tooltip="The text to shout.")],
            outputs=[io.String.Output(display_name="text", tooltip="The text in capitals, with !")],
        )

    @classmethod
    def execute(cls, text: str) -> io.NodeOutput:
        return io.NodeOutput(text.upper() + "!")


class GraduationExtension(ComfyExtension):
    async def get_node_list(self):
        return [Shout]


async def comfy_entrypoint():
    return GraduationExtension()
