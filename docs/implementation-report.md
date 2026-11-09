# Phase 1 implementation report

## Outcome

Created an isolated implementation at `D:\pinaka-agentic-creative`. It is a local Python package
and CLI implementing the controller-first Phase 1 foundation. No production deployment, external
API call, paid generation, Penpot mutation, upload, or publishing action occurred.

## Created

- strict Pydantic contracts for projects, facts, shots, slots, approvals, jobs and agent results;
- SQLite schema, migration marker, serialized write transactions and append-only events;
- explicit project state machine and approval sequencing;
- fact/approval/artifact dependency invalidation;
- transactional budget reservation, settlement ledger and idempotent job creation;
- restart-visible `submission_unknown` recovery state;
- content-addressed originals, protected paths, artifact hashes and lineage;
- executable/environment capability probes that never record credential values;
- provider protocol and deterministic mock adapter;
- CLI, pilot contract template, architecture/next-gate documentation and tests.

## Verification evidence

| Check | Result |
|---|---|
| Unit/integration suite | PASS — 25 tests |
| Ruff lint | PASS |
| Ruff format check | PASS — 26 files |
| Python compilation | PASS |
| Dependency consistency | PASS — `pip check` reports no broken requirements |
| CLI no-network vertical slice | PASS — job succeeded, artifact hashed, budget settled |
| SQLite integrity | PASS — `integrity_check=ok`, zero foreign-key violations |
| Scope inspection | PASS — runtime, virtual environment and caches are ignored |

## Capability baseline

| Capability | Observed status |
|---|---|
| Python 3.10.11 | working |
| Git 2.55.0 | working |
| FFmpeg / ffprobe | unavailable to the process |
| ImageMagick | unavailable to the process |
| KinoCut | unavailable to the process |
| fal credentials | not configured |
| Penpot MCP bridge for the Python controller | not configured/verified |

The OpenCode environment's own tools are separate from a capability exposed to this Python
controller. No claim is made that a provider or design bridge works until its connection spike
passes.

## Deferred register

| ID | Deferred item | Reason / gate |
|---|---|---|
| D1 | Real fal generation adapter | Pilot endpoint, budget and credential policy require approval |
| D2 | FFmpeg, KinoCut and ImageMagick operations | Tools are unavailable; install and run bounded connection spikes |
| D3 | Penpot edit/export adapter | Requires scoped MCP test and reconstruction-fidelity review |
| D4 | Stock network adapters | Rights policy/contracts are implemented; Openverse, Unsplash and Pexels still require separate connection spikes |
| D5 | Mixkit/Videezy automated adapters | Not authorized; both remain governed manual-import sources unless an official API or written permission is documented |
| D6 | Voice adapters | Provider access, terms and pilot choices remain unset |
| D7 | Media QA and first complete video | Requires provider spikes plus approved assets/facts |
| D8 | UI, API, queue, embeddings and distributed workers | No measured need in Phase 1 |

## Human decisions required before Phase 2

Complete `config/pilot-contract.example.json`: pilot SKU/assets, fact source, rights policy, language,
delivery target, maximum paid budget, LLM, exact fal endpoint, voice provider and acceptance owner.

## Residual risks

- SQLite and filesystem updates cannot share one physical transaction; operations are idempotent and
  hash-checked, but a future reconciliation command should scan orphaned files/records after a crash.
- Secret-like field names are rejected, but free-text content is not a DLP system. Real adapters must
  explicitly redact provider responses before persistence.
- Original immutability is enforced by controller APIs and hashes, not operating-system ACLs.
- No media quality, product identity, rights, accessibility or render-throughput claim has yet been
  validated.
- The sourcing evaluator enforces recorded licence findings but cannot establish ownership,
  model/property releases, trademark clearance, or legal sufficiency on its own.

## Asset-sourcing extension — 9 November 2026

Added a provider registry and a strict pre-import rights gate for Pexels, Openverse, Unsplash,
Mixkit and Videezy. Eligible imports retain normalized source, creator, licence, attribution, terms,
evidence, provider-compliance, use-context and uncertainty metadata alongside content and manifest
hashes. No stock API request or media download was made.

Detailed policy and bundle evidence:

- `asset-sourcing-policy.md`
- `asset-sourcing-implementation-report.md`

## Release and rollback

No release or deployment was performed. The repository has no commit yet. Runtime demonstration
data is under ignored `workspace/` paths. Any deletion or rollback of the new folder remains a
human-authorized action.
