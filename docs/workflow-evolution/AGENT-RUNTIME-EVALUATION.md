# Agent runtime evaluation — LangGraph candidate, Temporal deferred

## Status and evidence

Primary documentation and PyPI metadata reviewed. LangGraph, its checkpoint packages,
Temporal and PydanticAI are absent from the current Comfy runtime. This is a design
assessment plus an independently approved, completed synthetic spike, not a measured
model-quality/framework comparison or production adoption. Installation/runtime
use require separate approval; the user subsequently authorized only the isolated
LangGraph spike and Git/CI work. Public documentation GETs were made; no model/provider, production
service, Comfy queue or generated-code execution contacts were made.

The retrieved PyPI candidate is `langgraph==1.2.12` (Python >=3.10); dependencies
include langchain-core >=1.4.7,<2, checkpoint >=4.1.0,<5, prebuilt >=1.1.0,<1.2,
SDK >=0.4.2,<0.5, Pydantic >=2.7.4 and xxhash >=3.5. Full dependency resolution,
license/security assessment and compatibility remain gates, not verified claims.
Do not install this dependency tree into the running Comfy environment for a spike.

## Problem, principles and approach (before spike code)

Reuse an established stateful planning runtime without giving checkpointed state
execution/editing/provider authority. SRP separates proposal orchestration, native
Comfy execution, persistence and authority. DIP uses the Studio verifier/repositories
and independently authorized provider adapters, not framework-managed permissions.
Adapter preserves existing IR/proposal contracts. State-machine and idempotent
command patterns handle resume; conditional adoption avoids two redundant schedulers.

Prepare a direct SDK spike with deterministic Python fixture nodes, InMemorySaver,
explicit recursion limit and clarification interrupt. No framework mock substitutes
for real SDK behavior. Gate the entire spike on a separate explicit flag and exact
SDK version; default discovery reports skipped tests. Block fixture network access
and tracing. Verify successful predecessor nodes versus rerun interrupted node,
continued clarification yielding only unapproved data, rejection/malformed response,
and stale source blocking continuation. In-memory state is not restart durability.

## Division of responsibilities

```text
Native ComfyUI workspace
  -> authenticated Studio control plane (identity/RBAC not implemented yet)
     -> planning worker / optional LangGraph adapter
        -> frozen IR/catalog/policy references
        -> independently authorized provider gateway (future integration)
        -> current Studio verifier -> unapproved proposal
     -> existing human diff review and native edit transaction
     -> separately approved evaluation/installation/contact/Comfy execution
```

LangGraph owns planning control flow/checkpoints, not native canvas topology,
workflow ownership definitions, approval truth, evidence truth or executable code.
Keep its SDK dependencies in an isolated planning worker when production wiring is
approved; do not add a second frontend/canvas or couple native nodes to LangChain.
Studio SQLAlchemy repositories remain the canonical project/revision/evidence/job
records. Use the maintained PostgreSQL checkpointer instead of writing another one.
Its connection/transaction lifecycle is not automatically the Studio SQLAlchemy
transaction: stable operation receipts reconcile gaps, not fictional cross-system
ACID. Checkpoint IDs and planning run IDs are distinct from Studio revisions.

Temporal is a candidate for cross-worker, long-running distributed coordination,
not required merely because the application contains agents. Defer its installation
and service deployment until workloads, HA/SLOs and orchestration needs are agreed.
If adopted, assign infrastructure dispatch/cancellation/reconciliation to Temporal
and local decision flow to LangGraph; avoid two authorities for the same lifecycle.
For a single structured proposal/model call, existing adapters may be simpler than
LangGraph; the SDK is justified by bounded iterative/branching/resumable planning.
PydanticAI is an alternative for typed model/tool interfaces, not evaluated here;
its public documentation request returned HTTP 403. No ranking is claimed.

## Documented behavior and implications

| Verified documentation behavior | Studio requirement |
| --- | --- |
| LangGraph interrupts rerun their node from the beginning | No effect before interruption; provider/effect gateway rechecks current one-use scope on every attempted call |
| Successful task writes can survive a failed super-step | Recovered results are data, not fresh approval or blanket permission |
| `Command(resume=...)` supplies interrupt input | Clarification is not consent; no persisted `approved` Boolean or approval token |
| `thread_id` selects checkpoint/current/history state | IDs are not authentication; server mapping, tenant/project checks and restricted database access on every read/resume/fork/delete |
| Resumed threads execute the graph compiled now | Pin SDK/runtime code/contract versions; block incompatible resume instead of silently changing behavior |
| Durability modes exit/async/sync differ | Use explicit sync for accepted durable checkpoints; it still does not guarantee exactly-once effects |
| Optional pickle fallback exists | Do not enable it; restrict serializers/state types and review deserialization/storage boundaries |
| Graph recursion limits bound supersteps | Explicit per-invocation limit plus durable global rounds/call/token/contact/time budgets; not a hard wall-clock sandbox |
| Temporal Activities retry by default, unlimited attempts by default | No default retries for consequential calls; `maximum_attempts=1` and explicit reviewed reconciliation/retry policy |
| Timeout/cancellation can leave externally started work uncertain | Neither grants rollback; record unknown outcome and independently review any new contact |
| Temporal event history records workflow/activity events | Carry immutable IDs/hashes, not credentials, approval tokens or private reasoning; review encryption/retention/access |

Even an effect after an interrupt can repeat across crash/checkpoint gaps. Framework
examples do not establish exactly-once behavior or Studio permission. Disable SDK,
provider-client and infrastructure automatic retries for consequential contacts unless
an exact independent reviewed policy permits them. Do not auto-resume a pending
provider/edit/execution operation after worker restart using a saved approval.

## Proposed production boundary (not implemented)

- Checkpoint only schema/version, tenant/project/run mappings, immutable source /
  policy/catalog/candidate/evidence references, bounded counters and lifecycle.
  No model message history, chain-of-thought, credential values, bearer grants or
  native canvas objects; private project content stays in controlled source records.
- Read current authenticated membership/project permissions before hydration,
  resume, history, fork or deletion. Comfy profile headers are not enterprise auth.
- Check current source revision, selected native scope, policy/catalog digests,
  runtime version and cancellation state. Stale work remains inspectable as data.
- Stage outputs through current P0/P2/P4 verifiers and proposal flows only. Preserve
  separate hierarchy ownership/execution DAGs and native commit/rollback boundaries.
- Record an attempt before any approved provider contact; use stable request IDs,
  contact budgets and unknown-outcome states. Never blindly replay an LLM/tool call.
- Protect framework checkpoint tables independently: maintained checkpointer does
  not itself establish tenant RLS for Studio. Namespaced thread IDs alone are not
  tenant security. Database/schema roles, store access and async pooling need tests.
- Tracing is off by default for the spike; production telemetry needs explicit
  redaction, tenant access, retention and destination review. No implicit LangSmith
  requirement for this architecture.

## Acceptance gates

1. Approve isolated SDK/dependency installation; lock resolved versions and review
   licenses/advisories. Run the prepared real LangGraph synthetic spike; all network
   fixture guards must remain unused. Skipped tests are not passing SDK evidence.
2. Validate an independently approved PostgreSQL checkpointer/schema with restart,
   duplicate resume, concurrent claims, retention and crash-boundary tests. No private
   main database migration or auto `checkpointer.setup()` during plugin import.
3. Integrate current trusted identity/project ACLs; test tenant read/history/fork/
   delete leakage and revoked/expired authority. No direct framework API exposure.
4. Compare against existing orchestration on the same fixed fixtures and workload:
   outcome quality, call/cost/latency, checkpoint growth, operator recovery and
   maintenance burden. Synthetic planning does not validate model quality.
5. Independently approve real providers and distributed workers only after scope /
   lifecycle checks. Specify load/SLO targets and failure scenarios before claiming
   enterprise scale or SOTA status. Temporal requires a separate justified spike.

## Isolated spike verification

`custom_nodes/agent_workflow_studio/test_langgraph_spike.py` contains five direct-SDK
fixture scenarios. Initial discovery skipped them before authorization. After
explicit user approval, installed hash-locked LangGraph 1.2.12 and its dependencies
(38 packages total) into `/tmp/agent-workflow-langgraph-spike-venv`, Python 3.12.12,
not the Comfy runtime. All five real SDK fixtures passed, zero skips and no guarded
fixture network calls. The adapter fixture enforces unapproved/stale states; these
are not automatic framework security guarantees. [Synthetic result](agent-runtime-spike-result.json)
records SDK version, case IDs and source hashes. The spike has no production
adapter/route registration.

[Git/CI delivery](AGENT-RUNTIME-CI.md) adds ten runner/workflow-policy tests, all
passing, and a fail-closed evidence runner; remote GitHub execution is not yet
verified. Ruff lint/format and runner mypy passed; uv dependency consistency passed.
Set `COMFYUI_LANGGRAPH_SPIKE_ALLOWED=1` only in explicitly approved test processes;
exact SDK metadata must match `1.2.12`. Initial imports are delayed until admission;
tracing environment is cleared and network calls are guarded during fixtures. These guards are test instrumentation, not an OS sandbox
or universal egress/security proof. No model, SQL checkpointer, native canvas,
Temporal service or multi-tenant authentication is exercised by this spike.

## Primary sources

- [LangGraph Graph API](https://docs.langchain.com/oss/python/langgraph/graph-api.md)
- [LangGraph interrupts](https://docs.langchain.com/oss/python/langgraph/interrupts.md)
- [LangGraph checkpointers and durability](https://docs.langchain.com/oss/python/langgraph/checkpointers.md)
- [LangGraph PyPI metadata](https://pypi.org/pypi/langgraph/json)
- [Temporal Activity execution](https://docs.temporal.io/activity-execution.md)
- [Temporal retry policies](https://docs.temporal.io/encyclopedia/retry-policies.md)
- [Temporal event history](https://docs.temporal.io/workflow-execution/event.md)
