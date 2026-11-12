# Development loop

## Two lanes, both fail closed

```powershell
.\scripts\quick.ps1 -Tests tests/test_shot_motion.py
.\scripts\quick.ps1 -Keyword audio -IncludeSlow
.\scripts\check.ps1
.\scripts\check.ps1 -Fast -Workers 4
```

- `quick`: Ruff only on changed Python files, then selected tests with `-x`. Slow integration
  tests are excluded unless `-IncludeSlow` is passed. With no selection, only cached failures
  run; an empty failure cache **does not** fall back to the full suite or claim tests passed.
- `check`: full Ruff/format gate and all tests. `-Fast` uses declared `pytest-xdist` workers with
  per-worker, per-test isolated runtimes. It does not omit `slow` tests. Install `.[dev]` first.
- Both scripts resolve the repository from their own location and propagate failures, even when
  called from a different working directory. Lint errors cannot be hidden by a passing pytest run.

## Structural change

`controller.py` and `repository.py` are now thin compatibility facades. Read and edit the relevant
`use_cases/`, `store/` and `contracts/` modules; see [architecture.md](architecture.md).
Keep runtime checks and receipts in the controller boundary, not in CLI handlers.

## Measurement

Before this bundle, 511 tests took 54.42 s serially on this Windows host. Final verification runs
**516 tests in 49.85 s serially and 21.54 s on four workers**, with all tests included and passing.
Evidence is recorded in `implementation/remaining-items.md`.
This is measured wall time, not a promise that more workers always make SQLite/media tests faster.

The existing session-scoped migrated-runtime fixture is retained. It builds the schema once per
worker and copies it per test; migration tests still create real databases independently.

Cached-failure selection checks current test definitions first. This avoids pytest's run-all
fallback when a cached failure points to a renamed/deleted test. The Windows PowerShell JSON
empty-array conversion is explicitly flattened and regression-tested; an empty selection exits
without invoking pytest.

One full gate per release bundle is the default. Use focused tests while editing, never a full
image build per edit. CI remains the separate remote verification gate after Git push.
