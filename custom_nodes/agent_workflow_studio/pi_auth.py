"""Pi provider credential metadata and OS-keychain access."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from functools import lru_cache
from pathlib import Path

from .credential_store import (
    credential_configured,
    delete_credential,
    get_credential,
    save_credential,
)

_METADATA_HELPER = Path(__file__).with_name("pi_env_key_metadata.mjs")
_PROVIDER_ID = re.compile(r"[A-Za-z0-9_.+-]{1,120}\Z")
_ENV_NAME = re.compile(r"[A-Z][A-Z0-9_]{0,127}\Z")


@lru_cache(maxsize=256)
def provider_api_key_envs(provider: str) -> tuple[str, ...]:
    return provider_api_key_fields((provider,)).get(provider, ())


@lru_cache(maxsize=32)
def provider_api_key_fields(providers: tuple[str, ...]) -> dict[str, tuple[str, ...]]:
    if any(not _PROVIDER_ID.fullmatch(provider) for provider in providers):
        raise ValueError("provider must be a valid Pi provider ID")
    _, fields = _query_pi_key_metadata(providers)
    return fields


@lru_cache(maxsize=1)
def pi_api_key_providers() -> tuple[str, ...]:
    providers, _ = _query_pi_key_metadata(("*",))
    return providers


@lru_cache(maxsize=1)
def all_provider_api_key_env_names() -> tuple[str, ...]:
    provider_fields = provider_api_key_fields(pi_api_key_providers())
    names = {name for fields in provider_fields.values() for name in fields}
    return tuple(sorted(names))


def _query_pi_key_metadata(
    providers: tuple[str, ...],
) -> tuple[tuple[str, ...], dict[str, tuple[str, ...]]]:
    compat_path = _pi_ai_compat_path()
    node = shutil.which("node")
    if compat_path is None or node is None or not _METADATA_HELPER.is_file():
        return (), {}
    environment = {
        "HOME": str(Path.home()),
        "PATH": os.environ.get("PATH", ""),
        "NODE_NO_WARNINGS": "1",
    }
    try:
        result = subprocess.run(
            [node, str(_METADATA_HELPER), str(compat_path), json.dumps(providers)],
            cwd=str(Path.home()),
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=8,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return (), {}
    if result.returncode != 0:
        return (), {}
    try:
        metadata = json.loads(result.stdout)
    except json.JSONDecodeError:
        return (), {}
    if not isinstance(metadata, dict):
        return (), {}
    provider_ids = metadata.get("providers", [])
    fields = metadata.get("fields", {})
    if not isinstance(provider_ids, list) or not isinstance(fields, dict):
        return (), {}
    valid_providers = tuple(
        provider
        for provider in provider_ids
        if isinstance(provider, str) and _PROVIDER_ID.fullmatch(provider)
    )
    valid_fields = {
        provider: tuple(
            field
            for field in provider_fields
            if isinstance(field, str) and _ENV_NAME.fullmatch(field)
        )
        for provider, provider_fields in fields.items()
        if provider in providers or "*" in providers
        if isinstance(provider_fields, list)
    }
    return valid_providers, valid_fields


def credential_account(provider: str, env_name: str) -> str:
    if not _PROVIDER_ID.fullmatch(provider) or not _ENV_NAME.fullmatch(env_name):
        raise ValueError("invalid Pi credential account")
    if provider == "opencode-go" and env_name == "OPENCODE_API_KEY":
        return "opencode-go"
    return f"pi:{provider}:{env_name}"


def provider_credential_status(
    provider: str, env_names: tuple[str, ...] | None = None
) -> dict[str, bool]:
    fields = env_names if env_names is not None else provider_api_key_envs(provider)
    return {
        env_name: credential_configured(credential_account(provider, env_name))
        or _has_legacy_opencode_key(provider, env_name)
        for env_name in fields
    }


def save_provider_credential(provider: str, env_name: str, value: str) -> None:
    _validate_field(provider, env_name)
    if not isinstance(value, str) or not value.strip() or len(value) > 4096:
        raise ValueError("credential must contain 1–4,096 characters")
    save_credential(value.strip(), credential_account(provider, env_name))


def delete_provider_credential(provider: str, env_name: str) -> None:
    _validate_field(provider, env_name)
    delete_credential(credential_account(provider, env_name))


def provider_credential_environment(provider: str) -> dict[str, str]:
    fields = provider_api_key_envs(provider)
    environment: dict[str, str] = {}
    for env_name in fields:
        value = get_credential(credential_account(provider, env_name))
        if value is None and _has_legacy_opencode_key(provider, env_name):
            value = get_credential("opencode-go")
        if value:
            environment[env_name] = value
    return environment


def _validate_field(provider: str, env_name: str) -> None:
    if env_name not in provider_api_key_envs(provider):
        raise ValueError("Pi does not report this API-key field for the provider")


def _has_legacy_opencode_key(provider: str, env_name: str) -> bool:
    return (
        provider in {"opencode-go", "opencode"}
        and env_name == "OPENCODE_API_KEY"
        and credential_configured("opencode-go")
    )


def _pi_ai_compat_path() -> Path | None:
    executable = os.environ.get("AGENT_WORKFLOW_STUDIO_PI_CLI") or shutil.which("pi")
    if not executable:
        return None
    resolved = Path(executable).expanduser().resolve()
    for parent in (resolved.parent, *resolved.parents):
        candidate = parent / "node_modules/@earendil-works/pi-ai/dist/compat.js"
        if candidate.is_file():
            return candidate
    return None


__all__ = [
    "all_provider_api_key_env_names",
    "credential_account",
    "delete_provider_credential",
    "pi_api_key_providers",
    "provider_api_key_envs",
    "provider_api_key_fields",
    "provider_credential_environment",
    "provider_credential_status",
    "save_provider_credential",
]
