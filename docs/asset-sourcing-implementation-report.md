# Asset-sourcing bundle evidence

- **Request ID:** ASSET-SOURCING-20261109-01
- **Date:** 9 November 2026
- **Lane / release:** C — contracts/policy / local-only, no release
- **Outcome:** review five requested stock sources and incorporate them into a rights-governed
  sourcing flow without enabling external calls

## Baseline and scope

- Branch: `main`
- Repository state: initialized with no commits; existing foundation files were untracked
- Existing verification baseline: 14 tests passing
- Allowed paths: `docs/`, `src/pinaka/`, `tests/`
- No-touch: `.env*`, `workspace/`, credentials, provider accounts, production systems
- Runtime/data records changed: none
- Deployment baseline: no release or deployment exists

## Implemented

- provider profiles for Pexels, Openverse, Unsplash, Mixkit and Videezy;
- API/manual acquisition allowlists and media capability declarations;
- normalized source, rights, evidence, attribution, uncertainty and compliance contracts;
- deterministic `eligible` / `needs_review` / `blocked` evaluation;
- controller-governed sourced-original import that refuses unresolved rights;
- immutable artifact metadata carrying source-manifest and content hashes;
- official-source review and revised production/connection flow documentation.

## Verification

| Requirement | Result | Evidence |
|---|---|---|
| R1 provider review | PASS | `asset-sourcing-policy.md` and provider profiles |
| R2 provenance contract | PASS | strict Pydantic models and sourced-import test |
| R3 rights blocker | PASS | noncommercial/editorial/uncertain/attribution tests |
| R4 acquisition distinction | PASS | Mixkit/Videezy API rejection and profile tests |
| R5 flow integration | PASS | architecture, next-gate and traceability updates |

Technical checks:

- `python -m pytest`: PASS — 25 tests
- `ruff check src tests`: PASS
- `ruff format --check src tests`: PASS — 26 files
- `python -m compileall -q src tests`: PASS
- `python -m pip check`: PASS

## Deferred register

| Item | Status | Reason / next gate |
|---|---|---|
| Openverse network adapter | DEFERRED | Run one bounded API search and origin-licence verification spike |
| Unsplash network adapter | DEFERRED | Requires developer credentials and hotlink/download-event/UTM compliance test |
| Pexels network adapter | BLOCKED | New key issuance is paused; proceed only if an existing key is available |
| Mixkit automation | DEFERRED | Manual only; no documented public API and mass-download scripts are prohibited |
| Videezy automation | DEFERRED | Manual only; terms prohibit automated/non-human access and scraping |
| Final-output attribution QA | DEFERRED | Belongs to the shared render/final-approval pipeline |

## Release, rollback and residual risk

- Release SHA: N/A — repository still has no commit and no release was created.
- Deploy/rollback: N/A — local source changes only; production remained untouched.
- Provider terms can change and must be re-reviewed at actual selection time.
- Recorded eligibility is a policy decision, not legal clearance. Human review remains mandatory for
  uncertain personality, privacy, property, trademark, editorial and jurisdiction-specific issues.
- No external API behavior, download, credential handling, rate-limit behavior or provider account
  eligibility was exercised by this bundle.
