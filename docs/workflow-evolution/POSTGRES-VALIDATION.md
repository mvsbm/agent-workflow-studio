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

## Verification gates

Local: ephemeral fixture lifecycle/admission tests, import/compile, Ruff, mypy and
existing E0 focused tests; driver installation/consistency checked in isolated env.
Remote: pinned image pull, actual PostgreSQL migration/concurrency/restart cases and
no-skips gate. CI uses read-only token/immutable actions/ephemeral runner; no secret
references, production credentials, raw DB URL output or automatic deployment.
