"""Shared JSON Schema preparation and structured-object validation."""

from __future__ import annotations

import json
from typing import Any


def build_validator(schema_text: str) -> Any:
    if len(schema_text) > 32_000:
        raise ValueError("output schema is too large")
    try:
        schema = json.loads(schema_text)
    except json.JSONDecodeError as exc:
        raise ValueError("output schema must be valid JSON") from exc
    if not isinstance(schema, dict):
        raise TypeError("output schema must be a JSON object")
    _reject_refs(schema)
    try:
        from jsonschema.validators import validator_for
    except ImportError as exc:
        raise RuntimeError("Install the structured-output requirements") from exc
    validator = validator_for(schema)(schema)
    validator.check_schema(schema)
    return validator


def parse_validated_object(content: str, validator: Any, source: str) -> dict[str, Any]:
    try:
        output = json.loads(content)
    except json.JSONDecodeError as exc:
        category = _json_format_category(content)
        raise RuntimeError(
            f"{source} did not return a JSON object "
            f"(format={category}; parser={exc.msg}; line={exc.lineno}; column={exc.colno})"
        ) from None
    if not isinstance(output, dict):
        raise TypeError(
            f"{source} output must be a JSON object (output_type={type(output).__name__})"
        )
    errors = list(validator.iter_errors(output))
    if errors:
        first = errors[0]
        location = ".".join(str(part) for part in first.absolute_path) or "$"
        raise RuntimeError(
            f"agent output failed schema validation at {location}; constraint={first.validator}"
        )
    return output


def _json_format_category(content: str) -> str:
    stripped = content.lstrip()
    if not stripped:
        return "empty"
    if stripped.startswith("```"):
        return "markdown_fence"
    if stripped.startswith("{"):
        return "malformed_object"
    if stripped.startswith("["):
        return "malformed_array"
    return "non_json_prefix"


def _reject_refs(value: Any) -> None:
    if isinstance(value, dict):
        if "$ref" in value:
            raise ValueError("output schemas must not use $ref")
        for child in value.values():
            _reject_refs(child)
    elif isinstance(value, list):
        for child in value:
            _reject_refs(child)


__all__ = ["build_validator", "parse_validated_object"]
