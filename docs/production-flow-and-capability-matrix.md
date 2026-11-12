# Production flow and capability matrix

- **Status:** implementation guide for Phase 1 and Phase 2
- **Updated:** 11 November 2026
- **Companion to:** `two-phase-production-implementation-plan.md`

This document is the operational map for the controller-first studio. It keeps the Python
`CreativeController` as the only authority for state, approvals, budgets, rights and archives.
OpenMontage Studio is an optional Phase 2 composition renderer, provider gateway, advisory review
surface and creative library, never a second source of truth and never a channel publisher.

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
 grounding        Penpot / HTML      FFmpeg / Chrome   Studio (Phase 2)
 facts, claims,   tokens, components Kokoro / Tesseract ├ render: HyperFrames
 rights, sources  export, compare    whisper / ffprobe  ├ providers: image, video,
       │              │                │               │  upscale, avatar, mesh
       │              │                │               ├ review: advisory packet
       │              │                │               └ library: register, version
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
   separate publication approval → channel adapter → analytics
```

Three hard boundaries:

1. Agents and OpenMontage propose; only the controller commits project state.
2. OpenMontage has its own budget and consent. The controller must also reserve budget and approve
   the exact operation, creating a deliberate double gate for paid work.
3. Studio's `publish` verb registers a file into its own library. It is not a social-channel
   publish. Channel posting stays a controller-gated operation with its own authorization.

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
| Rights and sourcing | 1 | Rights eligibility gate, owned-source-first policy, Openverse + Wikimedia Commons + Internet Archive + Freesound + Unsplash + Pexels metadata search with live commercial-use gating, Unsplash human-selection and download-event gates, Pexels photo and video search, licence-evidence capture, governed download and import, rolling provider quota, font inventory and licence checks | Automated quality scoring, bulk acquisition | Origin-page licence evidence per item; exact font hash | Stock/media only through approved routes |
| Asset evaluation and treatment | 1 | Treatment engine deciding as-is, correct, composite, regenerate, replace or reject; rights checked for the chosen treatment; local analyzer proposing criteria from dimensions, OCR, face count and sharpness; deterministic crop/scale/pad correction; two-layer composite; local ComfyUI inpaint/restyle and generative-upscale routes; vision-model judgement of tone, brand, composition, legibility and safety with an explicit autonomy policy; project-recorded use context that rejects weaker overrides; primary and backup asset per slot with governed promotion; optional A/B pair; slot coverage and preflight rule `R-SLOTS-001`; audio beds with primary and backup; kids flash-rate and loudness checks; made-for-kids channel profile | Vision credential, inpaint checkpoint and ESRGAN model, richer compositing, automatic experiment assignment, automatic tempo and vocal detection | Preservation is not a use decision; every treatment is recorded with reasons; automated acceptance only above configured thresholds | Independent review when the judge is not confident |
| Design system and editable source | 1 | 88 Penpot tokens; hook and service-value; HTML animatic | Decision-clarity and CTA, assembly, accessibility and quality audit, Penpot export | Tokens before geometry; visual self-review | None |
| Animatic and timeline | 1 | Four-segment 10s timeline, HTML, SRT, content-addressed bundles | Shot/slot model, multi-format timelines, versioned recipes | One master timeline; derivatives by hash | Hyperframes renderer in Phase 2 |
| Local video assembly | 1 | Chrome frame capture, H.264 encode, AAC loudness mux, FFmpeg 9.0.2, ImageMagick 7.1.2, Playwright 1.65 | Kinocut, multi-track editing | Deterministic local chain before paid generation | Hosted render/upscale in Phase 2 |
| Narration | 1 | Kokoro AF Heart, model/voice hashes, ASR verification | Piper fallback, hosted voice, consent and likeness, multilingual | Voice identity is a human approval | Hosted TTS routes; local Kokoro blocked by eSpeak |
| Music and SFX | 1 | Open-source beds (primary/backup) and up to 12 SFX/ambience/stinger cues with licence gating, deterministic ducked mix, QA bands, `R-AUDIO-001`, `P-MUSIC-001`, credits artifact and kids cue limits | Paid music generation, beat-sync, stem separation | Commercial-use and modification flags only; attribution travels with the delivery packet | Library registration only |
| Captions and transcript | 1 | SRT sidecar, soft-subtitled derivative, OCR, ASR, caption readability/CPS audit and localization plan | Embedded captions in the approved master, word timing, translated captions, dubbing | Captions are a gate, not an afterthought | Hosted transcription/alignment |
| Managed image generation | 1/2 | Provider-profile contract, routing, external-spend reconciliation, zero-cost Nano Banana API-key adapter, ADC Vertex Nano Banana adapter with per-call cost cap and trial-credit accounting; one 1K sample passed at **4 cents trial credit** | Exact paid Studio/fal route, receipt import, trial-credit ledger settlement | Exact model allowlist; no wildcards; ADC only, never an API key for the Vertex route | fal, OpenAI, Recraft, Runware, Higgsfield |
| Local image generation | 2 | ComfyUI v0.44.0 with CPU PyTorch and SD1.5 fp16; controller adapter with fixed workflow, localhost-only guard and checkpoint allowlist; 256x256/4-step controller run in 25.5s | Quality benchmark, custom workflow catalogue | Local only; model licence and workflow hash recorded; generated text is not deliverable | Not applicable |
| Managed video generation | 2 | None | Exact fal video route, shot policy, animatic gate, cost limits | Only unresolved shots; no generated text/logos/claims | fal video and upscale |
| Avatar, likeness, voice clone | 2 | None | Consent, identity rights, disclosure, provider review | Explicit consent and identity gate | HeyGen, Runware, hosted voice |
| 3D, VFX and motion | 2 | Toolchain installed and probed: Blender 5.2.3, Natron 2.5.0, Synfig 1.5.3, Manim 0.19.1, Inkscape 1.4.4, GIMP 3.2.6, Krita 5.3.4.1, Audacity 4.0.2 | Controller adapters, scene templates, render farm, specialist handoff | Specialist handoff with work packet and re-import | Renderer/gateway option |
| Technical QA | 1 | Container, streams, loudness, black/freeze, silence, OCR, colour, ASR, hashes; independent ffprobe, MediaInfo and OpenCV scene detection; QCTools CLI and MediaConch adapter | Deeper visual-similarity checks, MediaConch policy files | Exact candidate hash; automated checks supplement human review | Review packets in Phase 2 |
| Content, brand and rights QA | 1 | Exact copy, prohibited claims, rights grounding, WCAG contrast | Claim substantiation, legal sign-off, content safety, independent brand review | Human brand/factual gate | External review option |
| Human approvals | 1 | Brief, sourcing, animatic, final, exact-hash archive | Review UI, comments, four-eyes, publication approval | No inferred consent; exact subject shown | Advisory review packets only; never the approving authority |
| Preflight rules | 1 | 11-rule read-only preflight plus CR-01–CR-15 creative evaluation with decision traces | Shot-level scheduling and cost/time estimates per recipe | Fail closed; exact evidence | None |
| Budget and cost | 1 | INR 0 budget, reservations, ledger, tool receipts, refunds, deterministic provider routing and external-spend reconciliation | Managed actual-cost settlement, Studio double-entry, chargeback | Reserve before call; settle actuals; reconcile | Studio budget is a second gate |
| Archive and lineage | 1 | Immutable manifest, hashes, approvals, QA, reconciliation, hashed runtime backup, integrity verification, restore drill, retention plan, verified offsite mirror | Retention enforcement | Archive immutable; publication separate | None; controller owns archive |
| Publication | 1/2 | Delivery packet; channel profiles; fail-closed publication preflight; manual-upload dispatch package with hashed media, captions and metadata | Platform API adapters, scheduling, disclosure execution, publication authorization | Separate human authorization after archive | Library registration only; no channel posting |
| Analytics and optimization | 2 | A/B experiment design, Wilson intervals, two-proportion z-test, Bonferroni adjustment, deterministic reports and CSV platform-export ingestion | Content calendar, automated feedback loop | Measure before optimizing; no silent model swaps | Review plus external analytics |
| Reliability and recovery | 1 | Idempotency, unknown-submission states, reconciliation, deterministic fakes, hashed runtime backup, SQLite integrity verification and restore drill | Managed-provider interruption drill, offsite restore, queue/worker only if measured | Retry only with explicit recovery evidence | Studio job recovery |
| Security and privacy | 1 | Secret rejection, `.local` deny, credential refs, no secrets in logs, data-classification provider routing, non-destructive retention plan, font-licence evidence | Retention enforcement, DPA review, key rotation | Minimum necessary context | Studio owns saved provider keys |
| CI and evaluation | 1 | 326 tests, CI workflow, Ruff/format/compile/secret scan | Golden media fixtures, model evals, prompt regression suite | Test before deploy; evaluate on a fixed benchmark | Provider route evaluation |
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
- Studio `workspace.publish` registers a versioned library item. Treat it as an import destination,
  never as a channel publish or an approval.
- Studio `review` is advisory evidence. It may inform a human decision but cannot satisfy a
  controller approval gate.
- HyperFrames composition sources are authored HTML, which keeps them compatible with the Phase 1
  editable-source decision rather than replacing it.

## OpenMontage Studio capability review (11 November 2026)

Read-only review through the Studio MCP: account status, provider connections, capability catalog,
renderer menu and production list. No production was created, no provider call was made and no credit
or dollar budget was reserved.

Account state: **signed in and connected**. Productions: **none**. Productive membership and device
lease were not exercised, because that requires creating a production.

### Capability classes

| Class | What it provides | Use in this project |
|---|---|---|
| `renderers` | One installed composition renderer, HyperFrames | Candidate Phase 2 replacement for the custom HTML → Playwright → FFmpeg chain |
| `tools` | Executable media operations (generate, clean, upscale, understand, align) | Optional focused operations |
| `providers` | Local, personal-provider and credit routes with `verify` and `quote` | Second budget gate for paid work |
| `skills` | 26 guidance documents: brief, script, scene plan, motion design, cinematography, audio, screenshot reconstruction, Blender specialties | Reference practice, not project authority |
| `components` | Reusable motion sequences, animated backdrops, atmosphere overlays, transitions, caption styles | Faster, consistent motion vocabulary |
| `surfaces` | Browser, editor, terminal, desktop, phone, music, mail and social interfaces | Product-demo scenes |
| `treatments` | Directing guidance for ads, launches, explainers, documentaries, fiction, screen demos, presenters, music-led work | Direction options for later campaigns |

### Renderer readiness

HyperFrames is installed and exposes `composition.author`, `composition.validate`,
`composition.inspect`, `composition.render`, `composition.render-alpha`,
`composition.render-sequence` and `composition.render-still`. Its readiness reports `unknown` until
described and verified. It is the only renderer, and it is materially close to the existing Phase 1
editable-HTML source of record.

### Provider readiness

| Operation | Ready routes | Status |
|---|---|---|
| `image.generate` | `fal.image`, `recraft.recraft-v4-1-vector` | fal and Recraft credentials verified |
| `video.generate` | `fal.video` | Credential verified |
| `video.upscale` | `fal.gemini-omni-flash-1-2-upscale` | Credential verified |
| `avatar.generate` | `fal.minimax-h3-max-lipsync`, `fal.mirage-avatar-x`, `fal.omnihuman-1-5`, `fal.sync-3`, `fal.veed-fabric-1-0` | Credential verified |
| `mesh.generate` | `fal.meshy-7-1` | Credential verified |
| `music.generate` | none | No saved credentials; local ACE-Step not configured |
| `speech.synthesize` | none | No saved credentials; local Kokoro blocked by eSpeak-NG |
| `speech.transcribe` | none | No saved credentials; local whisper.cpp needs approval and a 574 MB model |
| `speech.align` | none | No saved credentials |
| `image.upscale`, `image.cleanup`, `video.cleanup`, `video.understand`, `world.generate`, `stock.acquire` | none | No ready route |
| `scene.author` | n/a | Class present with no entries |

Any fal or Recraft balance must be re-quoted immediately before use; the earlier USD 10.00 figure was
a point-in-time reading and is not treated as current.

### Boundaries confirmed by the review

- Studio `workspace.publish` registers a file as a versioned library item. It does not post to any
  social channel.
- Studio `review` produces an advisory packet. It does not approve, block or authorize spending.
- Studio `request_decision` records a human answer; the controller still owns project approval.
- Studio owns saved provider credentials. They must never be copied into prompts, logs, manifests or
  Git.
- Studio `production` creation starts a shared production record, not a reasoning agent. This project
  has not created one.

### Consequence for the plan

Studio changes the Phase 2 option set in three real ways, and none of them justify moving authority:

1. **Composition**: HyperFrames could replace the bespoke HTML render chain while keeping editable
   HTML as the source form.
2. **Motion vocabulary**: components and transitions reduce hand-built motion work.
3. **Paid generation**: verified fal routes make image, video, upscale and avatar operations reachable
   once a controller adapter, exact route, quote and receipt import exist.

The controller remains the only approval, budget, lineage and archive authority in all three cases.

## Technology stack

| Layer | Technology | Role | Status |
|---|---|---|---|
| Language | Python 3.10+ | Controller, adapters, CLI, QA and recovery | Implemented |
| Contracts | Pydantic | Strict request, model and manifest validation | Implemented |
| State | SQLite | Projects, jobs, approvals, receipts, artifacts, lineage, events | Implemented |
| Packaging | `pyproject.toml`, uv lockfile, setuptools | Reproducible installs and builds | Implemented |
| CLI | argparse | Governed operator commands | Implemented |
| Tests | pytest | Unit, integration, CLI, controller and workflow coverage | Implemented |
| Lint and format | Ruff | Style and correctness gate | Implemented |
| CI | GitHub Actions (Python 3.10/3.11) | Tests, lint, format, compile, secret scan | Implemented |
| Editing environment | OpenCode V2 | Agent commands, skills, session workflow | Implemented |
| Editable design | HTML/CSS animatic | Phase 1 source of record | Implemented |
| Design platform | Penpot 2.18.7 local | Token and component design | Conditional; cloud attach blocked |
| Frame capture | Playwright 1.65, headless Chrome | Deterministic HTML frame export | Implemented |
| Video and audio | FFmpeg 9.0.2, ffprobe | Encode, mux, captions, derivatives, stream analysis | Implemented |
| Image tooling | ImageMagick 7.1.2, Pillow, OpenCV | Stills, composition and frame analysis | Implemented |
| Media inspection | MediaInfo / `pymediainfo` | Independent container and stream facts | Implemented |
| External QA | QCTools, MediaConch | Frame-level analysis and conformance reporting | Implemented |
| OCR | Tesseract 5.4.0 | On-screen copy and CTA verification | Implemented |
| ASR | faster-whisper `tiny.en` | Narration verification and caption timing | Implemented |
| Local TTS | Kokoro ONNX (AF Heart) | Zero-cash narration | Implemented |
| Captions | SRT + `mov_text` | Sidecar and soft-subtitle track | Implemented |
| Local image generation | ComfyUI v0.44.0, SD1.5 fp16, CPU | Local generation with fixed workflow | Implemented |
| Hosted image generation | Vertex ADC via `google-genai` | Nano Banana under explicit cost ceiling | Implemented |
| Studio renderer | OpenMontage HyperFrames | Authored HTML composition, validate, inspect, render | Available; not yet wired |
| Studio providers | fal, Recraft (verified) | Image, video, upscale, avatar, mesh | Available; no controller adapter |
| Studio review | Advisory review packets | Optional independent inspection | Available; unused |
| Studio library | Versioned creative items | Registration and handoff | Available; unused |
| Specialty 3D and motion | Blender 5.2.3, Natron 2.5.0, Synfig 1.5.3, Manim 0.19.1 | Scene, VFX and motion work | Installed; no controller adapter |
| Vector and raster | Inkscape 1.4.4, GIMP 3.2.6, Krita 5.3.4.1 | Asset preparation | Installed |
| Audio editing | Audacity 4.0.2 | Manual audio finishing | Installed |
| Sourcing | Openverse, Wikimedia Commons, Internet Archive, Freesound | Rights-aware asset discovery | Implemented |
| Fonts | FontTools | Exact-hash font inventory and licence evidence | Implemented |
| Analytics | Python statistics | Wilson intervals, two-proportion test, Bonferroni | Implemented |
| Backup | Filesystem snapshots, SQLite integrity checks | Backup, verification, restore drill | Local complete; off-device pending |
| Secrets | External credential store | Keeps secrets out of prompts, logs, database and Git | Implemented |
| Publication | Channel profiles, fail-closed preflight | Authorization and artifact gating | Implemented; adapters pending |

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
| G2 — Captions v2 | Approved, recording open | Captioned candidate `5a3421e7…e44c0` approved by exact hash; controller recording, lineage and delivery packet remain |
| G3 — Independent media QA | Done | ffprobe, MediaInfo, OpenCV, OCR, ASR and loudness checks pass 20/20 |
| G4 — Rules and sourcing | Done | CR-01–CR-15, recipes, slot policies, Openverse, Wikimedia Commons and Internet Archive adapters |
| G5 — Managed gateway | Partial | Verified fal and Recraft routes exist; controller adapter, exact route, quote and receipt import remain |
| G6 — Publication | Partial | Channel profiles and a fail-closed publication preflight exist; channel adapters and separate authorization remain |
| G7 — Phase 2 | Partial | Local toolchain installed; ComfyUI, QCTools/MediaConch and A/B analytics are controller-integrated; dubbing, platform API posting, content calendar and deeper creative-tool adapters remain |
| G8 — Studio composition | Available | HyperFrames renderer, components and treatments exist; evaluate against the current HTML render chain before adopting |
