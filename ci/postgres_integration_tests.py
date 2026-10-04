import hashlib
import importlib
import importlib.util
import json
import sys
import types
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
from typing import Any

from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
from postgres_fixture import PostgresFixture
from sqlalchemy import create_engine, insert, select, update
from sqlalchemy.engine import URL
from sqlalchemy.exc import IntegrityError
from sqlalchemy.schema import CreateSchema, DropSchema

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = "_studio_pg_probe"
package = types.ModuleType(PACKAGE)
package.__path__ = [str(ROOT / "custom_nodes/agent_workflow_studio")]
sys.modules[PACKAGE] = package
storage = importlib.import_module(PACKAGE + ".enterprise_storage")
schema = importlib.import_module(PACKAGE + ".enterprise_schema")
outbox_module = importlib.import_module(PACKAGE + ".enterprise_outbox")


def verify(graph: dict[str, Any], policy: dict[str, Any]) -> str:
    if type(graph) is not dict or not isinstance(graph.get("text"), str):
        raise TypeError("fixture_graph_invalid")
    return hashlib.sha256(
        json.dumps([graph, policy], sort_keys=True).encode()
    ).hexdigest()


class PostgresIntegrationTests(unittest.TestCase):
    fixture: PostgresFixture

    def setUp(self) -> None:
        arguments = self.fixture.connect_arguments()
        self.url = URL.create(
            "postgresql+psycopg",
            username=arguments["user"],
            password=arguments["password"],
            host=arguments["host"],
            port=arguments["port"],
            database=arguments["dbname"],
        )
        self.schema_name = "studio_" + uuid.uuid4().hex
        self.admin = create_engine(self.url, connect_args={"connect_timeout": 2})
        with self.admin.begin() as connection:
            connection.execute(CreateSchema(self.schema_name))
        self.engine = self.make_engine()
        migration_path = (
            ROOT
            / "custom_nodes/agent_workflow_studio/enterprise_migrations/versions/0001_control_plane.py"
        )
        spec = importlib.util.spec_from_file_location("pg_migration", migration_path)
        if spec is None or spec.loader is None:
            raise RuntimeError("postgres_migration_unavailable")
        migration = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(migration)
        with self.engine.begin() as connection:
            context = MigrationContext.configure(
                connection, opts={"version_table": "studio_alembic_version"}
            )
            with Operations.context(context):
                migration.upgrade()
            context.stamp(
                ScriptDirectory(str(migration_path.parents[1])), "studio_0001"
            )
        self.repository = storage.ProjectRepository(self.engine, verify)
        self.a = storage.AccessContext(
            "tenant-a", "user-a", frozenset({"project.read", "project.write"})
        )
        self.b = storage.AccessContext("tenant-b", "user-b", self.a.permissions)
        self.worker = storage.AccessContext(
            "tenant-a", "worker-a", frozenset({"events.publish"})
        )
        self.publisher = storage.RevisionOutbox(self.engine)
        self.graph = {"text": "hello"}
        self.policy = {"version": 1}

    def make_engine(self):
        return create_engine(
            self.url,
            connect_args={
                "connect_timeout": 2,
                "options": f"-c search_path={self.schema_name} -c statement_timeout=5000 -c lock_timeout=3000",
            },
            pool_size=4,
            max_overflow=4,
        )

    def tearDown(self) -> None:
        self.engine.dispose()
        with self.admin.begin() as connection:
            connection.execute(DropSchema(self.schema_name, cascade=True))
        self.admin.dispose()

    def create(self, context=None):
        return self.repository.create(
            context or self.a,
            "project-1",
            "Fixture project",
            self.graph,
            self.policy,
            "create-1",
        )

    def test_migration_stamp_and_schema_agree(self) -> None:
        with self.engine.connect() as connection:
            context = MigrationContext.configure(
                connection, opts={"version_table": "studio_alembic_version"}
            )
            self.assertEqual(context.get_current_revision(), "studio_0001")
            self.assertEqual(compare_metadata(context, schema.metadata), [])

    def test_tenant_scope_and_composite_foreign_keys(self) -> None:
        self.create()
        with self.assertRaisesRegex(storage.EnterpriseError, "project_not_found"):
            self.repository.read(self.b, "project-1")
        with self.assertRaises(IntegrityError), self.engine.begin() as connection:
            connection.execute(
                insert(schema.receipts).values(
                    tenant_id="tenant-b",
                    project_id="project-1",
                    request_key="bad-link",
                    operation="save",
                    input_digest="0" * 64,
                    revision=0,
                )
            )
        self.create(self.b)
        self.repository.save(
            self.a, "project-1", 0, {"text": "changed"}, self.policy, "save-1"
        )
        self.assertEqual(self.repository.read(self.b, "project-1")["graph"], self.graph)
        self.assertEqual(len(self.repository.list_projects(self.a)), 1)

    def test_permissions_and_raw_context_fail_closed(self) -> None:
        self.create()
        with self.assertRaisesRegex(storage.EnterpriseError, "access_context_required"):
            self.repository.read({"tenant_id": "tenant-a"}, "project-1")
        viewer = storage.AccessContext(
            "tenant-a", "viewer", frozenset({"project.read"})
        )
        with self.assertRaisesRegex(storage.EnterpriseError, "permission_denied"):
            self.repository.save(
                viewer, "project-1", 0, self.graph, self.policy, "save-1"
            )

    def parallel(self, operation):
        barrier = Barrier(2)

        def synchronized(graph, policy):
            barrier.wait(timeout=5)
            return verify(graph, policy)

        repository = storage.ProjectRepository(self.engine, synchronized)
        with ThreadPoolExecutor(max_workers=2) as pool:
            return list(pool.map(lambda index: operation(repository, index), [1, 2]))

    def test_competing_cas_has_one_winner(self) -> None:
        self.create()

        def operation(repository, index):
            try:
                return repository.save(
                    self.a,
                    "project-1",
                    0,
                    {"text": str(index)},
                    self.policy,
                    f"save-{index}",
                )["revision"]
            except storage.EnterpriseError as error:
                return str(error)

        self.assertCountEqual(self.parallel(operation), [1, "revision_conflict"])
        self.assertEqual(self.repository.read(self.a, "project-1")["revision"], 1)

    def test_concurrent_same_request_save_replays(self) -> None:
        self.create()
        results = self.parallel(
            lambda repository, _: repository.save(
                self.a, "project-1", 0, self.graph, self.policy, "save-1"
            )
        )
        self.assertEqual(results[0], results[1])
        self.assertEqual(self.repository.read(self.a, "project-1")["revision"], 1)

    def test_concurrent_same_request_create_replays(self) -> None:
        results = self.parallel(
            lambda repository, _: repository.create(
                self.a,
                "project-1",
                "Fixture project",
                self.graph,
                self.policy,
                "create-1",
            )
        )
        self.assertEqual(results[0], results[1])
        with self.engine.connect() as connection:
            self.assertEqual(len(connection.execute(select(schema.outbox)).all()), 1)

    def test_revision_receipt_event_rollback_atomically(self) -> None:
        self.create()
        failing = storage.ProjectRepository(
            self.engine, verify, event_id=lambda: "duplicate-event"
        )
        failing.save(self.a, "project-1", 0, {"text": "one"}, self.policy, "save-1")
        with self.assertRaises(storage.EnterpriseError):
            failing.save(self.a, "project-1", 1, {"text": "two"}, self.policy, "save-2")
        self.assertEqual(self.repository.read(self.a, "project-1")["revision"], 1)
        result = self.repository.save(
            self.a, "project-1", 1, {"text": "two"}, self.policy, "save-2"
        )
        self.assertEqual(result["revision"], 2)

    def test_engine_reopen_preserves_copied_unapproved_history(self) -> None:
        original = self.create()
        original["graph"]["text"] = "caller changed"
        self.repository.save(
            self.a, "project-1", 0, {"text": "next"}, self.policy, "save-1"
        )
        self.engine.dispose()
        self.engine = self.make_engine()
        repository = storage.ProjectRepository(self.engine, verify)
        self.assertEqual(repository.read(self.a, "project-1", 0)["graph"], self.graph)
        result = repository.read(self.a, "project-1")
        self.assertEqual(result["authority"], "unapproved_draft")
        self.assertNotIn("approval_token", result)

    def test_container_restart_preserves_data_not_approval(self) -> None:
        self.create()
        self.engine.dispose()
        self.admin.dispose()
        self.fixture.restart()
        self.url = self.url.set(port=self.fixture.port)
        self.engine = self.make_engine()
        self.admin = create_engine(self.url)
        result = storage.ProjectRepository(self.engine, verify).read(
            self.a, "project-1"
        )
        self.assertEqual(result["authority"], "unapproved_draft")
        self.assertNotIn("approval_token", result)

    def test_skip_locked_does_not_block_on_claimed_row(self) -> None:
        self.create()
        self.repository.save(self.a, "project-1", 0, self.graph, self.policy, "save-1")
        with self.engine.begin() as connection:
            now = outbox_module.database_time(connection)
            locked = (
                connection.execute(self.publisher.claim_query(self.worker, now, 1))
                .mappings()
                .one()
            )
            other = self.publisher.claim(self.worker, 1, 30)
            self.assertEqual(len(other), 1)
            self.assertNotEqual(other[0]["event_id"], locked["event_id"])

    def test_competing_publishers_claim_disjoint_events(self) -> None:
        self.create()
        self.repository.save(self.a, "project-1", 0, self.graph, self.policy, "save-1")
        barrier = Barrier(2)

        def clock(connection):
            barrier.wait(timeout=5)
            return outbox_module.database_time(connection)

        publisher = storage.RevisionOutbox(self.engine, clock)
        with ThreadPoolExecutor(max_workers=2) as pool:
            batches = list(
                pool.map(lambda _: publisher.claim(self.worker, 1, 30), range(2))
            )
        self.assertEqual([len(batch) for batch in batches], [1, 1])
        self.assertEqual(len({batch[0]["event_id"] for batch in batches}), 2)

    def test_database_clock_expiry_and_foreign_owner_fencing(self) -> None:
        self.create()
        claimed = self.publisher.claim(self.worker, 1, 30)[0]
        other = storage.AccessContext("tenant-b", "worker-a", self.worker.permissions)
        with self.assertRaisesRegex(storage.EnterpriseError, "lease_stale"):
            self.publisher.ack(
                other,
                claimed["event_id"],
                claimed["lease_token"],
                claimed["generation"],
            )
        with self.engine.begin() as connection:
            now = outbox_module.database_time(connection)
            connection.execute(update(schema.outbox).values(lease_until=now - 1))
        with self.assertRaisesRegex(storage.EnterpriseError, "lease_stale"):
            self.publisher.ack(
                self.worker,
                claimed["event_id"],
                claimed["lease_token"],
                claimed["generation"],
            )
        newer = self.publisher.claim(self.worker, 1, 30)[0]
        self.assertGreater(newer["generation"], claimed["generation"])
        self.publisher.ack(
            self.worker, newer["event_id"], newer["lease_token"], newer["generation"]
        )

    def test_corrupt_identity_is_rejected(self) -> None:
        self.create()
        with self.engine.begin() as connection:
            connection.execute(
                update(schema.revisions).values(semantic_digest="0" * 64)
            )
        with self.assertRaisesRegex(storage.EnterpriseError, "stored_revision_corrupt"):
            self.repository.read(self.a, "project-1")

    def test_completed_receipt_replay_survives_catalog_change(self) -> None:
        self.create()
        first = self.repository.save(
            self.a, "project-1", 0, self.graph, self.policy, "save-1"
        )

        def changed(graph, policy):
            raise ValueError("fixture_catalog_changed")

        repository = storage.ProjectRepository(self.engine, changed)
        self.assertEqual(
            repository.save(self.a, "project-1", 0, self.graph, self.policy, "save-1"),
            first,
        )
        with self.assertRaisesRegex(storage.EnterpriseError, "graph_invalid"):
            repository.save(self.a, "project-1", 1, self.graph, self.policy, "save-new")
