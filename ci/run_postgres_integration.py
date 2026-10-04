import argparse
import hashlib
import json
import os
import unittest
from pathlib import Path

from postgres_fixture import PostgresFixture

EXPECTED_CASES = frozenset(
    {
        "test_migration_stamp_and_schema_agree",
        "test_tenant_scope_and_composite_foreign_keys",
        "test_permissions_and_raw_context_fail_closed",
        "test_competing_cas_has_one_winner",
        "test_concurrent_same_request_save_replays",
        "test_concurrent_same_request_create_replays",
        "test_revision_receipt_event_rollback_atomically",
        "test_engine_reopen_preserves_copied_unapproved_history",
        "test_container_restart_preserves_data_not_approval",
        "test_skip_locked_does_not_block_on_claimed_row",
        "test_competing_publishers_claim_disjoint_events",
        "test_database_clock_expiry_and_foreign_owner_fencing",
        "test_corrupt_identity_is_rejected",
        "test_completed_receipt_replay_survives_catalog_change",
    }
)


def run(report_path: Path) -> int:
    if os.environ.get("COMFYUI_POSTGRES_TEST_ALLOWED") != "1":
        raise RuntimeError("postgres_fixture_approval_required")
    from postgres_integration_tests import PostgresIntegrationTests

    with PostgresFixture() as fixture:
        PostgresIntegrationTests.fixture = fixture
        suite = unittest.TestLoader().loadTestsFromTestCase(PostgresIntegrationTests)
        names = []
        for case in suite:
            if not isinstance(case, unittest.TestCase):
                raise TypeError("postgres_discovery_invalid")
            names.append(case.id())
        result = unittest.TextTestRunner(verbosity=2).run(suite)
        accepted = (
            result.wasSuccessful()
            and not result.skipped
            and result.testsRun == len(EXPECTED_CASES)
            and {name.rsplit(".", 1)[-1] for name in names} == EXPECTED_CASES
        )
        root = Path(__file__).resolve().parents[1]
        files = [
            "ci/postgres-image.txt",
            "ci/requirements-postgres.in",
            "ci/requirements-postgres.lock",
            "ci/postgres_fixture.py",
            "ci/postgres_integration_tests.py",
            "ci/run_postgres_integration.py",
        ]
        files += [
            "custom_nodes/agent_workflow_studio/" + name
            for name in [
                "enterprise_contracts.py",
                "enterprise_schema.py",
                "enterprise_projects.py",
                "enterprise_outbox.py",
                "enterprise_storage.py",
                "enterprise_migrations/versions/0001_control_plane.py",
            ]
        ]
        report = {
            "schema_version": "WORKFLOW_POSTGRES_ACCEPTANCE_V1",
            "synthetic": True,
            "production_ready": False,
            "image": fixture.image,
            "tests_run": result.testsRun,
            "errors": len(result.errors),
            "failures": len(result.failures),
            "skipped": len(result.skipped),
            "case_ids": names,
            "accepted": accepted,
            "source_sha256": {
                relative: hashlib.sha256((root / relative).read_bytes()).hexdigest()
                for relative in files
            },
        }
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(
            json.dumps(report, sort_keys=True, indent=2) + "\n", encoding="utf-8"
        )
        return 0 if accepted else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", type=Path, required=True)
    raise SystemExit(run(parser.parse_args().report))
