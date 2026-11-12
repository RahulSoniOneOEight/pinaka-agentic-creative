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

## Gap-register policy program (Revision 15)

Source of record: `C:\Users\LENOVO\Desktop\pinaka-flow-gaps.txt` (Revision 15).

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
| F13 ShortGPT defaults | Partial | ASR, background mode and segment-query code exists; ASR is not yet the forced default |
| F14 effects and sound plan | Closed | `record-effects-plan` validates cue rights, known shots and the flash-rate limit; quality bar gate |
| F15 stills-to-motion library | Partial | Camera moves (push/pull/pan/parallax) render; light/blur/particle libraries are planned but not rendered |
| F16 generation-last gate | Closed | Sourcing decision required before any creative generation reserve; `tests/test_flow_policy.py` |
| F17 motion-level gate | Closed | `motion_levels` schema v22, M1 character minimum at animatic; `tests/test_flow_policy.py` |
| F18-F21 flow ledger and production record | Closed | `flow_steps` schema v20, skip/blocked policy, `production-record` JSON+Markdown; `tests/test_production_policy_e2e.py` |
| F22-F26 A/B | Closed | `ab_experiments`/`ab_variants` schema v23, distinct hashes, status and final gate when declared; `tests/test_sourcing_ledger.py` |
| F27-F30 change-request template | Closed | `change-request` writes Markdown and YAML with element, slot and artifact hashes; delivered path recorded |
| P2 live Studio / avatar / video | Blocked | Studio still rejects `credits: 0`; no accepted live job; no new spend |

Verification for this bundle: 648 tests pass, `ruff check` clean, `ruff format` clean, `mypy`
clean on its configured files, and the v19 paid-run database migrates to v23 with all row counts
unchanged. QC commands and evidence are recorded in the Desktop gap document.