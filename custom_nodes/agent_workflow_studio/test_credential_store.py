"""Credential-store tests use only an in-memory fake, never the OS vault."""

from __future__ import annotations

from . import credential_store


class FakeKeyring:
    def __init__(self) -> None:
        self.value: str | None = None

    def get_password(self, service: str, account: str) -> str | None:
        assert service == credential_store.SERVICE
        assert account == credential_store.ACCOUNT
        return self.value

    def set_password(self, service: str, account: str, value: str) -> None:
        assert service == credential_store.SERVICE
        assert account == credential_store.ACCOUNT
        self.value = value

    def delete_password(self, service: str, account: str) -> None:
        assert service == credential_store.SERVICE
        assert account == credential_store.ACCOUNT
        self.value = None


def test_credential_store_round_trip(monkeypatch) -> None:
    keyring = FakeKeyring()
    monkeypatch.setattr(credential_store, "_keyring", lambda: keyring)

    assert not credential_store.credential_configured()
    credential_store.save_credential("test-only-secret")
    assert credential_store.credential_configured()
    assert credential_store.get_credential() == "test-only-secret"
    credential_store.delete_credential()
    assert not credential_store.credential_configured()


def test_os_keyring_errors_are_wrapped(monkeypatch) -> None:
    def fail():
        raise RuntimeError("backend details")

    monkeypatch.setattr(credential_store, "_keyring", fail)
    try:
        credential_store.save_credential("test-only-secret")
    except RuntimeError as exc:
        assert "backend details" not in str(exc)
    else:
        raise AssertionError("vault failure should be surfaced")
