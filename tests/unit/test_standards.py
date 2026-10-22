"""The type standard: one registry of core types and dataset classes, with rules it enforces."""

# Verifies: R11, R36
import copy
import json
import unittest

from custom_nodes.agent_workflow_studio import standards
from custom_nodes.agent_workflow_studio.standards import HERE, build


def documents():
    types = json.loads((HERE / "types.json").read_text(encoding="utf-8"))
    domains = [
        json.loads(p.read_text(encoding="utf-8"))
        for p in sorted((HERE / "datasets").glob("*.json"))
    ]
    return types, domains


class RegistryTests(unittest.TestCase):
    def test_the_shipped_registry_keeps_its_rules(self) -> None:
        registry = standards.registry()
        core = {s.name for s in registry.specs.values() if s.scope == "type"}
        self.assertEqual(
            core,
            {"STRING", "INT", "FLOAT", "BOOLEAN", "JSON", "TABLE", "DATASET", "FILE", "FOLDER"},
        )
        # No domain word is a core type: matching's classes live in their own domain file.
        self.assertEqual(standards.resolve("record_clusters").domain, "matching")
        self.assertIsNone(standards.resolve("customers"))

    def test_old_names_keep_working_as_aliases(self) -> None:
        self.assertEqual(standards.resolve("PAIRS").name, "record_pairs")
        self.assertEqual(standards.port_socket("CUSTOMERS"), "RECORD_CLUSTERS")
        self.assertEqual(standards.api_kind("count"), "integer")
        self.assertEqual(standards.api_kind("csv"), "table")
        self.assertEqual(standards.socket_aliases()["DECISIONS"], "PAIR_DECISIONS")
        # An old name another installed pack still uses is that pack's: it is not rewritten.
        self.assertNotIn("PAIRS", standards.socket_aliases(live={"PAIRS", "STRING"}))
        self.assertIsNone(standards.api_kind("image"))  # not registered: refused, not guessed

    def test_ports_and_kinds_come_from_the_registry(self) -> None:
        self.assertIn("RECORD_PAIRS", standards.port_types())
        self.assertNotIn(
            "SYNTHETIC_PEOPLE", standards.port_types()
        )  # in memory: cannot cross into a tool
        self.assertIsNone(standards.port_socket("synthetic_people"))
        self.assertEqual(
            standards.handoff_sockets()
            >= {"TABLE", "RECORD_PAIRS", "PAIR_DECISIONS", "RECORD_CLUSTERS"},
            True,
        )
        self.assertEqual(
            standards.api_kinds(),
            ["text", "integer", "number", "boolean", "json", "table", "file", "folder"],
        )

    def test_values_are_checked_against_their_type(self) -> None:
        self.assertEqual(standards.value_problems("TABLE", {"columns": [], "rows": []}), [])
        self.assertEqual(
            standards.value_problems("INT", True), ["should be INT: True is not of type 'integer'"]
        )
        reference = {
            "kind": "studio#dataset",
            "class": "pair_decisions",
            "uri": "runs/x/decisions.csv",
        }
        self.assertEqual(standards.value_problems("PAIR_DECISIONS", reference), [])
        self.assertEqual(
            standards.value_problems("RECORD_PAIRS", reference),
            ["should be a record_pairs dataset, got 'pair_decisions'"],
        )

    def test_the_agents_tool_schema_lists_the_registered_port_types(self) -> None:
        tools = json.loads((HERE.parent / "agent_tools.json").read_text(encoding="utf-8"))
        propose = next(t for t in tools if t["name"] == "comfy_propose_tool")
        for side in ("inputs", "outputs"):
            kind = propose["parameters"]["properties"][side]["items"]["properties"]["kind"]
            self.assertEqual(kind["enum"], standards.port_types())

    def test_the_written_standard_is_generated_from_the_registry(self) -> None:
        self.assertEqual(
            standards.DOC.read_text(encoding="utf-8"),
            standards.render(),
            "STANDARDS.md is stale: python -m custom_nodes.agent_workflow_studio.standards",
        )

    def test_a_broken_registry_is_refused_with_every_problem(self) -> None:
        types, domains = documents()
        types = copy.deepcopy(types)
        domains = copy.deepcopy(domains)
        types["types"]["TABLE"]["aliases"].append("PAIRS")  # a name two things claim
        matching = next(d for d in domains if d["domain"] == "matching")
        matching["classes"]["record_pairs"]["storage"] = "cloud"
        matching["classes"]["pair_decisions"]["socket"] = "RECORD_PAIRS"
        with self.assertRaises(ValueError) as caught:
            build(types, domains)
        message = str(caught.exception)
        self.assertIn("PAIRS names both", message)
        self.assertIn("storage is one of file, memory", message)
        self.assertIn("two dataset classes share a socket", message)


if __name__ == "__main__":
    unittest.main()
