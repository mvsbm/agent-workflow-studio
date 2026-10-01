"""Loopback-only ComfyUI routes for OpenCode Go credentials."""

from __future__ import annotations

import ipaddress
from urllib.parse import urlparse

from aiohttp import web

from .credential_store import (
    CredentialStoreError,
    credential_configured,
    delete_credential,
    get_credential,
    save_credential,
)
from .opencode_go import MODEL, complete_json

BASE = "/agent-workflow-studio/opencode-go"


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
    if (
        parsed.scheme not in {"http", "https"}
        or parsed.netloc.lower() != request.host.lower()
    ):
        return False
    host = parsed.hostname or ""
    try:
        origin_ip = ipaddress.ip_address(host)
    except ValueError:
        return host.lower() == "localhost"
    return origin_ip.is_loopback


def install_routes() -> None:
    """Register the extension's local secret-management endpoints."""
    from server import PromptServer

    routes = PromptServer.instance.routes

    @routes.get(f"{BASE}/status")
    async def status(request: web.Request) -> web.Response:
        if not _is_local_request(request):
            return web.json_response({"error": "local access only"}, status=403)
        try:
            configured = credential_configured()
        except CredentialStoreError:
            return web.json_response(
                {"error": "OS credential vault unavailable"}, status=503
            )
        return web.json_response(
            {"configured": configured, "provider": "OpenCode Go", "model": MODEL}
        )

    @routes.put(f"{BASE}/credential")
    async def save(request: web.Request) -> web.Response:
        if not _is_local_request(request):
            return web.json_response({"error": "local access only"}, status=403)
        try:
            body = await request.json()
        except (ValueError, web.HTTPBadRequest):
            return web.json_response({"error": "invalid JSON body"}, status=400)
        key = body.get("api_key") if isinstance(body, dict) else None
        if not isinstance(key, str) or not key.strip() or len(key) > 4096:
            return web.json_response({"error": "enter a valid API key"}, status=400)
        try:
            save_credential(key.strip())
        except CredentialStoreError:
            return web.json_response(
                {"error": "OS credential vault unavailable"}, status=503
            )
        return web.json_response({"configured": True, "model": MODEL})

    @routes.delete(f"{BASE}/credential")
    async def remove(request: web.Request) -> web.Response:
        if not _is_local_request(request):
            return web.json_response({"error": "local access only"}, status=403)
        try:
            delete_credential()
        except CredentialStoreError:
            return web.json_response(
                {"error": "OS credential vault unavailable"}, status=503
            )
        return web.json_response({"configured": False})

    @routes.post(f"{BASE}/test")
    async def test_connection(request: web.Request) -> web.Response:
        if not _is_local_request(request):
            return web.json_response({"error": "local access only"}, status=403)
        try:
            key = get_credential()
            if not key:
                return web.json_response(
                    {"error": "configure an API key first"}, status=409
                )
            complete_json(
                'Reply with the JSON object {"ok":true}.',
                '{"type":"object","properties":{"ok":{"type":"boolean"}},'
                '"required":["ok"],"additionalProperties":false}',
                key,
            )
        except CredentialStoreError:
            return web.json_response(
                {"error": "OS credential vault unavailable"}, status=503
            )
        except (RuntimeError, ValueError):
            return web.json_response(
                {"error": "OpenCode Go connection test failed"}, status=502
            )
        return web.json_response({"connected": True, "model": MODEL})


__all__ = ["install_routes"]
