"""OpenCode Go request and schema-validation tests; network is mocked."""

from __future__ import annotations

import json

import pytest

from . import opencode_go


class FakeResponse:
    def __init__(self, value: dict[str, object]) -> None:
        self.value = value

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self) -> bytes:
        return json.dumps(self.value).encode()


def test_request_uses_configured_model_and_schema_validation(monkeypatch) -> None:
    seen = {}

    def fake_urlopen(request, timeout):
        seen["url"] = request.full_url
        seen["authorization"] = request.get_header("Authorization")
        seen["timeout"] = timeout
        seen["payload"] = json.loads(request.data)
        return FakeResponse(
            {"choices": [{"message": {"content": '{"approved":true}'}}]}
        )

    monkeypatch.setattr(opencode_go, "urlopen", fake_urlopen)
    schema = '{"type":"object","properties":{"approved":{"type":"boolean"}},"required":["approved"],"additionalProperties":false}'
    result = opencode_go.complete_json("Decide", schema, "test-only-secret")

    assert result == {"approved": True}
    assert seen["url"] == opencode_go.BASE_URL
    assert seen["authorization"] == "Bearer test-only-secret"
    assert seen["payload"]["model"] == "mimo-v2.6-flash"
    assert seen["timeout"] == 120


def test_invalid_agent_output_fails_closed(monkeypatch) -> None:
    monkeypatch.setattr(
        opencode_go,
        "urlopen",
        lambda *_args, **_kwargs: FakeResponse(
            {"choices": [{"message": {"content": '{"approved":"yes"}'}}]}
        ),
    )
    schema = '{"type":"object","properties":{"approved":{"type":"boolean"}},"required":["approved"]}'
    with pytest.raises(RuntimeError, match="schema validation"):
        opencode_go.complete_json("Decide", schema, "test-only-secret")


def test_remote_schema_references_are_rejected_before_request(monkeypatch) -> None:
    monkeypatch.setattr(
        opencode_go,
        "urlopen",
        lambda *_args, **_kwargs: pytest.fail("must not make a request"),
    )
    with pytest.raises(ValueError, match=r"must not use \$ref"):
        opencode_go.complete_json(
            "Decide", '{"$ref":"https://example.invalid/schema"}', "test-only-secret"
        )
