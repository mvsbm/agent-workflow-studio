import hashlib
import json
import math
import re
from dataclasses import dataclass
from typing import Any


class EnterpriseError(ValueError):
    pass


def identifier(value: object) -> str:
    if not isinstance(value, str) or not re.fullmatch(
        r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", value
    ):
        raise EnterpriseError("invalid_identifier")
    return value


@dataclass(frozen=True)
class AccessContext:
    tenant_id: str
    actor_id: str
    permissions: frozenset[str]

    def __post_init__(self) -> None:
        identifier(self.tenant_id)
        identifier(self.actor_id)
        if not isinstance(self.permissions, frozenset) or not self.permissions <= {
            "project.read",
            "project.write",
            "events.publish",
        }:
            raise EnterpriseError("invalid_permissions")


def require(context: AccessContext, permission: str) -> None:
    if not isinstance(context, AccessContext):
        raise EnterpriseError("access_context_required")
    if permission not in context.permissions:
        raise EnterpriseError("permission_denied")


def encode(value: Any) -> str:
    remaining = 30000

    def validate(item: Any, depth: int) -> None:
        nonlocal remaining
        remaining -= 1
        if depth > 32 or remaining < 0:
            raise EnterpriseError("payload_limit")
        if type(item) is dict:
            for key, child in item.items():
                if not isinstance(key, str):
                    raise EnterpriseError("payload_invalid")
                validate(child, depth + 1)
        elif type(item) is list:
            for child in item:
                validate(child, depth + 1)
        elif type(item) not in (str, int, float, bool, type(None)) or (
            type(item) is float and not math.isfinite(item)
        ):
            raise EnterpriseError("payload_invalid")

    validate(value, 0)
    try:
        result = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        if len(result.encode("utf-8")) > 1048576:
            raise EnterpriseError("payload_limit")
        return result
    except EnterpriseError:
        raise
    except (ValueError, UnicodeError, OverflowError):
        raise EnterpriseError("payload_invalid") from None


def digest(value: Any) -> str:
    return hashlib.sha256(encode(value).encode("utf-8")).hexdigest()


def page_limit(value: int) -> int:
    if type(value) is not int or not 1 <= value <= 100:
        raise EnterpriseError("page_limit")
    return value
