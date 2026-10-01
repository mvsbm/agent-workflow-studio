"""Small OS-keyring wrapper for server-side provider credentials."""

from __future__ import annotations

SERVICE = "comfy.agent-workflow-studio"
ACCOUNT = "opencode-go"
_SECURE = ("SecretService", "libsecret", "kwallet", "macOS", "Windows")


class CredentialStoreError(RuntimeError):
    """The operating-system credential vault is unavailable or insecure."""


def _keyring():
    try:
        import keyring
    except ImportError as exc:
        raise CredentialStoreError(
            "Install the custom node's keyring requirements"
        ) from exc
    root = keyring.get_keyring()
    backends = getattr(root, "backends", [root])
    if not any(
        type(item).__module__.rsplit(".", 1)[-1] in _SECURE
        and getattr(item, "priority", 0) > 0
        for item in backends
    ):
        raise CredentialStoreError("No supported OS credential vault is available")
    return keyring


def _operate(
    action: str, value: str | None = None, account: str = ACCOUNT
) -> str | None:
    try:
        store = _keyring()
        if action == "read":
            return store.get_password(SERVICE, account)
        if action == "write" and value is not None:
            store.set_password(SERVICE, account, value)
            return None
        if action == "delete":
            if store.get_password(SERVICE, account) is not None:
                store.delete_password(SERVICE, account)
            return None
    except CredentialStoreError:
        raise
    except Exception as exc:
        raise CredentialStoreError("OS credential vault operation failed") from exc
    raise ValueError(f"unsupported credential operation: {action}")


def get_credential(account: str = ACCOUNT) -> str | None:
    return _operate("read", account=account)


def save_credential(value: str, account: str = ACCOUNT) -> None:
    _operate("write", value, account)


def delete_credential(account: str = ACCOUNT) -> None:
    _operate("delete", account=account)


def credential_configured(account: str = ACCOUNT) -> bool:
    return get_credential(account) is not None


__all__ = [
    "CredentialStoreError",
    "credential_configured",
    "delete_credential",
    "get_credential",
    "save_credential",
]
