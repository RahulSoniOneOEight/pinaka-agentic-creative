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
| P1 | Live fal image verification | OPEN: credential unblocked (`pinaka fal-check` → 200, `id:secret`); no controller-funded still has run yet |
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

Method: `pinaka fal-check` performs one read-only `GET https://api.fal.ai/v1/models`. That endpoint
answers **200 without any credential**, so a 401 isolates the credential from route-level auth
errors; nothing is submitted and nothing is charged.

| Request | Result |
|---|---|
| public endpoint, no auth | 200 |
| public endpoint, stored key | 401 `authorization_error` |
| public endpoint, malformed key `0000:0000` | 401 |

The stored value is well formed: one line, 39 characters, `fal_` single-token shape, no whitespace.
It is byte-identical to the value later supplied for reinstatement. fal's own client sends
`Authorization: Key <FAL_KEY>` verbatim and never splits on `:`; this repository accepts both shapes
(`tests/test_fal.py::test_single_token_fal_keys_are_accepted`). The earlier "malformed because it has
no `id:secret`" explanation was **wrong** — the credential itself is refused (revoked, expired or
never issued), not mis-shaped. Route-level probes cannot decide this: `GET fal.run/fal-ai/flux/schnell`
returns 401 with *and* without a key, and an unknown model returns 404 with *and* without one.

Consequence: the credential blocker is now closed. A replacement key in `id:secret` form was
supplied on 2026-11-13 and installed at `~/.config/opencode/secrets/fal-api-key` (mirrored to
`D:\pinaka-credentials\secrets\fal-api-key`, ACL restricted to the user). `pinaka fal-check` reports
`authenticated: true`, `http_status: 200`, `key_shape: id:secret`. The previous `fal_sk_...` value
was a different credential and is still refused; the account was never the problem. What remains for
P1 is one controller-funded still inside the standing USD 1 authorization; P2 additionally needs the
Studio `credits: 0` admission or the direct-video path.

### Audio closure corrections

The prior audio release was broader than its wiring: `mux-narration` omitted registered layers,
preflight read bed rights from treatment metadata, publication accepted a disclosure without checking
credit coverage, and kids cue policy was only a callable helper. This bundle wires the composition
mux, current-rights validation, verified credits and made-for-kids publication checks end to end.
Three legacy bed fixtures now explicitly supply CC0 rights rather than exercising an unlicensed bypass.

Audio gain checks validate configured attenuation, not a perceptually measured per-stem voice/bed
ratio. Actual final-mix loudness and true peak remain measured QA; a listening review is still human.

### Caption, stock and finishing bundle

- `build_srt` now wraps and splits by the locale `line_chars`/`lines` and reports `cps`
  violations; `strict=True` refuses to emit an over-limit cue. `export-captions` records the
  verdict and violation list.
- `transcribe-audio --srt` requests word timestamps, builds speech-timed cues with
  `build_srt_from_words`, and `caption-drift` compares them with plan captions (text overlap and
  start offset). No model call happens in the test suite; fakes cover the grouping and drift rules.
- Pexels search accepts a duration window and preference, dedupes provider ids and exposes a
  deterministic in-point helper; assembly segments carry an optional `start`.
- `draft-metadata` writes deterministic channel-sized copy; it does not invent claims.
- `grade-asset`, `key-asset` and `change-speed` are FFmpeg finishing adapters with hashed outputs;
  filter builders are unit-tested, execution is not run in the suite.
- Image edits can carry ordered reference images in `image_urls`. The provider-side consistency
  claim remains **unproven live**.
- Beat detection is local (FFmpeg + numpy); `assemble-film --beat-grid` snaps cuts and records the
  quantization. Song-fit remains a planner.
- Avatar/lip-sync is implemented against fal's published schemas with a likeness-consent gate and a
  funded `avatar.generate` route. **No live avatar call has been made.**
- Closure evidence and explicit non-claims for both: [capability-closures.md](capability-closures.md).

### Final local verification

- Serial: `python -m pytest -q` — **627 passed in 49.18 s**.
- Parallel full gate: `python -m pytest -q -n 4 --dist loadfile` — **627 passed in 33.57 s**.
  No slow tests omitted on this Windows host.
- `ruff check` and `ruff format --check` pass across 219 files.
- `python -m mypy` passes for 47 modules (the ratchet list in `pyproject.toml`).
- CLI `--help`, `operations` and `fal-check` exercised; retired Vertex verbs are absent.
- `fal-check` against the installed credential: the dead `fal_sk_...` value returned 401; the
  replacement `id:secret` key returns `authenticated: true`, `http_status: 200`.
- Focused modular-architecture tests: 9 passed in 0.44 s (pytest time, excluding shell/Ruff).
- Quick script smoke: external working directory, explicit test selection, missing-test exit 4
  propagated, and stale/empty failure cache exits without invoking the suite.
- Two PowerShell interoperability cases and three cache-selector cases are now regressions. The
  PowerShell-specific tests are explicitly skipped where `powershell.exe` is unavailable (e.g. Linux CI).
- The original suite included 18 Vertex-only tests, now retired; two fal pricing tests were moved,
  not discarded. New credential-boundary, one-take, spool, void-job and probe regressions add coverage.
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

### Gap-completion bundle (2026-11-13, schema v25)

- Commit `2f6752c`; 688 tests pass; `ruff check`/`format` clean; `mypy` clean (51 files).
- Schema v25 is additive: budget A/B labels, project change-request link, flow execution
  ownership, change-request/event tables and A/B delivery ledger. A v24 database opens and
  migrates; `tests/test_gap_completion.py::test_schema_v24_migrates_to_v25` covers it.
- Closed: F13 (ASR default), F15 (effect library), F20 (ownership auto-link), F24 (A/B packet),
  F26 (budget labels), F28 (intake), F29 (impact), F30 (revision link), F32 (slot coverage),
  F33 (Pexels evidence/unavailable) and F36 (caption coverage).
- Run: `scripts/qc_complete_flow.py`, project `prj_5af9fdf514b4451ea083828b466c7e31`,
  archive `archive_b9af3a4a8fe44d8aa32f10bf3b5f4cf4`, 30/30 quality-bar gates, final QA pass,
  ASR caption coverage 1.0, M2 motion and continuity pass, USD 0.00 of the USD 1.00 cap.
- A/B packet manifest `bfefa3de...`; change request `crq_9e17a6ae...` applied to revision
  `prj_2c39ecca...`; g5 records a local fallback of Studio with a trade-off.
- Still external, not claimed as passed: P1 live fal still, P2 Studio video/avatar (credits 0),
  Pexels video evidence pages (HTTP 403).
