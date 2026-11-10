# Production flow and capability matrix

- **Status:** implementation guide for Phase 1 and Phase 2
- **Updated:** 10 November 2026
- **Companion to:** `two-phase-production-implementation-plan.md`

This document is the operational map for the controller-first studio. It keeps the Python
`CreativeController` as the only authority for state, approvals, budgets, rights and archives.
OpenMontage Studio is an optional Phase 2 provider, render, review and publication gateway, never a
second source of truth.

## End-to-end flow

```text
Operator: OpenCode / CLI / review UI
                    │ proposals only
                    ▼
┌──────────────────────────────────────────────────────────────────────┐
│ CreativeController — sole authority                                  │
│ state · approvals · budgets · rights · hashes · idempotency · events │
│ recovery · archive · publication gate                                │
└──────┬──────────────┬────────────────┬───────────────┬───────────────┘
       │              │                │               │
       ▼              ▼                ▼               ▼
 Planning &       Design / Animatic  Local production  Optional OpenMontage
 grounding        Penpot / HTML      FFmpeg / Chrome   Studio gateway
 facts, claims,   tokens, components Kokoro / Tesseract image · video · voice
 rights, sources  export, compare    whisper / ffprobe  music · render · review
       │              │                │               │
       └──────────────┴────────────────┴───────────────┘
                              │
                              ▼
                 QA + human gates
     technical · OCR/ASR · brand · rights · accessibility
                              │
                              ▼
          exact-hash approval → immutable archive
                              │
                              ▼
        separate publication approval → publish → analytics
```

Two hard boundaries:

1. Agents and OpenMontage propose; only the controller commits project state.
2. OpenMontage has its own budget and consent. The controller must also reserve budget and approve
   the exact operation, creating a deliberate double gate for paid work.

## Stage gates

| Gate | Name | Evidence | Authority |
|---|---|---|---|
| G0 | Intake | client, objective, audience, format, rights context, budget | Controller + human |
| G1 | Brief | normalized brief, deliverables, segments, provider policy | Human approval |
| G2 | Grounding | brand, claims, sources, facts, uncertainty | Human approval |
| G3 | Sourcing | rights eligibility, origin evidence, attribution | Human approval |
| G4 | Animatic | timeline, editable source, narration, captions draft | Human approval |
| G5 | Final candidate | exact render hash, technical/content/rights QA | Human review |
| G6 | Archive | immutable manifest, approvals, costs, lineage | Exact-hash human approval |
| G7 | Publication | channel packet, disclosure, scheduling | Separate human approval |

## Master capability matrix

| Stage / capability | Phase | Implemented | Pending | Control / best practice | OpenMontage role |
|---|---|---|---|---|---|
| Intake, contract, deliverables | 1 | Pilot contract, deliverables, segments, archive | Campaign templates, multi-deliverable briefs | One contract = one approved scope | Production metadata only |
| Planning and grounding | 1 | Typed brand/claims/source bundles, deterministic timeline, CR-01–CR-15 engine with decision traces, versioned recipes and slot policies | Shot-level scheduling, cost/time estimates per recipe | Facts and claims before creative | None; controller owns truth |
| Rights and sourcing | 1 | Rights eligibility gate, owned-source-first policy, Openverse + Wikimedia Commons + Internet Archive + Freesound metadata search with live commercial-use gating, font inventory and licence checks | Provider-key routes, automated origin-page evidence capture | Origin-page licence evidence per item; exact font hash | Stock/media only through approved routes |
| Design system and editable source | 1 | 88 Penpot tokens; hook and service-value; HTML animatic | Decision-clarity and CTA, assembly, accessibility and quality audit, Penpot export | Tokens before geometry; visual self-review | None |
| Animatic and timeline | 1 | Four-segment 10s timeline, HTML, SRT, content-addressed bundles | Shot/slot model, multi-format timelines, versioned recipes | One master timeline; derivatives by hash | Hyperframes renderer in Phase 2 |
| Local video assembly | 1 | Chrome frame capture, H.264 encode, AAC loudness mux, FFmpeg 9.0.2, ImageMagick 7.1.2, Playwright 1.65 | Kinocut, multi-track editing | Deterministic local chain before paid generation | Hosted render/upscale in Phase 2 |
| Narration | 1 | Kokoro AF Heart, model/voice hashes, ASR verification | Piper fallback, hosted voice, consent and likeness, multilingual | Voice identity is a human approval | Hosted TTS routes; local Kokoro blocked by eSpeak |
| Music and SFX | 1 | None | Licensed music/SFX, ducking, mix, rights | Music licence per track; no unlicensed beds | ElevenLabs, Google, ACE-Step local |
| Captions and transcript | 1 | SRT sidecar, soft-subtitled derivative, OCR, ASR, caption readability/CPS audit and localization plan | Embedded captions in the approved master, word timing, translated captions, dubbing | Captions are a gate, not an afterthought | Hosted transcription/alignment |
| Managed image generation | 1/2 | Provider-profile contract, routing, external-spend reconciliation, zero-cost Nano Banana API-key adapter, ADC Vertex Nano Banana adapter with per-call cost cap and trial-credit accounting; one 1K sample passed at **4 cents trial credit** | Exact paid Studio/fal route, receipt import, trial-credit ledger settlement | Exact model allowlist; no wildcards; ADC only, never an API key for the Vertex route | fal, OpenAI, Recraft, Runware, Higgsfield |
| Local image generation | 2 | ComfyUI v0.44.0 with CPU PyTorch and SD1.5 fp16; controller adapter with fixed workflow, localhost-only guard and checkpoint allowlist; 256x256/4-step controller run in 25.5s | Quality benchmark, custom workflow catalogue | Local only; model licence and workflow hash recorded; generated text is not deliverable | Not applicable |
| Managed video generation | 2 | None | Exact fal video route, shot policy, animatic gate, cost limits | Only unresolved shots; no generated text/logos/claims | fal video and upscale |
| Avatar, likeness, voice clone | 2 | None | Consent, identity rights, disclosure, provider review | Explicit consent and identity gate | HeyGen, Runware, hosted voice |
| 3D, VFX and motion | 2 | Toolchain installed and probed: Blender 5.2.3, Natron 2.5.0, Synfig 1.5.3, Manim 0.19.1, Inkscape 1.4.4, GIMP 3.2.6, Krita 5.3.4.1, Audacity 4.0.2 | Controller adapters, scene templates, render farm, specialist handoff | Specialist handoff with work packet and re-import | Renderer/gateway option |
| Technical QA | 1 | Container, streams, loudness, black/freeze, silence, OCR, colour, ASR, hashes; independent ffprobe, MediaInfo and OpenCV scene detection; QCTools CLI and MediaConch adapter | Deeper visual-similarity checks, MediaConch policy files | Exact candidate hash; automated checks supplement human review | Review packets in Phase 2 |
| Content, brand and rights QA | 1 | Exact copy, prohibited claims, rights grounding, WCAG contrast | Claim substantiation, legal sign-off, content safety, independent brand review | Human brand/factual gate | External review option |
| Human approvals | 1 | Brief, sourcing, animatic, final, exact-hash archive | Review UI, comments, four-eyes, publication approval | No inferred consent; exact subject shown | Studio review UI/publication |
| Preflight rules | 1 | 11-rule read-only preflight plus CR-01–CR-15 creative evaluation with decision traces | Shot-level scheduling and cost/time estimates per recipe | Fail closed; exact evidence | None |
| Budget and cost | 1 | INR 0 budget, reservations, ledger, tool receipts, refunds, deterministic provider routing and external-spend reconciliation | Managed actual-cost settlement, Studio double-entry, chargeback | Reserve before call; settle actuals; reconcile | Studio budget is a second gate |
| Archive and lineage | 1 | Immutable manifest, hashes, approvals, QA, reconciliation, hashed runtime backup, integrity verification, restore drill, retention plan, verified offsite mirror | Retention enforcement | Archive immutable; publication separate | None; controller owns archive |
| Publication | 1/2 | Delivery packet; channel profiles; fail-closed publication preflight; manual-upload dispatch package with hashed media, captions and metadata | Platform API adapters, scheduling, disclosure execution, publication authorization | Separate human authorization after archive | Studio publication is a candidate |
| Analytics and optimization | 2 | A/B experiment design, Wilson intervals, two-proportion z-test, Bonferroni adjustment, deterministic reports and CSV platform-export ingestion | Content calendar, automated feedback loop | Measure before optimizing; no silent model swaps | Review plus external analytics |
| Reliability and recovery | 1 | Idempotency, unknown-submission states, reconciliation, deterministic fakes, hashed runtime backup, SQLite integrity verification and restore drill | Managed-provider interruption drill, offsite restore, queue/worker only if measured | Retry only with explicit recovery evidence | Studio job recovery |
| Security and privacy | 1 | Secret rejection, `.local` deny, credential refs, no secrets in logs, data-classification provider routing, non-destructive retention plan, font-licence evidence | Retention enforcement, DPA review, key rotation | Minimum necessary context | Studio owns saved provider keys |
| CI and evaluation | 1 | 193 tests, CI workflow, Ruff/format/compile/secret scan | Golden media fixtures, model evals, prompt regression suite | Test before deploy; evaluate on a fixed benchmark | Provider route evaluation |
| Multi-format and localization | 1/2 | 9:16 master; 1:1, 16:9 and 4:5 blurred-crop derivatives; delivery packet; caption readability/CPS audit and locale plans | Format-specific re-layout, derivative approval, dubbing, translated captions | One master, many hashed derivatives | Hosted TTS/transcription/upscale |

### Paid test envelope

The person authorized a maximum of **USD 1.00** for the capability test and implementation run. No
paid call was executed. The only verified funded route is OpenMontage Studio's saved fal key, which
is a separate budget/consent authority; using it requires a controller-owned exact route, a reserved
budget and receipt import. The local capability work in this run cost **USD 0.00**. The ceiling is
recorded in `config/tests/usd-1-provider-test.json`.

### Cost-controlled routes

| Route | Cost | Status |
|---|---|---|
| Nano Banana (`gemini-2.5-flash-image`) | Free-tier only; adapter refuses paid billing | Implemented; blocked without `GEMINI_API_KEY` |
| Vertex Nano Banana ADC (`gemini-nano-banana-2.1`) | ~$0.034 per 1K image of trial credit | Controller sample passed: 1376x768, 4 cents actual |
| ComfyUI + SD1.5 on CPU | USD 0.00 after free model download | Working; controller adapter and smoke test pass |
| Blender, Inkscape, GIMP, Krita, Audacity, Natron, Synfig, Manim | USD 0.00 | Installed |
| QCTools CLI, MediaConch CLI | USD 0.00 | Installed and executed |
| OpenMontage Studio fal | Paid (USD 10.00 available) | **Not used** |

### Penpot decision

Cloud Penpot editing was blocked by the multi-user plugin token session: the MCP key was accepted,
but the browser plugin did not attach to the remote session. The person accepted the editable HTML
animatic as the Phase 1 source of record. Local Penpot 2.18.7 is healthy and backed up; completing
the cloud design remains a post-archive task.

### Caption readability finding

The archived pilot SRT covers all 4 cues and matches the approved script, but it does not meet
standard caption readability limits:

| Cue | Finding |
|---|---|
| 2 | One line has 63 characters; the 42-character limit is exceeded |
| 4 | One line has 46 characters; the limit is exceeded |
| 4 | The cue reads at 23.0 characters/second; the 20.0 limit is exceeded |

The independent review and archive remain valid for the approved master. A v2 captioned master
should re-break the lines, re-time cue 4, and carry the corrected SRT. The localization plan flags
Hindi, German and Japanese targets as `review_timing` because translated text will exceed their
reading-speed limits.

## OpenMontage Studio integration model

Use Studio only through a controller-owned adapter.

```text
CreativeController
  → reserve budget, approval and data classification
  → StudioGateway.invoke(exact route, input hashes, limits, work-item id)
  → Studio consent + Studio budget gate
  → provider job
  → import output into the project by hash
  → controller records actual cost, lineage and receipt
  → Studio remains a provider, never the state owner
```

Rules:

- Never let Studio write SQLite, manifests or project state directly.
- Import every output through the controller or a governed workspace import.
- Keep saved provider keys in Studio; never copy them into prompts, logs or the repository.
- The controller budget and Studio budget must both pass before a paid call.
- The current INR 0 pilot policy means Studio paid routes are disabled, including the verified fal
  balance.
- Studio's local Kokoro is blocked by eSpeak-NG; the project already has a working local Kokoro chain.

### Verified Studio readiness (9 November 2026)

| Route | Readiness | Evidence |
|---|---|---|
| `fal.image` gateway | Ready | Saved `FAL_KEY` verified with **USD 10.00** available; 10 image models listed |
| `fal.video-upscale` | Ready | Same verified fal key |
| `recraft.recraft-v4-1-vector` | Credential accepted, unusable | 0 API units |
| Music, speech, transcription | Blocked | No saved credentials for ElevenLabs, Google, OpenAI, Azure, Cartesia, MiniMax, Runware or xAI |
| Studio local Kokoro | Blocked | eSpeak-NG path not configured |
| Studio local Whisper | Blocked | whisper.cpp not enabled; 574 MB model download needs explicit approval |

The controller has no exact Studio route, quote or receipt-import path yet, so the fal balance was
not used. The controller-side routing and external-spend reconciliation are implemented; the remaining
step is to bind one exact `fal.image` model, capture its quote, reserve the work item and import the
receipt and artifact by hash.
- Studio local whisper.cpp needs a large model and explicit approval; faster-whisper already covers
  review transcription.

## Best practices for content-creation AI agents

### Truth and governance

1. Agents propose; the controller commits.
2. Ground facts, sources and claims before writing or generating.
3. Every product or service claim has an approved source or human approval.
4. Version prompts, recipes, models and provider profiles.
5. Final approval binds one exact hash, not a description.

### Rights and brand

6. Rights eligibility passes before an asset enters composition.
7. Owned assets first, then official APIs, then governed manual imports.
8. Exact text, logos and product identity are composed, not model-generated.
9. Voice and likeness need explicit consent and a licence record.
10. Music and fonts carry their own licences.

### Production and QA

11. One master, many derivatives; every format is a new hashed artifact.
12. Every artifact records parentage, model, prompt, seed, licence and cost.
13. Export, look, fix, then present: visual self-review before human review.
14. Accessibility and captions are gates.
15. QA runs against the exact candidate hash.
16. Platform specs are explicit: 1080×1920, 30fps, H.264/AAC, −16 LUFS, −1.5 dBTP, captions.

### Reliability and cost

17. Reserve budget before a paid call; settle actuals; reconcile.
18. Idempotency keys for every operation; unknown submissions need recovery.
19. Deterministic fakes and golden fixtures for tests.
20. Do not add queues, vector databases or distributed workers until measured need.
21. Archive immutably; publish separately.
22. Back up database, assets and manifests; test restore.

### Security and privacy

23. Secrets never appear in prompts, logs, SQLite, manifests, fixtures or Git.
24. Use credential references, not values.
25. Apply data classification and retention per provider and project.
26. Send minimum necessary context to any model.

### Evaluation and improvement

27. Maintain a fixed evaluation set for scripts, images, voice and video.
28. Human review of brand, facts, rights and continuity remains mandatory.
29. Analytics closes the loop only after publication and consent.
30. Record deferred work explicitly; no silent truncation.

## Recommended next gates

| Gate | Status | Outcome |
|---|---|---|
| G1 — Editable source | Deferred | Finish Penpot or formally accept HTML as source (accepted amendment recorded) |
| G2 — Captions v2 | Open | Embed captions in a new master, fix the readability findings and re-approve the exact hash |
| G3 — Independent media QA | Done | ffprobe, MediaInfo, OpenCV, OCR, ASR and loudness checks pass 20/20 |
| G4 — Rules and sourcing | Done | CR-01–CR-15, recipes, slot policies, Openverse, Wikimedia Commons and Internet Archive adapters |
| G5 — Managed gateway | Partial | Provider routing and external-spend reconciliation are implemented; the exact Studio/fal route, quote and receipt import remain |
| G6 — Publication | Partial | Channel profiles and a fail-closed publication preflight exist; channel adapters and separate authorization remain |
| G7 — Phase 2 | Partial | Local toolchain installed; ComfyUI, QCTools/MediaConch and A/B analytics are controller-integrated; dubbing, platform API posting, content calendar and deeper creative-tool adapters remain |
