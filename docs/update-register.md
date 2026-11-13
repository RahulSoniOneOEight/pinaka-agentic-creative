# Update register

| Item | Status | Evidence |
|---|---|---|
| Assembly, take planning, script proposal storage | Closed | Existing regressions; script default is now supplied-copy/local rather than Vertex |
| Veo/Runway exclusion | Closed | Contract, route and operation-catalog refusal tests |
| U1 provider consolidation | Closed (code) | Vertex/ADC paths removed; fal funded image edit and local speech regressions |
| U2 / M1â€“M9 open-licensed music and sound cues | Closed (code/local runtime) | Both mux paths, rights, credit coverage and kids publication wiring tested |
| U3 shared policy | Closed | Central active-project, funded-item, one-take and audio-rights guards |
| U4 operation catalog | Closed | `pinaka operations`; policy only, not credential discovery |
| U5 controller split | Closed | `use_cases/`; compatible facade and ownership tests |
| U6 repository split | Closed | `store/`; original transaction/event boundary preserved |
| U7 contract split | Closed | `contracts/`; legacy imports preserved |
| U8 test lanes | Closed | Declared/pinned xdist, slow integration marker, scripts tested outside repository |
| F2 credential boundary | Closed | `policy/secrets.py` refuses credential names and values in persisted payloads; `tests/test_secret_guard.py` |
| F3 one-take guard | Closed | Multiplier/retry field matrix, not a single provider name; `tests/test_policy_guardrails.py` |
| F4 Studio spool hardening | Closed | Response validation, exclusive request write, staleness with `void-job` release; `tests/test_studio_spool.py`, `tests/test_job_void.py` |
| F5 doc and skill drift | Closed | README current-state claims, agent skill, Vertex configs moved to `config/retired/` |
| F6 typing and ledger atomicity | Closed | `python -m mypy` ratchet in CI and `check.ps1`; provider spend check+append in one transaction |
| C1 caption legibility | Closed | `build_srt` wraps/splits to locale `line_chars`/`lines`, reports `cps` violations, optional strict mode; `tests/test_caption_legibility.py` |
| C2 ASR captions and drift | Closed (local code) | `word_timestamps`, `build_srt_from_words`, `transcribe-audio --srt`, `caption-drift`; `tests/test_asr_captions.py` |
| C3 stock selection | Closed | Pexels duration window/preference, provider-id dedupe, deterministic in-point, assembly `start`; `tests/test_stock_selection.py` |
| C4 metadata drafting | Closed | Deterministic title/description/hashtags within channel limits; `draft-metadata`; `tests/test_metadata_draft.py` |
| C5 finishing grade/key/speed | Closed | `grade-asset`, `key-asset`, `change-speed` adapters with hashed receipts; `tests/test_finishing.py` |
| C6 reference-conditioned stills | Closed (code) | Existing image-edit path now accepts ordered reference images (`image_urls`); live consistency unproven; `tests/test_image_references.py` |
| C7 beat detection and cuts | Closed | `detect-beats` (local FFmpeg + numpy), `assemble-film --beat-grid` quantization, song-fit planner; closure and non-claims in [implementation/capability-closures.md](implementation/capability-closures.md) |
| C8 avatar / lip sync | Closed (code) | OmniHuman 1.5 and Sync 3 schemas verified from fal's published OpenAPI; adapter, budget route and likeness-consent gate; live call not yet made; see [implementation/capability-closures.md](implementation/capability-closures.md) |
| P1 live fal image | **Open â€” credential unblocked** | `pinaka fal-check` â†’ `authenticated: true`, 200, `id:secret`; no funded still has run yet |
| P2 live Studio image-to-video/import/assembly | **Blocked, not closed** | Same admission refusal; no accepted paid job exists |

`pinaka fal-check` is the zero-cost credential probe used by P1: a read-only model-catalog call that
answers 200 without a key, so a 401 isolates the credential from route-level auth. See the evidence
table in `implementation/remaining-items.md`.
| B2 paid music generation | Closed / excluded | User chose open-licensed/owned sources only |

The user explicitly authorized **USD 1 maximum total** for P1/P2, with zero OpenMontage credits,
one still, one five-second H3 clip, no retry and no publication. That authorization is not the
blocker: the local fal credential now verifies (200), and Studio's zero-credit budget validator
still blocks the Studio admission that P2 needs. No defaults or alternative credentials were used
to bypass either. New real spend is **USD 0**.

Detailed contract, baseline, request ID, exact refusal, test evidence, rollback and remaining
limitations: [implementation/remaining-items.md](implementation/remaining-items.md).

## Gap-register policy program (Revision 18)

Source of record: `C:\Users\LENOVO\Desktop\pinaka-flow-gaps.txt` (Revision 18).

| Item | Status | Evidence |
|---|---|---|
| F0 brief as production contract | Closed | `plan-from-text` writes `contracts/production-contract-<hash>.json`; `require_production_contract` refuses paid work after the brief changes; `tests/test_flow_policy.py` |
| F1 `idea_only` sourcing cascade | Closed | `derive_slots` and text-planner slots use owned -> official_api -> manual_import -> deterministic -> generated; recipe and planner regressions |
| F2 slots before spend | Closed | `add-slot`, `plan-from-text --persist-slots`; `tests/test_flow_policy.py` |
| F3 source and ceiling checks | Closed (candidate path) | `_enforce_generation_routing` plus per-treatment ceiling in `record_sourcing_candidate`; `tests/test_sourcing_ledger.py` |
| F4 slot-bound paid generation | Closed | `reserve_budget_item` refuses `image.generate`/`video.generate`/`avatar.generate` with no `slot_id`; `tests/test_flow_policy.py` |
| F5 sourcing manifest | Closed | `sourcing-manifest` writes a hashed JSON manifest; final approval records it as a dependency |
| F6 generated rights/provenance | Closed | Generated artifacts carry model, prompt hash, slot, sourcing-decision id and a `rights.synthetic` block; `tests/test_flow_policy.py` |
| F7 full-bleed/no-text composition | Closed | `object-fit: cover`, camera-move support and validation that rejects baked text/letterboxing; `tests/test_composition.py` |
| F8 caption alignment/language | Existing | ASR captions, drift gate and locale rules already shipped; not re-verified live in this bundle |
| F9-F12 candidate ledger, scoring, 2-pick, element slots | Closed | `sourcing_candidates` schema v21, `pick-slot-assets`, `derive-element-slots`, final approval two-pick gate; `tests/test_sourcing_ledger.py` |
| F13 ShortGPT defaults | Closed | `export-captions --source auto` uses narration ASR when present, records plan fallback otherwise; background/segment-query retained; `tests/test_gap_completion.py` |
| F14 effects and sound plan | Closed | `record-effects-plan` validates cue rights, known shots and the flash-rate limit; quality bar gate |
| F15 stills-to-motion library | Closed | Camera push/pull/pan/parallax plus light_glow, blur, particles, flash and fade/dissolve render as authored `data-effect` layers; true-peak limit threaded; `tests/test_gap_completion.py` |
| F16 generation-last gate | Closed | Sourcing decision required before any creative generation reserve; `tests/test_flow_policy.py` |
| F17 motion-level gate | Closed | `motion_levels` schema v22, M1 character minimum at animatic; `tests/test_flow_policy.py` |
| F18-F21 flow ledger, ownership and production record | Closed | `flow_steps` schema v20/v25; skip/blocked policy; `execution_owner`/`studio_role`/`fallback_of` with auto-link and trade-off; `production-record` JSON+Markdown; `tests/test_production_policy_e2e.py` |
| F22-F26 A/B | Closed | `ab_experiments`/`ab_variants` schema v23; `<repo>/ab-delivery` writes A/, B/, experiment and metrics plan; budget items carry `variant_label`/`ab_experiment_id`; `tests/test_sourcing_ledger.py`, `tests/test_gap_completion.py` |
| F27-F30 change-request template, intake, impact and revision | Closed | Template plus `submit-change-request` (hash/field validation), deterministic impact analysis and `create-revision --change_request_id`; request/event tables in schema v25; `tests/test_gap_completion.py` |
| P2 live Studio / avatar / video | Blocked | Studio still rejects `credits: 0`; no accepted live job; no new spend |

Verification for this bundle: 648 tests pass, `ruff check` clean, `ruff format` clean, `mypy`
clean on its configured files, and the v19 paid-run database migrates to v23 with all row counts
unchanged. QC commands and evidence are recorded in the Desktop gap document.
## Live policy run (2026-11-13)

| Item | Status | Evidence |
|---|---|---|
| Five Little Ducks 12 s rhyme | Closed (flow) / motion FAIL | `workspace/rhyme-flow-qc`, project `prj_75f91f43382d4b05bb3d501935d45de4`, archived |
| Budget | Closed | USD 1.00 cap, USD 0.00 spent; `asset-costs` count 0 |
| Sourcing | Closed | 20 candidates from Openverse and Pexels; Pexels evidence 403 recorded; three primary + three backup assets bound |
| Quality bar | Pass | 10/10 gates, failed list empty; technical QA 10 checks, 0 fail |
| Flow ledger | Pass | G0-G7 complete, all with evidence |
| A/B | Pass | ungraded master and graded derivative, distinct hashes |
| Change request | Pass | `C:\Users\LENOVO\Desktop\pinaka-rhyme-flow-qc`, slot artifact ids, hashes and credits |
| Motion | **FAIL: M0 recorded as M1** | camera push/pan only; no character pixels move; motion gate trusts the declared level and has no frame verifier |
| Composition full-bleed | Fixed | `FULL_BLEED_ROLES`; `tests/test_composition.py::test_primary_visual_layer_is_full_bleed` |
| Change-request slot join | Fixed | `_bound_entry` reads slot assets; credits included |
| Slot-coverage gate at final approval | Closed | `require_slot_coverage` runs at FINAL and `quality_bar` adds a `slot_coverage` gate; `tests/test_gap_completion.py` |
| Pexels evidence fallback | Closed | `archive_search_evidence` stores API responses; a 403 writes `provider_blocked` evidence and marks `asset-provider:pexels` unavailable; `tests/test_gap_completion.py` |
| Caption coverage warning | Closed | ASR SRT pads the final cue to the runtime; complete-flow run reports 8.0/8.0 s, ratio 1.0, pass |

## Complete implementation run (Revision 18, commits 2f6752c + 20c52f8 + 94589f4 + b72a09a)

| Item | Status | Evidence |
|---|---|---|
| Remaining gaps F13/F15/F20/F24/F26/F28/F29/F30/F32/F33/F36 | Closed | schema v25, 688 tests, 30/30 gates; `scripts/qc_complete_flow.py` |
| USD 1.00 rhyme run | Closed | project `prj_75ad612d53a9490ca81a13a389755c64`, archive `archive_683489ffd6c2401b81be64d3d78b2180`, USD 0.00 spent |
| A/B delivery | Closed | `A/`, `B/`, `experiment.json`, `metrics-plan.json`; manifest `f88359d0...` |
| Change request + revision | Closed | `crq_daffcf9d...` applied; revision `prj_066cc524...` linked; both ledger steps recorded |
| Motion / captions / QA | Pass | M2 both shots; continuity passes; ASR coverage 1.0; QA 10/10; kids peak -4.39 dBTP vs -2.0 limit |
| Live providers | Unchanged | P1 fal still not re-run; P2 Studio blocked at credits 0; Pexels still 403 |

**Quality caveat.** Revision 18 closes the listed **governance implementation**
items; it does not certify creative quality. The 30/30 result is Policy QC.
The original fixture had a ~1 s near-black cut, placeholder art, 540×960 at
12 fps and no music/SFX. Creative QC remains open; see [fixes.md](fixes.md).

## 10-second counting rhyme fixture (2026-11-14)

| Item | Result |
|---|---|
| Project / archive | `prj_6118755d98124c8eb9afc5b56969826e` / `archive_9d46af3b850a47a4bd9dcc1d55951da9` |
| Delivery | 1080×1920, 24 fps, 10.0 s, 240 frames, hash `d275712f...` |
| Quality bar | 33/33 gates pass; QA 13/13; quality readiness true |
| Luma / motion | 141–184, no dark runs; continuity identity 1.0 |
| Captions / audio | 0.992 coverage, 2 cues; −16.84 LUFS, −4.04 dBTP; music bed + 2 SFX cues |
| Budget | USD 1.00 cap, USD 0.00 spent |
| Quality contract | archived run `flow_fixture`; production contract recorded; publication pending human review |
| Review packet | `art_571faf6a...` (contact sheet + evidence) |
| Change request / revision | `crq_a4741041...` / `prj_f1a66ab6...` |
| Tests | 701 pass |

The archived run is a **flow fixture**; a production contract was recorded on the same exact hash. Publication is allowed by that contract but readiness fails on the human review (model review is `revise`, average 3.22). Live Studio routing falls back because `account_ready=false` (private alpha, no account/credits). Final character art is still procedural. Data binding, SFX, the plan generator and the Studio routing interface are implemented.
publication; production-grade character art, music/SFX, the plan generator,
exact data binding and Studio routing remain open.
