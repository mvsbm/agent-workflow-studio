# Isolated LangGraph spike and Git/CI delivery

## Plan before implementation

User approved LangGraph 1.2.12 in an isolated test environment and requested Git and
CI/CD. SRP: dependency lock, framework fixture tests and CI evidence delivery are
separate. DIP/Adapter: test the maintained SDK directly, not a substitute runtime.
Least authority: fixture network/tracing disabled, read-only GitHub token, bounded
job timeout, no deployment credentials or main Comfy imports. Fail fast: pinned SDK
version, hash-locked installs, local lint then tests; CI policy/syntax checks and real
fixtures. Skipped spike must not satisfy CI.

Create a dedicated feature branch without resetting/stashing or staging unrelated
work. Commit/push were initially gated; the user subsequently explicitly approved
a scoped spike/CI commit and feature-branch push, not a merge or deployment.
Generate a reproducible SDK dependency lock using existing uv/public PyPI, install
only into a temporary Python 3.12 venv, and run the five real SDK fixtures via
unittest discovery so package-level Comfy startup is not imported.

Add a scoped GitHub Actions pipeline for this feasibility spike with immutable
Action revisions. Pull requests/pushes run validation; manual workflow dispatch may
also publish a synthetic evidence artifact. This is delivery of test evidence, not
production deployment. Production CD requires an actual reviewed runtime artifact,
OIDC/project authorization, PostgreSQL integration, service target/credentials and
configured protected environment; none are implied by a workflow file.

Verification: run exactly the local CI fixture commands with the isolated lock;
check dependency consistency, lint/format, gate behavior and workflow structure.
Do not run a full suite/build, SDK/model/provider/automation contacts, main reload,
remote pipeline trigger, commit/push or production deployment implicitly.

## Delivered bounded slice

- Branch: `feat/enterprise-agent-runtime-ci`; based on the existing checkout with
  unrelated work retained. Only the spike/CI files are authorized for commit/push;
  no merge or production deployment is authorized.
- [Workflow](../../.github/workflows/agent-runtime-spike.yml): PR/scoped push CI;
  manual dispatch optionally delivers synthetic evidence after success. Read-only
  contents permission, no secret references, ephemeral Ubuntu runner, 10-minute
  timeout, credential persistence off, immutable Action commits and pinned uv 0.9.27.
- `ci/requirements-langgraph-spike.in` pins LangGraph 1.2.12;
  `ci/requirements-langgraph-spike.lock` locks all 38 resolved packages/hashes for
  Python 3.12 with universal resolution. Install uses public PyPI and wheels only.
  Lock was generated using existing uv; no hand-maintained transitive versions.
- `ci/run_langgraph_spike.py` refuses missing permission/dependency/wrong SDK,
  unexpected case IDs/counts, skips and failures. JSON report contains source
  hashes and `synthetic: true`, `production_ready: false`; hashes are not signed
  provenance or evidence of an immutable Git commit.
- `ci/test_spike_runner.py`: six runner-admission tests (mock result/version only,
  not substituted SDK execution). `ci/test_workflow_policy.py`: four YAML policy
  checks using PyYAML already in the SDK dependency lock. These are scoped checks,
  not a full GitHub Actions validator or universal workflow security certification.
- Isolated SDK env: `/tmp/agent-workflow-langgraph-spike-venv`, Python 3.12.12,
  `include-system-site-packages` disabled. Real SDK fixture runner passes **5/5**,
  zero skips, zero guarded fixture network calls; runner/policy tests pass **10/10**.
  [Recorded synthetic report](agent-runtime-spike-result.json).
- uv dependency consistency, local Ruff lint/format and runner mypy pass. Reused
  existing lint/type tools without installing them into the SDK or Comfy env.
  Main Comfy still has no LangGraph; SQLAlchemy/Alembic versions unchanged. No
  native queue/provider/webhook/database/service/deployment contacts occurred.

## Repeat the verified fixture commands

```sh
uv venv --python 3.12 /tmp/agent-workflow-langgraph-spike-venv
uv pip install --no-config --default-index https://pypi.org/simple \
  --python /tmp/agent-workflow-langgraph-spike-venv/bin/python \
  --require-hashes --only-binary :all: -r ci/requirements-langgraph-spike.lock
uv pip check --python /tmp/agent-workflow-langgraph-spike-venv/bin/python
/tmp/agent-workflow-langgraph-spike-venv/bin/python -m unittest discover \
  -s ci -p 'test_*.py' -v
COMFYUI_LANGGRAPH_SPIKE_ALLOWED=1 \
  /tmp/agent-workflow-langgraph-spike-venv/bin/python ci/run_langgraph_spike.py \
  --report /tmp/agent-workflow-langgraph-spike-result.json
```

Dependency installation is approved for this isolated spike only. Do not replace an
existing environment or assume these commands authorize production setup.

## Remaining Git/CI/CD gates

Scoped commit/push is now approved. GitHub/Linux execution remains unverified until
the branch run completes; local checks alone are not remote-run evidence.
A new manual-dispatch workflow must be available on the default branch before the
GitHub UI can offer its dispatch; do not implicitly merge to enable it.

Delivery currently means a seven-day synthetic evidence artifact, not deploying an
agent runtime. Production CD remains blocked on real runtime artifact/integration,
OIDC/project ACLs, PostgreSQL/checkpointer/restart tests, security/dependency/license
review, workload/SLOs, protected deployment environments and explicit target
approval. A manual dispatch alone is not a protected production approval process.

No capacity, model-quality, comparative framework superiority, HA, strong tenant
isolation or SOTA certification is inferred from these fifteen tests.
