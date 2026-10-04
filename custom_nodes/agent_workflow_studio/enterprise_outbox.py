import json
import math
import re
import uuid
from collections.abc import Callable
from typing import Any

from sqlalchemy import Select, and_, func, or_, select, update
from sqlalchemy.engine import Connection, Engine, RowMapping
from sqlalchemy.sql.elements import ColumnElement

from .enterprise_contracts import (
    AccessContext,
    EnterpriseError,
    identifier,
    page_limit,
    require,
)
from .enterprise_schema import outbox


def database_time(connection: Connection) -> float:
    value = connection.scalar(select(func.extract("epoch", func.current_timestamp())))
    if value is None:
        raise EnterpriseError("database_clock_invalid")
    return float(value)


class RevisionOutbox:
    def __init__(
        self, engine: Engine, clock: Callable[[Connection], float] = database_time
    ) -> None:
        self.engine = engine
        self.clock = clock

    def _eligible(self, now: float) -> ColumnElement[bool]:
        return or_(
            outbox.c.state == "pending",
            and_(outbox.c.state == "leased", outbox.c.lease_until <= now),
        )

    def claim_query(
        self, context: AccessContext, now: float, limit: int
    ) -> Select[Any]:
        require(context, "events.publish")
        return (
            select(outbox)
            .where(outbox.c.tenant_id == context.tenant_id, self._eligible(now))
            .order_by(outbox.c.event_id)
            .limit(page_limit(limit))
            .with_for_update(skip_locked=True)
        )

    def _payload(self, row: RowMapping) -> dict[str, Any]:
        try:
            payload = json.loads(row["payload_json"])
            if (
                type(payload) is not dict
                or set(payload) != {"project_id", "revision", "semantic_digest"}
                or payload["project_id"] != row["project_id"]
                or payload["revision"] != row["revision"]
                or not isinstance(payload["semantic_digest"], str)
                or not re.fullmatch(r"[a-f0-9]{64}", payload["semantic_digest"])
            ):
                raise EnterpriseError("outbox_event_corrupt")
            return payload
        except (ValueError, TypeError):
            raise EnterpriseError("outbox_event_corrupt") from None

    def claim(
        self, context: AccessContext, limit: int = 50, lease_seconds: int = 30
    ) -> list[dict[str, Any]]:
        require(context, "events.publish")
        page_limit(limit)
        if type(lease_seconds) is not int or not 1 <= lease_seconds <= 60:
            raise EnterpriseError("lease_limit")
        claimed = []
        with self.engine.begin() as connection:
            now = self.clock(connection)
            if not isinstance(now, (int, float)) or not math.isfinite(now):
                raise EnterpriseError("database_clock_invalid")
            rows = (
                connection.execute(self.claim_query(context, now, limit))
                .mappings()
                .all()
            )
            for row in rows:
                payload = self._payload(row)
                token = uuid.uuid4().hex
                result = connection.execute(
                    update(outbox)
                    .where(
                        outbox.c.tenant_id == context.tenant_id,
                        outbox.c.event_id == row["event_id"],
                        outbox.c.generation == row["generation"],
                        self._eligible(now),
                    )
                    .values(
                        state="leased",
                        generation=row["generation"] + 1,
                        lease_owner=context.actor_id,
                        lease_token=token,
                        lease_until=now + lease_seconds,
                    )
                )
                if result.rowcount == 1:
                    claimed.append(
                        {
                            "event_id": row["event_id"],
                            "topic": row["topic"],
                            "payload": payload,
                            "lease_token": token,
                            "generation": row["generation"] + 1,
                        }
                    )
        return claimed

    def ack(
        self, context: AccessContext, event_id: str, lease_token: str, generation: int
    ) -> None:
        require(context, "events.publish")
        identifier(event_id)
        identifier(lease_token)
        if type(generation) is not int or generation < 1:
            raise EnterpriseError("lease_stale")
        with self.engine.begin() as connection:
            now = self.clock(connection)
            if not isinstance(now, (int, float)) or not math.isfinite(now):
                raise EnterpriseError("database_clock_invalid")
            result = connection.execute(
                update(outbox)
                .where(
                    outbox.c.tenant_id == context.tenant_id,
                    outbox.c.event_id == event_id,
                    outbox.c.state == "leased",
                    outbox.c.lease_owner == context.actor_id,
                    outbox.c.lease_token == lease_token,
                    outbox.c.generation == generation,
                    outbox.c.lease_until > now,
                )
                .values(
                    state="published",
                    lease_owner=None,
                    lease_token=None,
                    lease_until=None,
                )
            )
            if result.rowcount != 1:
                raise EnterpriseError("lease_stale")
