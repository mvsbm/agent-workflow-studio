"""Loopback-only ComfyUI routes for Pi model catalog and credential management."""

from __future__ import annotations

import asyncio
import ipaddress
from urllib.parse import urlparse

from aiohttp import web

from .credential_store import CredentialStoreError
from .pi_auth import (
    delete_provider_credential,
    pi_api_key_providers,
    provider_api_key_fields,
    provider_credential_status,
    save_provider_credential,
)
from .pi_cli import PiCLIError, check_auth, list_available_models

PI_BASE = "/agent-workflow-studio/pi"


def _is_local_request(request: web.Request) -> bool:
    try:
        remote = ipaddress.ip_address(request.remote or "")
    except ValueError:
        return False
    if not remote.is_loopback:
        return False
    origin = request.headers.get("Origin")
    if origin is None:
        return True
    parsed = urlparse(origin)
    if parsed.scheme not in {"http", "https"} or parsed.netloc.lower() != request.host.lower():
        return False
    host = parsed.hostname or ""
    try:
        origin_ip = ipaddress.ip_address(host)
    except ValueError:
        return host.lower() == "localhost"
    return origin_ip.is_loopback


def install_routes() -> None:
    """Register the extension's local Pi catalog and secret-management endpoints."""
    from server import PromptServer

    routes = PromptServer.instance.routes

    @routes.get(f"{PI_BASE}/catalog")
    async def pi_catalog(request: web.Request) -> web.Response:
        if not _is_local_request(request):
            return web.json_response({"error": "local access only"}, status=403)
        try:
            catalog = await asyncio.to_thread(_collect_pi_catalog)
        except (PiCLIError, OSError, ValueError):
            return web.json_response({"error": "Pi model catalog unavailable"}, status=503)
        return web.json_response(catalog)

    @routes.put(f"{PI_BASE}/credential")
    async def save_pi_credential(request: web.Request) -> web.Response:
        if not _is_local_request(request):
            return web.json_response({"error": "local access only"}, status=403)
        try:
            body = await request.json()
        except (ValueError, web.HTTPBadRequest):
            return web.json_response({"error": "invalid JSON body"}, status=400)
        provider = body.get("provider") if isinstance(body, dict) else None
        env_name = body.get("env_name") if isinstance(body, dict) else None
        secret = body.get("api_key") if isinstance(body, dict) else None
        if not all(isinstance(value, str) for value in (provider, env_name, secret)):
            return web.json_response({"error": "invalid credential request"}, status=400)
        try:
            await asyncio.to_thread(save_provider_credential, provider, env_name, secret)
        except CredentialStoreError:
            return web.json_response({"error": "OS credential vault unavailable"}, status=503)
        except ValueError:
            return web.json_response({"error": "invalid Pi API-key field"}, status=400)
        return web.json_response({"saved": True, "provider": provider, "env_name": env_name})

    @routes.delete(f"{PI_BASE}/credential")
    async def remove_pi_credential(request: web.Request) -> web.Response:
        if not _is_local_request(request):
            return web.json_response({"error": "local access only"}, status=403)
        provider = request.query.get("provider", "")
        env_name = request.query.get("env_name", "")
        try:
            await asyncio.to_thread(delete_provider_credential, provider, env_name)
        except CredentialStoreError:
            return web.json_response({"error": "OS credential vault unavailable"}, status=503)
        except ValueError:
            return web.json_response({"error": "invalid Pi API-key field"}, status=400)
        return web.json_response({"saved": False, "provider": provider, "env_name": env_name})

    @routes.post(f"{PI_BASE}/auth-check")
    async def pi_auth_check(request: web.Request) -> web.Response:
        if not _is_local_request(request):
            return web.json_response({"error": "local access only"}, status=403)
        try:
            body = await request.json()
        except (ValueError, web.HTTPBadRequest):
            return web.json_response({"error": "invalid JSON body"}, status=400)
        if not isinstance(body, dict):
            return web.json_response({"error": "invalid auth-check request"}, status=400)
        try:
            result = await asyncio.to_thread(
                check_auth,
                body.get("provider", ""),
                body.get("model", ""),
                body.get("credential_source", "pi_managed"),
            )
        except CredentialStoreError:
            return web.json_response({"error": "OS credential vault unavailable"}, status=503)
        except ValueError:
            return web.json_response({"error": "invalid Pi model/auth selection"}, status=400)
        except PiCLIError:
            return web.json_response({"error": "Pi authentication status check failed"}, status=502)
        return web.json_response(result)


def _collect_pi_catalog() -> dict[str, object]:
    models = [
        model for model in list_available_models() if model.get("provider") and model.get("id")
    ]
    providers = tuple(sorted({model["provider"] for model in models} | set(pi_api_key_providers())))
    fields = provider_api_key_fields(providers)
    provider_data = []
    vault_available = True
    for provider in providers:
        api_key_fields = fields.get(provider, ())
        if vault_available:
            try:
                saved = provider_credential_status(provider, api_key_fields)
            except CredentialStoreError:
                vault_available = False
                saved = {field: False for field in api_key_fields}
        else:
            saved = {field: False for field in api_key_fields}
        provider_data.append(
            {
                "id": provider,
                "has_models": any(model["provider"] == provider for model in models),
                "api_key_fields": list(api_key_fields),
                "keychain": saved,
            }
        )
    return {
        "models": models,
        "providers": provider_data,
        "credential_vault_available": vault_available,
        "catalog_source": ("Pi CLI catalog and installed provider metadata; offline mode"),
        "provider_requests_made": False,
    }


__all__ = ["install_routes"]
