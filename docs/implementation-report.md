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

## Phase 1–2 governance extension — 10 November 2026

Added the deterministic planning and control plane for both phases:

- CR-01–CR-15 evaluation with decision traces, 11 versioned recipes and slot policies;
- Openverse, Wikimedia Commons, Internet Archive and Freesound metadata adapters;
- exact-hash font inventory and licence gate;
- provider routing, external-spend reconciliation and a fail-closed publication preflight;
- zero-cost Nano Banana image adapter with a free-tier-only guard;
- A/B experiment design, Wilson intervals and two-proportion z-test;
- runtime backup, integrity verification, restore drill and non-destructive retention plan.

Verification: **308 tests pass**, Ruff/format/compile clean, archived-pilot preflight **12/12 PASS**,
restore drill **329 items** with SQLite integrity `ok`, and publication preflight correctly blocked
for missing authorization and captions.

The caption audit found readability defects in the approved SRT: cue 2 has a 63-character line,
cue 4 has a 46-character line, and cue 4 reads at 23.0 CPS against a 20.0 limit. The archive remains
valid; a v2 captioned master should correct these.

No paid provider was called. The Freesound key is now present in the vault and a live metadata search succeeded; Nano Banana uses the ADC route. Installed
local tools are recorded in `open-source-production-toolchain.md`.

Toolchain verification:

- Manim 0.19.1 rendered `SquareScene.mp4` at 480p15.
- QCTools CLI 1.4.1 produced a 147,582-byte report for the archived master.
- MediaConch CLI 25.04 parsed the archived master; it returns `N/A` without a policy file.
- Font inventory extracted real family names from Arial and Calibri and blocked both for missing
  exact-hash licence records.
- The A/B example fixture found `cash_flow_hook` at 24% completion versus 18% control
  (`p=0.000988`, winner under Bonferroni-adjusted alpha); this is synthetic test data, not platform
  performance.
- ComfyUI v0.44.0 generated a 512x512 PNG on CPU in 252 seconds using SD1.5; the smoke-test image
  contains pseudo-text and is not suitable for delivery.
- The ADC-authenticated Vertex Nano Banana adapter was added with a per-call cost ceiling and
  trial-credit accounting; ADC and model lookup passed before the sample.
- One controller-owned Vertex sample (`gemini-nano-banana-2.1`, 1K, 16:9) produced a 1376x768 PNG
  with 31 prompt tokens and 1120 candidate tokens: **4 cents of GCP trial credit**, estimated 5 cents.
  Artifact `art_5d8e1002db0e49daadb1f06ae18d9aa1`, spend record `art_b857edd84b7f454bab05cf0dcd6e7a4a`.
- The controller-owned ComfyUI adapter generated a 256x256, 4-step image in 25.5 seconds and stored
  artifact `art_e938ebdc62654fb485095f9cff4f9e44` with hash
  `d5f5fe5d9af2355c4583a03e91b5ceac6c95958d39bdbfe2b8678eedd2bfa378`.
- QCTools/MediaConch external QA parsed 758 frames from the archived master; MediaConch returned
  `N/A` without a policy.
- The offsite mirror copied **329 items** and passed SQLite integrity verification.
- CSV analytics ingestion reproduced the `cash_flow_hook` winner from the example fixture.
- The publication dispatch package correctly refused the archived pilot because publication
  authorization and embedded captions are still missing.

## Release and rollback

No release or deployment was performed. The repository has no commit yet. Runtime demonstration
data is under ignored `workspace/` paths. Any deletion or rollback of the new folder remains a
human-authorized action.
