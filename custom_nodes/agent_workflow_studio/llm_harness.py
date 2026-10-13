"""Event-driven lifecycle and safe diagnostics for one structured LLM request."""

from __future__ import annotations

import os
import time
from collections.abc import Callable
from typing import Any

from .node_progress import report_node_progress

DEFAULT_REASONING_TIMEOUT_SECONDS = 120
PROGRESS_REPORT_INTERVAL_SECONDS = 10
REASONING_TIMEOUT_ENV = "AGENT_WORKFLOW_STUDIO_REASONING_TIMEOUT_SECONDS"


class LLMRunHarness:
    def __init__(
        self,
        node_id: str,
        stage: str,
        *,
        reasoning_timeout_seconds: float | None = None,
    ) -> None:
        if reasoning_timeout_seconds is None:
            reasoning_timeout_seconds = _reasoning_timeout_from_environment()
        if not 1 <= reasoning_timeout_seconds <= 600:
            raise ValueError("reasoning timeout must be between 1 and 600 seconds")
        self.node_id = node_id
        self.stage = stage
        self.reasoning_timeout_seconds = reasoning_timeout_seconds
        self.started_at = 0.0
        self.last_progress_at = 0.0
        self.reasoning_events = 0
        self.reasoning_chars = 0
        self.answer_chars = 0
        self.connected_after_seconds: float | None = None
        self.first_reasoning_after_seconds: float | None = None
        self.first_answer_after_seconds: float | None = None
        self.finish_reason: str | None = None
        self.auth_source: str | None = None
        self.usage: dict[str, int] = {}
        self.structured_output_received = False
        self.structured_output_validated = False

    def report_status(self, message: str) -> None:
        self._report(f"{self.stage}: {message}")

    def run(
        self,
        complete: Callable[..., dict[str, Any]],
        task: str,
        schema: str,
        *,
        temperature: float = 0.0,
        max_tokens: int = 2048,
        system_prompt: str = "",
        pi_config: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self.started_at = time.monotonic()
        self.last_progress_at = self.started_at
        self._report(
            f"{self.stage}: request started; stopping after "
            f"{self.reasoning_timeout_seconds:.0f}s without answer text or typed output."
        )
        try:
            result = complete(
                task,
                schema,
                temperature=temperature,
                max_tokens=max_tokens,
                system_prompt=system_prompt,
                pi_config=pi_config,
                on_event=self._on_provider_event,
                on_progress=self._on_answer,
                reasoning_timeout_seconds=self.reasoning_timeout_seconds,
            )
        except Exception as exc:
            self._report(self._summary("failed", error=exc))
            raise
        self._report(self._summary("complete; response schema validated"))
        return result

    def _on_provider_event(self, event: dict[str, Any]) -> None:
        kind = event.get("type")
        if kind == "reasoning_delta":
            self.reasoning_events += 1
            self.reasoning_chars += _non_negative_int(event.get("characters"))
            now = time.monotonic()
            if self.first_reasoning_after_seconds is None:
                self.first_reasoning_after_seconds = now - self.started_at
            if (
                self.reasoning_events == 1
                or now - self.last_progress_at >= PROGRESS_REPORT_INTERVAL_SECONDS
            ):
                self.last_progress_at = now
                elapsed = now - self.started_at
                self._report(
                    f"{self.stage}: model reasoning is active; text withheld; "
                    f"{elapsed:.0f}s elapsed, {self.reasoning_events} reasoning events."
                )
        elif kind == "answer_delta":
            self.answer_chars += _non_negative_int(event.get("characters"))
            if self.first_answer_after_seconds is None:
                self.first_answer_after_seconds = time.monotonic() - self.started_at
        elif kind == "finish":
            finish_reason = event.get("finish_reason")
            if isinstance(finish_reason, str):
                self.finish_reason = finish_reason[:80]
        elif kind == "usage":
            self.usage = _token_usage(event.get("usage"))
        elif kind == "structured_output_received":
            self.structured_output_received = True
            self._report(f"{self.stage}: typed output received; arguments withheld.")
        elif kind == "structured_output_validated":
            self.structured_output_validated = True
            self._report(f"{self.stage}: typed output passed local schema validation.")
        elif kind == "auth_source":
            source = event.get("auth_source")
            if source in {"pi_managed", "studio_keychain"}:
                self.auth_source = source
                label = "Studio OS Keychain" if source == "studio_keychain" else "Pi-managed auth"
                fields = _credential_field_names(event.get("credential_fields"))
                field_label = f" ({', '.join(fields)})" if fields else ""
                self._report(
                    f"{self.stage}: authentication source is {label}{field_label}; "
                    "credential values withheld."
                )
        elif kind == "configured":
            provider = event.get("provider")
            model = event.get("model")
            thinking_level = event.get("thinking_level")
            self._report(
                f"{self.stage}: Pi CLI configured for "
                f"{provider or 'default'}/{model or 'default'}; "
                f"thinking={thinking_level or 'default'}; only the schema-bound "
                "output tool is enabled; action tools, user extensions, sessions, "
                "and retries are disabled."
            )
        elif kind == "connected":
            if self.connected_after_seconds is None:
                self.connected_after_seconds = time.monotonic() - self.started_at
            provider = event.get("provider")
            model = event.get("model")
            model_label = f" ({provider}/{model})" if provider and model else ""
            self._report(
                f"{self.stage}: model stream connected after "
                f"{self.connected_after_seconds:.1f}s{model_label}."
            )

    def _on_answer(self, text: str) -> None:
        self.answer_chars = max(self.answer_chars, len(text))
        self._report(
            f"{self.stage}: answer text received; {len(text)} characters; content withheld."
        )

    def _summary(self, state: str, *, error: Exception | None = None) -> str:
        elapsed = time.monotonic() - self.started_at
        details = [
            f"{self.stage}: {state} after {elapsed:.1f}s",
            f"answer_chars={self.answer_chars}",
            f"reasoning_events={self.reasoning_events}, "
            f"reasoning_chars={self.reasoning_chars} (text withheld)",
        ]
        if self.connected_after_seconds is not None:
            details.append(f"connected_after={self.connected_after_seconds:.1f}s")
        if self.first_reasoning_after_seconds is not None:
            details.append(f"first_reasoning_after={self.first_reasoning_after_seconds:.1f}s")
        if self.first_answer_after_seconds is not None:
            details.append(f"first_answer_after={self.first_answer_after_seconds:.1f}s")
        if self.finish_reason:
            details.append(f"finish_reason={self.finish_reason}")
        if self.auth_source:
            details.append(f"auth_source={self.auth_source}")
        if self.usage:
            details.append(f"usage={self.usage}")
        if self.structured_output_received:
            details.append("structured_output_received=true (arguments withheld)")
        if self.structured_output_validated:
            details.append("structured_output_validated=true")
        if error is not None:
            details.append(
                f"failure_category={_failure_category(error)}; details withheld from progress text"
            )
        return "; ".join(details)

    def _report(self, message: str) -> None:
        report_node_progress(self.node_id, message)


def _failure_category(error: Exception) -> str:
    error_name = type(error).__name__
    if error_name == "PiReasoningBudgetExceeded":
        return "reasoning_timeout"
    if isinstance(error, TimeoutError):
        return "timeout"
    if error_name == "PiCLIError":
        return "pi_cli_error"
    if isinstance(error, (ValueError, TypeError)):
        return "input_or_schema_validation"
    if isinstance(error, OSError):
        return "local_process_or_transport"
    return "internal_error"


def _reasoning_timeout_from_environment() -> float:
    value = os.environ.get(REASONING_TIMEOUT_ENV)
    if value is None:
        return DEFAULT_REASONING_TIMEOUT_SECONDS
    try:
        timeout = float(value)
    except ValueError:
        raise ValueError(f"{REASONING_TIMEOUT_ENV} must be a number") from None
    if not 1 <= timeout <= 600:
        raise ValueError(f"{REASONING_TIMEOUT_ENV} must be between 1 and 600")
    return timeout


def _credential_field_names(value: Any) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(
        field
        for field in value[:8]
        if isinstance(field, str)
        and len(field) <= 128
        and field.isascii()
        and field.isupper()
        and field.replace("_", "").isalnum()
    )


def _non_negative_int(value: Any) -> int:
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    return 0


def _token_usage(value: Any) -> dict[str, int]:
    if not isinstance(value, dict):
        return {}
    usage = {}
    for key in (
        "prompt_tokens",
        "completion_tokens",
        "input_tokens",
        "output_tokens",
        "reasoning_tokens",
        "total_tokens",
    ):
        tokens = value.get(key)
        if isinstance(tokens, int) and not isinstance(tokens, bool) and tokens >= 0:
            usage[key] = tokens
    return usage


__all__ = [
    "DEFAULT_REASONING_TIMEOUT_SECONDS",
    "PROGRESS_REPORT_INTERVAL_SECONDS",
    "REASONING_TIMEOUT_ENV",
    "LLMRunHarness",
]
