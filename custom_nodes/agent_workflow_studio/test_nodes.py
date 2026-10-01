"""Offline tests for native ComfyUI V3 nodes."""

from __future__ import annotations

import math

import pytest

from . import nodes


def test_generation_controls_emits_native_typed_config():
    result = nodes.NayoseGenerationControls.execute(
        '{"profile":"smoke","difficulty":"realistic","seed":73}'
    )

    assert result.result == (
        {"profile": "smoke", "difficulty": "realistic", "seed": 73},
    )
    schema = nodes.NayoseGenerationControls.define_schema()
    assert schema.outputs[0].io_type == "NAYOSE_GENERATION_CONFIG"


def test_generation_controls_rejects_non_object_json():
    with pytest.raises(TypeError, match="must be a JSON object"):
        nodes.NayoseGenerationControls.execute("[1, 2, 3]")


def test_nayose_adapter_domain_validates_dict_before_typed_socket(monkeypatch):
    class FakeAPI:
        def preview_generation(self, value):
            assert value == {"profile": "smoke"}
            return {"config_hash": "cfg123"}

    monkeypatch.setattr(nodes.NayoseAPI, "from_environment", lambda: FakeAPI())
    result = nodes.NayoseValidateAgentControls.execute({"profile": "smoke"})

    assert result.result == ({"profile": "smoke"},)
    assert nodes.NayoseValidateAgentControls.define_schema().outputs[0].io_type == (
        "NAYOSE_GENERATION_CONFIG"
    )


def test_train_node_resolves_auto_to_host_device(monkeypatch):
    class FakeAPI:
        def run_admin_job(self, action, params):
            assert action == "train"
            assert params == {
                "generation_id": "gen_smoke",
                "seed": 42,
                "device": "mps",
                "model_family": "off",
            }
            return {"run": "run_smoke", "fingerprint": "fp123", "metrics": {}}

    monkeypatch.setattr(nodes, "_default_device", lambda: "mps")
    monkeypatch.setattr(nodes.NayoseAPI, "from_environment", lambda: FakeAPI())
    result = nodes.NayoseTrainModel.execute(
        {"generation_id": "gen_smoke", "fingerprint": "fp123"}, 42, "auto", "off"
    )

    assert result.result[0]["run_id"] == "run_smoke"


def test_llm_node_takes_prompt_input_and_emits_native_dict(monkeypatch):
    monkeypatch.setattr(nodes, "get_credential", lambda: "test-only-secret")
    monkeypatch.setattr(
        nodes,
        "complete_json",
        lambda *_args, **_kwargs: {"answer": "ok"},
    )

    output = nodes.OpenCodeGoStructuredAgent.execute(
        "connected prompt",
        '{"type":"object"}',
        0.0,
        128,
    )

    assert output.result == ({"answer": "ok"},)
    assert nodes.OpenCodeGoStructuredAgent.define_schema().outputs[0].io_type == "DICT"


def test_llm_node_does_not_execute_without_server_credential(monkeypatch):
    monkeypatch.setattr(nodes, "get_credential", lambda: None)
    with pytest.raises(RuntimeError, match="Configure an OpenCode Go API key"):
        nodes.OpenCodeGoStructuredAgent.execute("prompt", '{"type":"object"}', 0, 128)


def test_side_effecting_nodes_disable_comfy_cache():
    assert math.isnan(nodes.NayoseGenerateDataset.fingerprint_inputs())
    assert math.isnan(nodes.NayoseTrainModel.fingerprint_inputs())
    assert math.isnan(nodes.OpenCodeGoStructuredAgent.fingerprint_inputs())


def test_native_extension_registers_all_workflow_nodes():
    import asyncio

    from . import AgentWorkflowStudioExtension

    node_types = asyncio.run(AgentWorkflowStudioExtension().get_node_list())
    ids = {node.define_schema().node_id for node in node_types}
    assert "AgentWorkflowOpenCodeGoStructuredAgent" in ids
    assert "AgentWorkflowNayoseGenerateDataset" in ids
    assert "AgentWorkflowNayoseValidateAgentControls" in ids
