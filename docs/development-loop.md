# Development loop

Why a small change feels slow here, and the lanes that fix it. Numbers are measured on this
machine (6 logical cores, Windows), not estimated.

## Diagnosis

### 1. The change surface is wide, because two files are huge

| File | Lines |
|---|---|
| `src/pinaka/controller.py` | 6,691 |
| `src/pinaka/repository.py` | 5,642 |
| `src/pinaka/cli.py` | 2,589 |

Every feature is a method on `CreativeController`, so a "one-field change" is never one file. Excluding
Veo/Runway touched **10 files / 98 lines**: contract, domain guard, rate card, provider mapping,
controller, two test files, four docs. Adding a CLI command is 3 files minimum (models, controller,
cli) before tests.

That is the dominant cost. It is not the test runner — it is locating and re-reading a 6,700-line
file for every edit.

### 2. Every test replayed all 18 migrations

`CreativeController.open()` calls `Database.initialize()`, which replays migrations 1–18 on an empty
file. Measured: **77 ms per open**, and 496 tests each opened a fresh runtime.

| | Before | After |
|---|---|---|
| Fresh open + migrate | 77 ms | — |
| Copy a pre-migrated runtime | — | 2 ms |
| Full suite | 59 s | **43 s** |

The `migrated_runtime` fixture in `tests/conftest.py` builds the schema once per session and copies it
per test. Same 496 tests, all green.

### 3. There is no hot spot left to optimise

Slowest single test: **1.33 s**. The 43 s is breadth (496 tests × ~85 ms), spread across encode,
caption, QA and Studio-gateway tests. Nothing to micro-optimise.

### 4. The default loop was "everything, serially, repeatedly"

`pyproject.toml` had no fast-lane config (`--lf`, `-x`, markers, parallel workers). The old habit was
`ruff` on the whole tree plus a full `pytest` after **every** edit — five or six times per change
bundle, which is 5 minutes of pure waiting for one feature.

## The lanes

| Lane | Command | Typical | When |
|---|---|---|---|
| L0 | `.\scripts\quick.ps1 -Tests tests/test_x.py` | **2–3 s** | after each edit |
| L1 | `.\scripts\quick.ps1 -Keyword motion` | 3–6 s | after a group of related edits |
| L2 | `.\scripts\check.ps1` | **45 s** | once per bundle, before commit |
| L3 | `git commit && git push` | — | after L2 is green |

`quick.ps1` runs ruff only on **files changed since HEAD** (tracked + untracked), so it can never
reformat the other 139 files, then runs only the tests you name. With no arguments it runs
`--lf` (last failed), so a red loop stays red until it is actually fixed.

`check.ps1` is the gate: `ruff check` on `src` and `tests`, `ruff format --check`, full `pytest`.
Add `-Fast` to use `-n 6` if `pytest-xdist` is installed (not a dependency today).

### Rules that matter more than the commands

1. **One full suite per bundle, not per edit.** Everything else is targeted.
2. **Never `ruff format src` during iteration** — format the changed files only.
3. **Read narrowly.** `grep` for the symbol, then `read` with `offset`/`limit`. Never re-read a
   6,700-line file to change one method.
4. **Batch independent reads and edits** in one turn instead of one tool call at a time.
5. **Write the test with the change**, so L0 is meaningful and L2 has nothing new to surprise you.

## Shrinking the change surface

Measured cost of a typical change today:

| Change | Files | Why |
|---|---|---|
| Exclude a provider (Veo/Runway) | 10 | contract + guard + rate card + mapping + tests + docs |
| New CLI command | 5+ | models, controller, cli, test, doc |
| New QA check | 4+ | qa, controller, test, doc |

The way down is structural, in this order:

1. **Split `controller.py` by feature** into `controller/` modules (motion, assembly, publication,
   studio, qa, vertex) with `CreativeController` kept as the facade that composes them. Each module
   lands around 300–600 lines. Mechanical, covered by the existing 496 tests, and an edit then reads
   one small file. This is the single highest-value change.
2. **Split `repository.py`** along the same seams (projects, artifacts, shots, budget, studio).
3. **Add a feature template** — a skeleton that creates the model, controller method, CLI verb,
   test and doc row in the agreed shape, so a new verb is copy-and-fill rather than re-derivation.
4. **Mark heavy tests** (`@pytest.mark.slow` for anything spawning FFmpeg/Playwright) and exclude
   them from L1 with `-m "not slow"`, so the mid-loop drops from seconds to under a second.
5. **Optional: `pytest-xdist`** as a dev dependency — 496 tests over 6 workers should land near
   12–15 s, which makes L2 cheap enough to run on every save.

Items 1–2 are refactors with no behaviour change; they should be done as their own bundle with the
suite green before and after, not folded into a feature.
