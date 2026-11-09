# Pinaka Agentic Creative Production

Controller-first foundation for the creative-production system described in
`Pinaka_Agentic_Creative_Production_Specification.txt`.

This milestone implements the **enforcement layer**, not autonomous production. It provides
validated contracts, SQLite-owned state, human approvals, dependency invalidation, transactional
budget reservations, recoverable provider-job states, immutable original handling, artifact
lineage, capability probes, an audit stream, and a deterministic mock provider.

## Safety boundary

- Agents may propose; `CreativeController` decides and commits.
- Provider work cannot be reserved beyond the project budget.
- Jobs requiring an approval cannot run without an active approval.
- Reused idempotency keys return the original job; changed requests are rejected.
- A changed fact set invalidates only dependent approvals and artifacts.
- Originals are imported content-addressably and never overwritten by controller APIs.
- Secrets are rejected from persisted request/response fields.
- No real generation, stock, Penpot, upload, or publishing adapter is enabled yet.

## Install on Windows

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install --upgrade pip
.\.venv\Scripts\python -m pip install -e ".[dev]"
```

## Verify

```powershell
.\.venv\Scripts\python -m pytest
.\.venv\Scripts\ruff check src tests
.\.venv\Scripts\ruff format --check src tests
.\.venv\Scripts\python -m compileall -q src tests
```

## Run the no-network vertical slice

```powershell
.\.venv\Scripts\python -m pinaka --home .\workspace demo --name "Foundation Demo"
```

The command creates a project, records a brief approval and fact set, reserves a simulated cost,
runs the deterministic mock adapter, stores a generated test artifact, settles the budget, and
prints the resulting state. It makes no external or paid call.

Useful commands:

```powershell
.\.venv\Scripts\python -m pinaka --home .\workspace capabilities
.\.venv\Scripts\python -m pinaka --home .\workspace list-projects
.\.venv\Scripts\python -m pinaka --home .\workspace events
```

## Runtime layout

Runtime data defaults to `workspace/` and is excluded from Git:

```text
workspace/
├── state/pinaka.sqlite
└── projects/<project-id>/
    ├── inputs/                 ├── references/
    ├── assets/originals/       ├── assets/derived/
    ├── assets/generated/       ├── designs/
    ├── audio/                  ├── previews/
    ├── renders/                ├── qa/
    └── archive/
```

## Current scope and next gate

Implemented: Phase 1 foundation and a mock vertical slice. Before adding a real adapter, complete
the pilot contract in `config/pilot-contract.example.json`, review the rights policy, and pass the
connection spike described in `docs/next-gate.md`.

See `docs/implementation-report.md` for verified evidence, capability findings, deferred work, and
residual risks.
