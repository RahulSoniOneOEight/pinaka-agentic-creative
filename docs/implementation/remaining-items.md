# Remaining-items closure

## Contract and baseline

- Request: close the outstanding register and push the verified changes.
- Baseline: `bca8ca090848f327c3a836e812f46c0304b6a732`, `main`, clean tree.
- Baseline verification: 511 tests passed in 54.42 s.
- Lane D; release mechanism: source bundle plus external-integration verification.
- No UI or production deployment. No archived-project mutation.
- Scope: `src/pinaka/`, `tests/`, `scripts/`, dependency metadata, related documentation and `.github/workflows/ci.yml` (renamed optional-extra/test dependencies).
- No touch: `.local/`, secrets, original media, archived projects, pilot budgets/approvals.
- Tests use disposable runtimes; no application schema migration is planned.
- Rollback: revert the scoped change commit; do not reset project state.

## Requirement ledger

| ID | Requirement | Status / acceptance |
|---|---|---|
| U1 | Remove active Vertex paths; fal image generation/edit, local TTS/ASR and provider-independent script proposals | PASS: funded edit, replay, currency/one-take/refusal tests; local TTS and supplied-copy tests. Live provider behavior remains P1/P2, not claimed here |
| U3 | Shared policy guards | PASS: 18 copied active-project checks centralized, funded-item/one-take checks exercised before adapter calls; audio rights checked before mixing |
| U4 | Operation catalog | PASS: explicit routes/cost-model/receipt policy; motion packets and image execution consume it; no live-readiness claim |
| U5 | Feature-local controller slices | PASS: compatible facade, disjoint method ownership test; source relocation preserved method text before intentional edits |
| U6 | Aggregate-local repositories | PASS: compatible facade; existing atomic state+event transaction boundary preserved |
| U7 | Aggregate-local contracts | PASS: legacy imports alias the same strict classes; all feature/contract regressions retained |
| U8 | Targeted/parallel test lanes | PASS: xdist declared/pinned, strict slow marker, full serial/parallel verification; scripts fail closed and resolve repository location |
| P1 | Live fal image verification | BLOCKED: Studio rejects the explicitly authorized zero-credit production budget before any job exists |
| P2 | Live Studio still/clip/import/assembly | BLOCKED: same admission failure; no paid generation, no import/assembly of a live clip claimed |

## Invariants

Zero-spend default; no Veo/Runway; open-licensed/owned music and SFX only;
one take per authorized paid request; unknown submissions require reconciliation;
exact-hash approval, archive and publication authorization stay separate human acts.

## Evidence

Final verification, commit range and unresolved prerequisites will be recorded here.
An approximate price is not a spending ceiling; a blocked live test is not a passed item.

### Paid-test attempt

- User was asked to authorize at most **USD 1 total**, a separate test project, one fal still,
  one five-second Minimax H3 first-frame clip, **zero OpenMontage credits**, no retry/publication.
- Raw answer: **"Authorize up to US$1"**.
- Studio bootstrap: signed in/connected, installed version 0.1.6. Provider discovery:
  `fal.image`/`fal.video` ready; FLUX.2 pro nominal floor USD 0.03, H3 768P USD 0.060/s.
- Stable create request ID: `pinaka-close-register-20261113-v1`.
- Requested budget: `{credits: 0, usd: 1}`.
- Exact refusal: `Invalid production action "create" request: budget.credits: Too small:
  expected number to be >0`.
- No production ID or accepted generation job was returned. No Pinaka live project/reservation
  was created, no provider key was read/copied, no provider submission was made. **New spend: USD 0.**
- Do not work around this with default credits, a direct script, a different profile or new keys.
  Studio must support the user's zero-credit BYOK budget before these live acceptance tests resume.

### Local fal credential check (free, no submission)

The second route — Pinaka calling fal directly with the key file at
`%USERPROFILE%/.config/opencode/secrets/fal-api-key` — was tested without submitting a job:

| Request | Result |
|---|---|
| real app, **no** auth header | 401 |
| real app, garbage key `0000:0000` | 401 |
| real app, stored key | 401 |
| fake app, **no** auth header | 404 |
| fake app, stored key | 404 |

401 is therefore the unauthenticated response, and the stored value produces exactly the same
result as sending nothing. The 404 on an unknown app happens without authentication and is not
evidence of a valid key. The stored value is a bare `fal_...` with no colon, while fal parses
`Authorization: Key <key_id>:<key_secret>`; a malformed value is treated as no auth.

Conclusion: **the local fal credential is not accepted by the model API.** This is independent of
the Studio `credits: 0` blocker: P1/P2 are blocked on both routes until either a valid
`key_id:key_secret` key with the `API` preset is installed, or Studio admits a zero-credit budget.
No job was created and no charge was incurred. Studio's own stored credential is separate and was
not read or copied.

### Audio closure corrections

The prior audio release was broader than its wiring: `mux-narration` omitted registered layers,
preflight read bed rights from treatment metadata, publication accepted a disclosure without checking
credit coverage, and kids cue policy was only a callable helper. This bundle wires the composition
mux, current-rights validation, verified credits and made-for-kids publication checks end to end.
Three legacy bed fixtures now explicitly supply CC0 rights rather than exercising an unlicensed bypass.

Audio gain checks validate configured attenuation, not a perceptually measured per-stem voice/bed
ratio. Actual final-mix loudness and true peak remain measured QA; a listening review is still human.

### Final local verification

- Serial: `python -m pytest -q` — **516 passed in 49.85 s**.
- Parallel full gate from outside the repository:
  `scripts/check.ps1 -Fast -Workers 4` — Ruff passed, 201 files formatted,
  **516 passed in 21.54 s**. No slow tests omitted on this Windows host.
- Python compileall and `uv lock --check` passed. Lock resolves 147 packages.
- CLI `--help` and `operations` exercised; retired Vertex verbs are absent.
- Focused modular-architecture tests: 9 passed in 0.44 s (pytest time, excluding shell/Ruff).
- Quick script smoke: external working directory, explicit test selection, missing-test exit 4
  propagated, and stale/empty failure cache exits without invoking the suite.
- Two PowerShell interoperability cases and three cache-selector cases are now regressions. The
  PowerShell-specific tests are explicitly skipped where `powershell.exe` is unavailable (e.g. Linux CI).
- The original suite included 18 Vertex-only tests, now retired; two fal pricing tests were moved,
  not discarded. New architecture, funded-fal, audio and test-lane regressions replace/add coverage.
- Source scan found no credential-pattern matches. No live credentials or original/archived media
  were read, copied, overwritten or committed by this bundle.

### Resulting structure and release scope

| Surface | Current size |
|---|---:|
| Controller facade | 100 lines |
| Repository facade (including shared transaction helpers) | 176 lines |
| Legacy model exports | 89 lines |
| Motion slice | 459 lines |
| Still-edit slice | 143 lines |

Behavior is owned by 22 feature modules (plus package/shared support), 18 store modules and nine
contract modules. Compatibility imports remain; this is a modular monolith, not a distributed rewrite.
The schema remains v19. Baseline data, pilot budget/approvals and archives are unchanged.

Release: the scoped commit carrying this report, based on `bca8ca0`; its SHA is reported with the
Git push result. No application deployment, proxy restart or channel publication is applicable.
Rollback is a normal revert of that scoped commit, not a database downgrade/reset.

Remaining acceptance blockers are **P1/P2 only**, with the exact Studio admission failure recorded
above. Their budget approval does not expire into permission to spend on another route or default
credit allowance. Membership/device/Full-access admission must also be rechecked when Studio creation
can actually proceed. Remote CI status is separate from the local test evidence and is not presumed.
