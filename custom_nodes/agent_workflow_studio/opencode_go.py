"""OpenCode Go chat client with schema-validated JSON output."""

from __future__ import annotations

import ipaddress
import json
import os
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

BASE_URL = "https://opencode.ai/zen/go/v1/chat/completions"
MODEL = "mimo-v2.6-flash"


def _endpoint() -> str:
    endpoint = os.environ.get("AGENT_WORKFLOW_STUDIO_OPENCODE_URL", BASE_URL)
    parsed = urlparse(endpoint)
    if parsed.scheme == "https" and parsed.hostname:
        return endpoint
    if parsed.scheme == "http" and parsed.hostname:
        try:
            if ipaddress.ip_address(parsed.hostname).is_loopback:
                return endpoint
        except ValueError:
            if parsed.hostname == "localhost":
                return endpoint
    raise ValueError("OpenCode endpoint must use HTTPS or loopback HTTP")


def _reject_refs(value: Any) -> None:
    if isinstance(value, dict):
        if "$ref" in value:
            raise ValueError("output schemas must not use $ref")
        for child in value.values():
            _reject_refs(child)
    elif isinstance(value, list):
        for child in value:
            _reject_refs(child)


def complete_json(
    task: str,
    schema_text: str,
    api_key: str,
    *,
    temperature: float = 0.0,
    max_tokens: int = 2048,
    system_prompt: str = "",
) -> dict[str, Any]:
    if not task.strip() or len(task) > 32_000:
        raise ValueError("task must contain 1–32,000 characters")
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
        raise RuntimeError("Install the custom node's OpenCode requirements") from exc
    validator = validator_for(schema)(schema)
    validator.check_schema(schema)

    if not 0.0 <= temperature <= 2.0 or not 1 <= max_tokens <= 32768:
        raise ValueError("temperature or max_tokens is out of range")
    system = system_prompt.strip()
    system += ("\n\n" if system else "") + (
        "Return one JSON value that satisfies this JSON Schema. "
        "Do not include Markdown or text outside the JSON value.\n"
        + json.dumps(schema, ensure_ascii=False)
    )
    payload = {
        "model": MODEL,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "messages": [
            {
                "role": "system",
                "content": system,
            },
            {"role": "user", "content": task},
        ],
    }
    request = Request(
        _endpoint(),
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=120) as response:
            result = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        raise RuntimeError(f"OpenCode Go request failed with HTTP {exc.code}") from None
    except (URLError, TimeoutError, OSError) as exc:
        raise RuntimeError(
            f"OpenCode Go is unavailable ({type(exc).__name__})"
        ) from None
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise RuntimeError("OpenCode Go returned invalid JSON") from None

    try:
        content = result["choices"][0]["message"]["content"]
        if not isinstance(content, str):
            raise TypeError
        output = json.loads(content)
    except (KeyError, IndexError, TypeError, json.JSONDecodeError):
        raise RuntimeError("OpenCode Go did not return a JSON object") from None
    if not isinstance(output, dict):
        raise TypeError("agent output must be a JSON object")
    errors = list(validator.iter_errors(output))
    if errors:
        first = errors[0]
        location = ".".join(str(part) for part in first.absolute_path) or "$"
        raise RuntimeError(f"agent output failed schema validation at {location}")
    return output


__all__ = ["BASE_URL", "MODEL", "complete_json"]
