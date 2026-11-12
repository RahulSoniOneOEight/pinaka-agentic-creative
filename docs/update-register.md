# Update register

| Item | Status | Evidence |
|---|---|---|
| Assembly, take planning, script proposal storage | Closed | Existing regressions; script default is now supplied-copy/local rather than Vertex |
| Veo/Runway exclusion | Closed | Contract, route and operation-catalog refusal tests |
| U1 provider consolidation | Closed (code) | Vertex/ADC paths removed; fal funded image edit and local speech regressions |
| U2 / M1–M9 open-licensed music and sound cues | Closed (code/local runtime) | Both mux paths, rights, credit coverage and kids publication wiring tested |
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
| C7 beat detection and cuts | Closed | `detect-beats` (local FFmpeg + numpy), `assemble-film --beat-grid` quantization, song-fit planner; `tests/test_beats.py` |
| C8 avatar / lip sync | Closed (code) | OmniHuman 1.5 and Sync 3 schemas verified from fal's published OpenAPI; adapter, budget route and likeness-consent gate; live call not yet made |
| P1 live fal image | **Open — credential unblocked** | `pinaka fal-check` → `authenticated: true`, 200, `id:secret`; no funded still has run yet |
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
