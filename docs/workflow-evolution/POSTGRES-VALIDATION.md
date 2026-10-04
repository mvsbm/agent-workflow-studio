# PostgreSQL acceptance slice — plan before code

User approved isolated psycopg installation and disposable PostgreSQL locally/in CI.
SSO provider was not selected. No auth provider, workspace migration, deployment or
main Comfy dependency change is authorized. Local Docker CLI points to a stopped
Colima daemon; do not start/reconfigure that VM implicitly. Real PostgreSQL tests
will run on GitHub's disposable Docker runner; local tests cover fixture admission.

## Principles and bounded implementation

SRP: Docker fixture lifecycle, pure storage imports, integration cases, result runner
and CI configuration stay separate. DIP: real SQLAlchemy engine and psycopg driver;
existing graph verifier remains injected. Transactional Outbox/CAS/fencing patterns
are exercised against PostgreSQL, not mocked or inferred from SQLite. Existing
Alembic migration supplies schema operations; no new migration framework.

Create a separate enterprise-PostgreSQL feature branch. Scope commits to the existing
pure E0 storage modules/migration and new PostgreSQL fixture/tests/CI/docs; retain
unrelated P0–P6/UI/stock/user changes unstaged. No merge or production publication.

Resolve an official PostgreSQL 18 image to an immutable OCI digest. Generate random
throwaway database credentials in memory, restrict host port to numeric loopback,
never commit/log credential values, and remove only this fixture's container and
anonymous volumes on exit. No production DSN input or fallback to an existing DB.
Use a hash-locked isolated Python 3.12 dependency set (existing SQLAlchemy/Alembic
versions plus psycopg binary). No host PostgreSQL installation.

Test real migration/stamp/schema agreement, copied unapproved history, tenant query
scoping and composite FKs, competing CAS saves, same-request create/save races,
atomic rollback, SKIP LOCKED disjoint claims, database-clock stale/foreign lease ack,
engine reopen and actual container restart. Require exact cases/counts, no skips,
SDK-free report with synthetic/non-production labels and source/image provenance.
Tests may destroy only their generated schema/container. No real model, Comfy queue,
provider, sandbox or webhook effects.

Tenant-scoped SQL is not OIDC/project RBAC or PostgreSQL RLS: existing AccessContext
is a trusted internal contract, not authentication. Those remain a separate next
slice once an identity provider is chosen. No enterprise capacity, HA, backup-restore
or production security guarantee is inferred from this small acceptance suite.

## Delivered and verified

Branch `feat/enterprise-postgres-validation` contains the pure E0 storage library,
frozen Alembic revision, fixture/runner/cases and dedicated PostgreSQL CI workflow.
Isolated `/tmp/agent-workflow-postgres-venv` holds seven hash-locked dependencies;
main Comfy has no new driver or database. Local fixture tests pass **7/7**, and
Ruff/mypy/uv dependency consistency checks pass. No local real-PostgreSQL execution
occurred because Colima is stopped; it was not started or reconfigured.

[GitHub PostgreSQL run 37180474588](https://github.com/mvsbm/agent-workflow-studio/actions/runs/37180474588)
passes **14/14 real PostgreSQL cases**, zero skips/errors/failures.
[Synthetic artifact report](postgres-acceptance-result.json) was downloaded and
checked against every recorded source hash. The companion SDK CI also passed.
The first remote run reproduced a restart fixture bug: the randomly published port
was treated as stable. A failing local regression test now requires rediscovery of
the numeric-loopback binding and rebuilding the test engine URL after restart.
No storage/data failure was masked or converted to a passing outcome.

CI uses pinned Ubuntu 24.04, immutable Node-24 checkout/setup-python Actions, pinned
uv/image and a read-only token. Upload-artifact v4 currently emits a nonblocking
Node-20 deprecation annotation; Action maintenance remains a follow-up. Reports
are seven-day synthetic artifacts, not deployment packages. No merge or production
migration/deployment occurred; unrelated working changes remain outside commits.

Acceptance covers deterministic fixture graph verification, not the real Comfy
registry/LLM/sandbox/provider/authentication boundary. Alembic operations plus
version stamp were exercised directly; a production migration CLI/env/rollback
runbook is still pending. No performance/HA/backup restore or RLS was tested.
SSO provider selection remains unanswered and blocks real identity integration.

## Verification gates

Local: ephemeral fixture lifecycle/admission tests, import/compile, Ruff, mypy and
existing E0 focused tests; driver installation/consistency checked in isolated env.
Remote: pinned image pull, actual PostgreSQL migration/concurrency/restart cases and
no-skips gate. CI uses read-only token/immutable actions/ephemeral runner; no secret
references, production credentials, raw DB URL output or automatic deployment.
