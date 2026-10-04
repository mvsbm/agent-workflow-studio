import json
import re
import uuid
from collections.abc import Callable
from typing import Any

from sqlalchemy import insert, select, update
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.exc import IntegrityError, OperationalError

from .enterprise_contracts import (
    AccessContext,
    EnterpriseError,
    digest,
    encode,
    identifier,
    page_limit,
    require,
)
from .enterprise_schema import outbox, projects, receipts, revisions


class ProjectRepository:
    def __init__(
        self,
        engine: Engine,
        verifier: Callable[[dict[str, Any], dict[str, Any]], str],
        event_id: Callable[[], str] | None = None,
    ) -> None:
        if not callable(verifier):
            raise EnterpriseError("graph_verifier_required")
        self.engine = engine
        self.verifier = verifier
        self.event_id = event_id or (lambda: uuid.uuid4().hex)

    def _snapshot(
        self, graph: dict[str, Any], policy: dict[str, Any]
    ) -> tuple[str, str, str, str]:
        graph_json, policy_json = encode(graph), encode(policy)
        if type(graph) is not dict or type(policy) is not dict:
            raise EnterpriseError("graph_invalid")
        snapshot = {"graph": json.loads(graph_json), "policy": json.loads(policy_json)}
        try:
            semantic = self.verifier(snapshot["graph"], snapshot["policy"])
        except (ValueError, TypeError):
            raise EnterpriseError("graph_invalid") from None
        if (
            encode(snapshot["graph"]) != graph_json
            or encode(snapshot["policy"]) != policy_json
        ):
            raise EnterpriseError("graph_verifier_mutated")
        if not isinstance(semantic, str) or not re.fullmatch(r"[a-f0-9]{64}", semantic):
            raise EnterpriseError("graph_identity_invalid")
        return (
            graph_json,
            policy_json,
            semantic,
            digest({**snapshot, "semantic_digest": semantic}),
        )

    def _revision(
        self,
        connection: Connection,
        context: AccessContext,
        project_id: str,
        revision: int,
    ) -> dict[str, Any]:
        row = (
            connection.execute(
                select(revisions).where(
                    revisions.c.tenant_id == context.tenant_id,
                    revisions.c.project_id == project_id,
                    revisions.c.revision == revision,
                )
            )
            .mappings()
            .first()
        )
        if not row:
            raise EnterpriseError("revision_not_found")
        try:
            snapshot = {
                "graph": json.loads(row["graph_json"]),
                "policy": json.loads(row["policy_json"]),
            }
            if (
                row["schema_version"] != 1
                or digest({**snapshot, "semantic_digest": row["semantic_digest"]})
                != row["content_digest"]
            ):
                raise EnterpriseError("stored_revision_corrupt")
        except (ValueError, TypeError):
            raise EnterpriseError("stored_revision_corrupt") from None
        return {
            "project_id": project_id,
            "revision": revision,
            "parent_revision": row["parent_revision"],
            "semantic_digest": row["semantic_digest"],
            "created_by": row["created_by"],
            "authority": "unapproved_draft",
            **snapshot,
        }

    def _replay(
        self,
        connection: Connection,
        context: AccessContext,
        project_id: str,
        key: str,
        operation: str,
        input_digest: str,
    ) -> dict[str, Any] | None:
        receipt = (
            connection.execute(
                select(receipts).where(
                    receipts.c.tenant_id == context.tenant_id,
                    receipts.c.project_id == project_id,
                    receipts.c.request_key == key,
                )
            )
            .mappings()
            .first()
        )
        if not receipt:
            return None
        if receipt["operation"] != operation or receipt["input_digest"] != input_digest:
            raise EnterpriseError("request_reused")
        return self._revision(connection, context, project_id, receipt["revision"])

    def _append(
        self,
        connection: Connection,
        context: AccessContext,
        project_id: str,
        revision: int,
        key: str,
        operation: str,
        input_digest: str,
        snapshot: tuple[str, str, str, str],
    ) -> dict[str, Any]:
        graph_json, policy_json, semantic, content = snapshot
        connection.execute(
            insert(revisions).values(
                tenant_id=context.tenant_id,
                project_id=project_id,
                revision=revision,
                parent_revision=revision - 1 if revision else None,
                created_by=context.actor_id,
                semantic_digest=semantic,
                content_digest=content,
                graph_json=graph_json,
                policy_json=policy_json,
            )
        )
        connection.execute(
            insert(receipts).values(
                tenant_id=context.tenant_id,
                project_id=project_id,
                request_key=key,
                operation=operation,
                input_digest=input_digest,
                revision=revision,
            )
        )
        connection.execute(
            insert(outbox).values(
                tenant_id=context.tenant_id,
                event_id=identifier(self.event_id()),
                project_id=project_id,
                revision=revision,
                topic="studio.revision.saved",
                payload_json=encode(
                    {
                        "project_id": project_id,
                        "revision": revision,
                        "semantic_digest": semantic,
                    }
                ),
            )
        )
        return self._revision(connection, context, project_id, revision)

    def create(
        self,
        context: AccessContext,
        project_id: str,
        name: str,
        graph: dict[str, Any],
        policy: dict[str, Any],
        request_key: str,
    ) -> dict[str, Any]:
        require(context, "project.write")
        identifier(project_id)
        identifier(request_key)
        if not isinstance(name, str) or not name.strip() or len(name) > 256:
            raise EnterpriseError("project_name_invalid")
        graph, policy = json.loads(encode(graph)), json.loads(encode(policy))
        input_digest = digest(
            {
                "operation": "create",
                "actor_id": context.actor_id,
                "name": name,
                "graph": graph,
                "policy": policy,
            }
        )
        try:
            with self.engine.begin() as connection:
                replay = self._replay(
                    connection, context, project_id, request_key, "create", input_digest
                )
                if replay is not None:
                    return replay
                snapshot = self._snapshot(graph, policy)
                connection.execute(
                    insert(projects).values(
                        tenant_id=context.tenant_id,
                        project_id=project_id,
                        name=name,
                        created_by=context.actor_id,
                        head_revision=0,
                    )
                )
                return self._append(
                    connection,
                    context,
                    project_id,
                    0,
                    request_key,
                    "create",
                    input_digest,
                    snapshot,
                )
        except IntegrityError:
            with self.engine.connect() as connection:
                replay = self._replay(
                    connection, context, project_id, request_key, "create", input_digest
                )
                if replay is not None:
                    return replay
            raise EnterpriseError("storage_conflict") from None
        except OperationalError:
            raise EnterpriseError("storage_unavailable") from None

    def save(
        self,
        context: AccessContext,
        project_id: str,
        expected_revision: int,
        graph: dict[str, Any],
        policy: dict[str, Any],
        request_key: str,
    ) -> dict[str, Any]:
        require(context, "project.write")
        identifier(project_id)
        identifier(request_key)
        if (
            type(expected_revision) is not int
            or not 0 <= expected_revision < 2147483647
        ):
            raise EnterpriseError("revision_invalid")
        graph, policy = json.loads(encode(graph)), json.loads(encode(policy))
        input_digest = digest(
            {
                "operation": "save",
                "actor_id": context.actor_id,
                "expected_revision": expected_revision,
                "graph": graph,
                "policy": policy,
            }
        )
        try:
            with self.engine.begin() as connection:
                replay = self._replay(
                    connection, context, project_id, request_key, "save", input_digest
                )
                if replay is not None:
                    return replay
                snapshot = self._snapshot(graph, policy)
                result = connection.execute(
                    update(projects)
                    .where(
                        projects.c.tenant_id == context.tenant_id,
                        projects.c.project_id == project_id,
                        projects.c.head_revision == expected_revision,
                    )
                    .values(head_revision=expected_revision + 1)
                )
                if result.rowcount != 1:
                    replay = self._replay(
                        connection,
                        context,
                        project_id,
                        request_key,
                        "save",
                        input_digest,
                    )
                    if replay is not None:
                        return replay
                    exists = connection.execute(
                        select(projects.c.project_id).where(
                            projects.c.tenant_id == context.tenant_id,
                            projects.c.project_id == project_id,
                        )
                    ).first()
                    raise EnterpriseError(
                        "revision_conflict" if exists else "project_not_found"
                    )
                return self._append(
                    connection,
                    context,
                    project_id,
                    expected_revision + 1,
                    request_key,
                    "save",
                    input_digest,
                    snapshot,
                )
        except IntegrityError:
            with self.engine.connect() as connection:
                replay = self._replay(
                    connection, context, project_id, request_key, "save", input_digest
                )
                if replay is not None:
                    return replay
            raise EnterpriseError("storage_conflict") from None
        except OperationalError:
            raise EnterpriseError("storage_unavailable") from None

    def read(
        self, context: AccessContext, project_id: str, revision: int | None = None
    ) -> dict[str, Any]:
        require(context, "project.read")
        identifier(project_id)
        if revision is not None and (
            type(revision) is not int or not 0 <= revision <= 2147483647
        ):
            raise EnterpriseError("revision_invalid")
        with self.engine.connect() as connection:
            head = connection.execute(
                select(projects.c.head_revision).where(
                    projects.c.tenant_id == context.tenant_id,
                    projects.c.project_id == project_id,
                )
            ).scalar_one_or_none()
            if head is None:
                raise EnterpriseError("project_not_found")
            return self._revision(
                connection, context, project_id, head if revision is None else revision
            )

    def list_projects(
        self, context: AccessContext, limit: int = 50, after: str | None = None
    ) -> list[dict[str, Any]]:
        require(context, "project.read")
        page_limit(limit)
        query = select(
            projects.c.project_id, projects.c.name, projects.c.head_revision
        ).where(projects.c.tenant_id == context.tenant_id)
        if after is not None:
            query = query.where(projects.c.project_id > identifier(after))
        with self.engine.connect() as connection:
            return [
                dict(row)
                for row in connection.execute(
                    query.order_by(projects.c.project_id).limit(limit)
                ).mappings()
            ]
