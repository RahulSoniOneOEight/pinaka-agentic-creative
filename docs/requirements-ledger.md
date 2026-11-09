# Phase 1 requirement ledger

| ID | Requirement | Status | Evidence |
|---|---|---|---|
| R1 | New isolated Python project | PASS | Editable install in `.venv`; package and CLI load successfully |
| R2 | Validated contracts and SQLite ownership | PASS | Pydantic contract tests, reopen test, `PRAGMA integrity_check=ok` |
| R3 | Controlled project transitions | PASS | Happy-path and invalid-transition tests |
| R4 | Human approvals and targeted invalidation | PASS | Fact-change test invalidates dependent work and preserves unrelated artifacts |
| R5 | Budget, idempotency and recovery | PASS | Reservation, concurrency, settlement and restart tests |
| R6 | Artifact provenance and protected originals | PASS | Content-hash, traversal rejection, lineage and idempotent-import tests |
| R7 | Capability probes and mock paid-call boundary | PASS | Capability command and deterministic no-network vertical slice |
| R8 | Operator documentation and CLI | PASS | README, architecture/next-gate docs and CLI smoke test |

Phase 2 provider connections are outside this ledger and are explicitly deferred in
`implementation-report.md`.

## Stock-sourcing extension — 9 November 2026

| ID | Requirement | Status | Evidence |
|---|---|---|---|
| R1 | Review and classify Pexels, Openverse, Unsplash, Mixkit and Videezy | PASS | Provider matrix and official-source links in `asset-sourcing-policy.md` |
| R2 | Preserve source, creator, licence, attribution, terms date, hashes and uncertainty | PASS | Strict source/rights/compliance contracts and immutable sourced-import manifest |
| R3 | Block incompatible or unresolved rights before production import | PASS | Commercial/editorial, attribution, evidence and uncertainty policy tests |
| R4 | Separate official-API acquisition from governed manual import | PASS | Provider profiles; Mixkit/Videezy API attempts are rejected in tests |
| R5 | Incorporate the providers into architecture and connection gates | PASS | Updated `architecture.md`, `next-gate.md` and traceability |

Real API searches and downloads are deliberately deferred to the provider-specific connection
spikes; no credentials or external calls were used in this bundle.
