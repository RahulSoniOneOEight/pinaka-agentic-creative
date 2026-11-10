# Two-phase production implementation plan

- **Status:** implementation roadmap; no production integration is enabled by this document
- **Updated:** 9 November 2026

> Companion: `production-flow-and-capability-matrix.md` for the updated end-to-end flow, master
> capability matrix, OpenMontage Studio integration model and agent best practices.

This roadmap turns the tool inventory in `open-source-production-toolchain.md` into exactly two
production phases. It assumes the controller-first foundation is already implemented. The completed
foundation milestone and the two production phases below are different scopes: **Production Phase 1**
starts from the existing controller, rather than replacing it.

The plan deliberately includes tools previously described as optional. In real production, vector
editing, timeline correction, audio cleanup, compositing, diagrams, and 3D work are often necessary.
"Optional" therefore means **selected by the brief**, not unsupported or unlikely to be used.

Production may use approved OpenAI GPT models, DeepSeek models, fal endpoints, and other managed
services. These are operational capabilities, not exceptions, but every exact provider/model/endpoint
remains controller-authorized, privacy-reviewed, metered, and recoverable. "All services are
available" never means a wildcard credential or unrestricted provider call.

## Delivery model

```text
COMPLETED FOUNDATION
controller + SQLite + approvals + budgets + rights + artifacts + mock adapter
                              |
                              v
PRODUCTION PHASE 1 â€” OPERATIONAL HYBRID STUDIO
OpenCode + GPT/DeepSeek + fal + sourcing/design/edit/audio/QA + final archive
                              |
                     Phase 1 human gate
                              |
                              v
PRODUCTION PHASE 2 â€” ADVANCED, RESILIENT, AND OPTIMIZED STUDIO
local generation + 3D/VFX/animation + deep QA + multi-provider routing
                              |
                     Phase 2 human gate
                              v
separate future publishing authorization; never implied by production approval
```

Each phase is delivered as several small, independently verified bundles. "Two phases" does not mean
one large dependency installation or one unreviewable commit per phase.

## Tool integration modes

Not every practical tool should receive the same kind of adapter.

| Mode | Use when | Controller behavior | Examples |
|---|---|---|---|
| **Native adapter** | A stable CLI, API, or library supports deterministic automation | Validate request, invoke with an argument array or typed client, capture receipt, import outputs | FFmpeg, ffprobe, ImageMagick, MediaInfo, whisper.cpp |
| **Governed service/MCP** | A local service or MCP exposes scoped structured operations | Authorize exact operation and scope, record remote/local endpoint identity, import verified export | Penpot, ComfyUI |
| **Governed specialist handoff** | A GUI is valuable in practice but brittle to automate | Produce a work packet; a human edits a derivative; controller re-imports it with project/tool/export evidence | Kdenlive, Inkscape, Krita, GIMP, Audacity, Ardour, OpenToonz |
| **Conditional headless adapter** | A specialist has a reliable batch/headless mode used by selected recipes | Invoke only for an approved recipe and bounded resource profile | Blender, Manim, Natron where supported |
| **Managed model/service adapter** | An approved hosted model or service is selected for quality, capability, cost, privacy, or deadline | Validate/redact inputs, reserve cost, invoke an exact allowlisted model/endpoint, reconcile usage and response | OpenAI GPT, DeepSeek, fal, an approved hosted voice service |

This distinction makes specialist software usable without giving every application direct access to
SQLite, project state, credentials, or protected originals.

## Shared contracts required before tool adapters

Production Phase 1 starts by adding generic contracts that both phases reuse.

### Tool profile

Each registered tool records:

- stable tool ID and display name;
- integration mode and supported operations;
- executable, endpoint, or MCP identity;
- exact version and binary/build fingerprint where available;
- software licence and third-party notice location;
- network policy, required approvals, and credential policy;
- supported input/output media and deterministic fallback;
- capability state: `configured`, `working`, `degraded`, or `unavailable`.

### Provider and model profile

Each managed provider/model combination separately records:

- provider, endpoint, model/version, media types, and approved roles;
- credential reference name without the credential value;
- data classification ceiling and prohibited input categories;
- privacy, retention, deletion, residency, and training-use terms;
- model/output licence and commercial-use disposition;
- token, request, attempt, image, video-second, and cost limits as applicable;
- timeout, retry, idempotency, rate-limit, and `submission_unknown` policy;
- allowed fallback models and human approval requirements.

### Tool operation request

Every requested operation includes:

- project, deliverable, segment, shot, and slot references where applicable;
- input artifact IDs and expected hashes;
- typed operation and parameters;
- exact provider/model profile when the operation is managed;
- validated prompt or request hash and redaction disposition;
- output declaration and target media profile;
- token, time, memory, attempts, generated duration, and cost ceilings;
- network allowance and approval dependencies;
- idempotency key and fallback order.

### Execution receipt

Every invocation or specialist re-import records:

- tool/version/build identity;
- redacted argument or workflow hash;
- start/end time, exit state, elapsed time, and cost;
- provider request/job ID, model-reported usage, and settlement state where applicable;
- input/output hashes and artifact lineage;
- stdout/stderr or service-response hashes without secrets;
- operator identity for manual work;
- QA result, failure reason, and selected fallback.

Secrets, unrestricted environment dumps, and raw provider credentials are never placed in receipts.

## Production Phase 1 â€” Operational hybrid studio

### Phase 1 outcome

An operator can start or resume a project from OpenCode and complete a rights-cleared production from
brief through final approved archive. The operational path combines local tools with approved OpenAI
GPT, DeepSeek, fal, stock, and voice services. Common specialist edits are supported through governed
handoffs. Automatic publishing remains separate from production completion.

### Phase 1 entry criteria

- Existing controller, SQLite, approvals, artifact store, budget ledger, rights evaluator, and mock
  adapter tests remain green.
- Pilot brief identifies the delivery format, language, rights context, budget, and acceptance owner.
- Pilot contract identifies allowed OpenAI GPT and DeepSeek models, fal endpoints, data classes,
  service budgets, and credential reference names.
- The target machine has enough disk space for immutable originals and derivatives.
- Every external connection is disabled until its individual spike is approved.

### Phase 1 work packages

#### P1-A â€” OpenCode operator workflow and complete controller surface

Implement:

- `.opencode/skills/pinaka-project/SKILL.md`;
- `/pinaka/new`, `/pinaka/plan`, `/pinaka/status`, `/pinaka/next`, `/pinaka/source`,
  `/pinaka/approve`, `/pinaka/produce`, `/pinaka/qa`, and `/pinaka/archive`;
- safe OpenCode permissions for network calls, external writes, paid work, publishing, and Git pushes;
- CLI coverage for shots, slots, sourced imports, artifacts, tool operations, reconciliation, QA, and
  archive;
- resume from SQLite and artifact manifests rather than conversation history;
- generic tool-profile, operation-request, execution-receipt, and specialist-handoff contracts;
- filesystem/SQLite reconciliation for orphaned files, records, reservations, and unknown
  submissions;
- CI for tests, Ruff, compilation, documentation references, and secret checks.

Acceptance:

- commands are proposal templates, not authorization shortcuts;
- each state mutation still passes through `CreativeController`;
- a fresh OpenCode session can identify the next valid action from persisted state;
- deterministic fake tool adapters cover success, failure, timeout, retry, and unavailable states.

#### P1-B â€” Purpose-neutral schema and deterministic planning

Implement the planning capabilities required before tools can be selected safely:

- additive schema v2 with `Project -> Deliverable -> Segment -> Shot -> Slot`;
- independent output, product-role, form, structure, subject, tone, outcome, truth, use-context,
  audience, accessibility, budget, and deadline fields;
- typed grounding bundles for product facts, claims, tutorials, sources, stories, series, brands, and
  platforms;
- `GROUNDING_LOCKED` with a backward-compatible path from legacy `FACTS_LOCKED` projects;
- versioned recipes and deterministic CR-01 through CR-15 rule evaluation with decision traces;
- typed exactness, risk, realism, treatment, effects, effort, and allowed-source policies per slot;
- stage, shot, attempt, generated-seconds, and deadline/fallback allowances;
- validated planner proposals containing input hashes, alternatives, uncertainty, cost, and schedule
  effects.

Acceptance:

- existing v1 projects remain readable and receive an explicit migration path;
- identical inputs and versions produce identical rules, constraints, and decision traces;
- fictional or product-free work can advance with the correct grounding bundle;
- tool selection follows slot policy and cannot override rights, risk, budget, or approval rules.

#### P1-C â€” Managed reasoning and model gateway

Implement controller-facing provider profiles and adapters for:

- **OpenAI GPT:** one exact approved text/reasoning model for planning, research synthesis, scripts,
  structured proposals, and QA assistance; add a vision-capable profile only when the pilot needs it;
- **DeepSeek:** one exact approved reasoning/chat model for planning, alternatives, script support,
  comparison, and provider fallback;
- **additional model services:** one profile at a time through the same contract, never through a
  wildcard OpenAI-compatible endpoint or shared unrestricted credential.

OpenCode may use an approved GPT or DeepSeek model to assist the operator. That conversational model
still only proposes. Any call whose result becomes production state, consumes a project budget, or
handles protected inputs must be submitted as a controller-authorized job through the provider
adapter.

Implement:

- typed role-specific input/output contracts rather than free-form production mutations;
- prompt-template versioning, input/prompt hashes, and structured-output validation;
- data classification, minimum-necessary context, redaction, and prohibited-input checks;
- token/request/cost reservations and actual-usage settlement;
- idempotency, rate-limit handling, timeout, retry, and unknown-submission recovery;
- policy-based selection using capability, quality, privacy, latency, and cost;
- deterministic fakes plus one bounded connection spike for the selected GPT profile and one for the
  selected DeepSeek profile.

The consumer ChatGPT website is not automated. "ChatGPT" capability means an approved OpenAI GPT
model reached through an API or OpenCode provider that can be identified, metered, and governed.

#### P1-D â€” Deterministic video, image, and timeline production

Implement native adapters and bounded spikes for:

- **FFmpeg and ffprobe:** cut, scale, crop, overlay, captions, audio mix, concat, export, and probe;
- **Kinocut:** equivalent typed operations, preflight, receipt capture, and direct FFmpeg fallback;
- **ImageMagick:** resize, crop, alpha, masks, composition, conversion, and image inspection;
- **libvips:** supported high-volume image path when benchmarked input size justifies it;
- **MediaInfo:** independent metadata validation.

Support governed specialist handoffs for:

- **Kdenlive:** practical timeline correction, title adjustment, proxy review, and manual finishing;
- **Shotcut:** alternate timeline editor when Kdenlive is unavailable or unsuitable;
- **MoviePy:** not a default; permit a future native adapter only for a demonstrated gap that cannot
  be expressed safely through Kinocut or direct FFmpeg.

Every Kdenlive or Shotcut handoff must preserve the work packet, project file when portable, tool
version, source artifact references, export settings, output hash, and operator note.

#### P1-E â€” Editable design, browser rendering, and practical graphics

Implement:

- **Penpot:** one scoped component/layout edit, editable source preservation, selected export,
  reconstruction comparison, token/font evidence, and controller import;
- **Playwright with pinned Chromium:** approved source verification, deterministic screenshots,
  HTML/CSS title cards, browser capture, and visual comparison;
- **Manim:** conditional headless adapter for educational diagrams and explainers.

Support governed specialist handoffs for:

- **Inkscape:** logos, icons, diagrams, masks, and SVG cleanup;
- **Krita:** raster illustration, painting, and frame-level artwork;
- **GIMP:** photo repair, cut-outs, texture work, and manual compositing.

These tools are practical Phase 1 capabilities. They are not mandatory for every project, but a
recipe may select them and the controller must know how to issue and re-import their work safely.

#### P1-F â€” Narration, captions, audio cleanup, and baseline QA

Implement:

- **Kokoro:** first local narration candidate with approved voice evidence;
- **Piper:** fast local fallback where its voice licence and quality are acceptable;
- **whisper.cpp:** transcription, word/segment timing, caption draft, and narration-versus-script
  comparison;
- **FFmpeg audio filters:** resample, mix, fade, normalize, loudness measure, and mux;
- **OpenCV:** bounded black/frozen-frame, blur, boundary, and image-similarity checks;
- **Tesseract OCR:** rendered caption, label, and disclaimer checks.

Support **Audacity** as a governed specialist handoff for noise cleanup, repair, and human listening
review. Preserve the approved script, raw narration, edit project or operation note, export settings,
and resulting hash. Voice cloning remains outside Phase 1.

#### P1-G â€” Rights-aware sourcing and fonts

Implement or validate:

- owned-asset-first selection;
- one bounded **Openverse** API search with exact origin-page licence verification;
- governed import from **Wikimedia Commons**, **Freesound**, and **Internet Archive** with per-item
  evidence;
- exact font-file and licence capture for **Google Fonts**, **Fontsource**, or approved supplied fonts;
- existing Pexels/Unsplash provider policies, with live access enabled only after their separate
  credential and terms gates;
- Mixkit and Videezy manual-only paths with eligible and blocked fixtures.

No source is visually ranked into production until rights eligibility has passed.

#### P1-H â€” Managed media generation and additional services

Implement one controller-governed **fal still endpoint** with:

- exact endpoint/model allowlisting and request schema validation;
- input/reference eligibility and privacy checks;
- maximum attempts, dimensions, elapsed time, and cost reservation;
- provider job persistence, idempotency, polling, timeout, and `submission_unknown` recovery;
- response, usage, actual-cost, output-hash, lineage, and rights reconciliation.

After the still path passes and a human approves an animatic, implement one **fal video endpoint** for
the exact unresolved shot. Bound model, references, prompt, dimensions, seconds, attempts, and maximum
cost. The endpoint must not generate text, logos, product identity, claims, or other exact elements
that the slot policy reserves for editable composition.

The same provider-profile contract may add an approved hosted voice, transcription, search, or other
specialist service needed by the pilot. Each service is an independent profile and connection gate;
approval of OpenAI, DeepSeek, or fal does not authorize another provider or endpoint.

#### P1-I â€” Operational pilot and recovery proof

Complete one pilot that uses enough of the stack to prove integration rather than isolated commands:

1. Start from `/pinaka/new` or an equivalent natural-language request.
2. Use a controller-governed GPT or DeepSeek job to produce a validated planning, research, script, or
   QA proposal; run a bounded connection/contract check for the other model provider.
3. Approve the normalized brief, grounding, plan, provider profiles, and service budget.
4. Import at least one owned or rights-cleared source.
5. Generate at least one rights-approved still through the allowlisted fal endpoint.
6. Create and approve an animatic; generate one bounded fal video clip for an explicitly permitted
   unresolved shot.
7. Create one editable Penpot or SVG/HTML design artifact and keep exact text/brand/product elements
   editable.
8. Produce narration and captions through an approved local or hosted path.
9. Assemble the production with Kinocut/FFmpeg.
10. Exercise at least one practical specialist handoff, such as Inkscape, GIMP/Krita, Audacity, or
    Kdenlive.
11. Run ffprobe, MediaInfo, relevant OpenCV/Tesseract checks, rights/content QA, cost reconciliation,
    and exact-hash approval.
12. Interrupt at least one managed operation and prove idempotent restart/reconciliation without a
    duplicate call, duplicate charge, or loss of provenance.
13. Archive the approved project and verify that a new OpenCode session can report its full state.

### Phase 1 tool coverage

| Capability | Primary tool | Practical conditional tools | Integration target |
|---|---|---|---|
| Operator and resume | OpenCode + Python CLI | Approved GPT or DeepSeek OpenCode model | Commands/skill plus controller contracts |
| Planning, research, scripts, and assisted QA | OpenAI GPT + DeepSeek provider profiles | Additional exact model profiles | Managed model adapters with structured contracts |
| Managed image/video generation | fal still, then fal video after animatic approval | Other exact approved media endpoints | Paid provider adapter with reservations and recovery |
| Video edit/render | Kinocut + FFmpeg | Kdenlive, Shotcut | Native adapter plus governed handoff |
| Image processing | ImageMagick | libvips | Native adapters |
| Layout and brand design | Penpot | Inkscape, Krita, GIMP | MCP plus governed handoffs |
| Browser/screen work | Playwright + Chromium | Manim for explainers | Native/headless adapters |
| Narration | Kokoro | Piper, approved hosted voice, Audacity | Native/managed adapters plus governed cleanup |
| Captions/transcription | whisper.cpp + FFmpeg | Human correction packet | Native adapter plus reviewed derivative |
| Technical QA | ffprobe + MediaInfo | OpenCV, Tesseract | Native deterministic checks |
| Sourcing | Owned assets + Openverse | Wikimedia, Freesound, Internet Archive, governed stock import | API/manual rights gate |

### Phase 1 exit gate

Production Phase 1 is complete only when:

- the OpenCode command and skill flow can start and resume the pilot without chat-memory dependence;
- schema v2 migration, deterministic recipe/rule evaluation, and slot policy tests pass;
- exact GPT and DeepSeek profiles pass bounded structured-output, privacy, budget, and recovery tests;
- the controller can select either model by policy without granting it mutation or spending authority;
- one fal still and one post-animatic fal video operation complete with reservations, usage/cost
  settlement, output hashes, and restart evidence;
- all Phase 1 tools report an explicit capability state rather than being assumed available;
- FFmpeg and Kinocut operations have receipts and a tested fallback path;
- specialist handoff and re-import work without modifying originals;
- one editable design source, narration/caption chain, timeline render, and QA report are linked to
  exact hashes;
- a rights manifest and any required attribution/disclosure output are complete;
- restart/reconciliation and deterministic fake-adapter tests pass;
- each supported Phase 1 specialist has a tool profile and validated handoff/re-import fixture, even
  though a single pilot does not need to invoke every specialist;
- `/pinaka/new` through `/pinaka/archive` completes on one real operational project using approved
  managed services and local production tools;
- the exact final candidate receives human approval;
- all real service spend reconciles within the approved project and per-operation limits;
- automatic publishing and production deployment did not occur.

A human checkpoint approves the Phase 1 evidence before any Production Phase 2 connection is enabled.

## Production Phase 2 â€” Advanced, resilient, and optimized studio

### Phase 2 outcome

Pinaka expands the operational Phase 1 system with rights-approved local generation, 3D, motion
graphics, compositing, character animation, advanced audio, deeper delivery QA, and policy-based
routing across multiple approved local and managed providers. Phase 2 improves resilience, privacy,
quality, and cost; it is not the first point at which Pinaka can complete a production.

### Phase 2 entry criteria

- Every Production Phase 1 exit criterion is PASS.
- Still-led animatic creation and approval work end to end.
- The target hardware has measured GPU/CPU memory, disk, and render throughput.
- At least one local model and workflow has complete licence, model-card, hash, and commercial-use
  review.
- The human owner approves the additional storage, compute, privacy, and maximum-cost envelopes.

### Phase 2 work packages

#### P2-A â€” Local generative production

Implement a governed **ComfyUI** adapter for one approved still workflow first:

- import a fixed workflow JSON and hash it;
- allowlist nodes, models, and output locations;
- record model, VAE, encoder, LoRA, ControlNet, seed, sampler, dimensions, and reference hashes;
- enforce attempts, time, memory, and generated-pixel limits;
- reconcile outputs after interruption;
- run identity, text, rights, and visual QA before candidate selection.

Only after still generation and animatic approval pass may a separately approved local video workflow
be added. Video requests are bounded per shot by model, seconds, dimensions, references, attempts, and
maximum resource/cost exposure.

**Ollama** or **llama.cpp** may be added for local planning or review when a measured privacy or
offline requirement exists. Their model licences and outputs remain independent of the serving tool.

#### P2-B â€” Advanced motion, 3D, VFX, and animation

Add recipe-selected support for:

- **Blender:** conditional headless adapter plus governed `.blend` handoff for 3D products, scenes,
  motion graphics, and compositing;
- **Natron:** governed project handoff or verified batch path for node-based VFX and compositing;
- **Synfig Studio:** governed vector-animation project and export path;
- **OpenToonz:** governed traditional-animation project and export path;
- **Manim:** extend the Phase 1 adapter for reusable branded diagram and data-animation templates;
- **Kdenlive:** advanced conform and final manual timeline review when automated assembly is
  insufficient.

The controller stores project-file derivatives and export receipts, but does not attempt to recreate
every GUI action. A source project alone is not a final artifact; its exported candidate still passes
normal hash, lineage, rights, and QA gates.

#### P2-C â€” Advanced audio and delivery QA

Add:

- **Ardour** governed handoffs for multitrack sessions, detailed mixing, and stem delivery;
- **QCTools** for selected signal-level investigations;
- **MediaConch** for delivery-policy conformance where a target format justifies it;
- expanded OpenCV/OCR checks, loudness targets, caption safe-area checks, and visual regression;
- music/effect source manifests and attribution output.

No open-source music-generation model is approved by category. Each exact model and weight requires
its own commercial-use and training-source review; rights-cleared stock or commissioned audio remains
the default.

#### P2-D â€” Multi-provider resilience and optimization

Extend the Phase 1 GPT, DeepSeek, fal, and optional hosted-service profiles only after measured need:

- add alternate exact models/endpoints for quality, region, latency, capacity, or cost resilience;
- benchmark representative approved fixtures without exposing production secrets;
- route by declared capability, data policy, quality tier, deadline, and maximum cost;
- use circuit-breaker and rate-limit state without silently switching to a provider that lacks input
  approval;
- preserve provider/model identity in every decision trace and output receipt;
- test outage, timeout, partial response, moderation refusal, unknown submission, and cost variance;
- keep credential values out of prompts, logs, SQLite, manifests, fixtures, and Git.

No dynamic "any available model" routing is permitted. Every fallback edge names an already approved
provider/model profile and the conditions under which the controller may select it.

#### P2-E â€” Multi-format reference pilots

Complete reference projects that exercise different practical paths:

1. branded still or carousel using Penpot plus raster/vector specialist work;
2. narrated explainer using Manim or HTML/SVG graphics, local TTS, captions, and FFmpeg;
3. captured tutorial using Playwright/Chromium capture and reproducibility QA;
4. fictional or animated piece using ComfyUI and an approved 2D/3D animation path;
5. product-led short using exact owned product assets, identity checks, and editable composition.

Not every pilot must use every tool. Together they must demonstrate the complete governed capability
matrix, including at least one specialist handoff, one local generation workflow, one advanced
motion/animation path, and one cross-provider resilience exercise.

### Phase 2 tool coverage

| Capability | Primary tool | Practical conditional tools | Integration target |
|---|---|---|---|
| Local image/video generation | ComfyUI | Ollama/llama.cpp for bounded local analysis | Governed local service |
| 3D and motion graphics | Blender | Manim | Headless adapter plus source-project handoff |
| VFX/compositing | Blender | Natron, GIMP/Krita | Headless or governed handoff |
| 2D animation | Synfig Studio | OpenToonz, Krita | Governed source-project handoff |
| Advanced timeline | Kinocut/FFmpeg | Kdenlive, Shotcut | Automated baseline plus human conform |
| Advanced audio | FFmpeg | Ardour, Audacity | Governed session handoff |
| Deep delivery QA | ffprobe/MediaInfo/OpenCV | QCTools, MediaConch, Tesseract | Automated reports plus targeted specialist checks |
| Managed model/media resilience | Phase 1 GPT, DeepSeek, and fal profiles | Additional exact approved providers/endpoints | Policy routing across governed adapters |

### Phase 2 exit gate

Production Phase 2 is complete only when:

- one approved local still-generation workflow is reproducible from recorded inputs and hashes;
- video generation, if enabled, is bounded by an approved animatic and per-shot limits;
- advanced source projects and their exports retain complete lineage;
- the five reference-project categories have complete QA and human dispositions, or an explicitly
  approved deferred record;
- rights, model, voice, font, codec, attribution, disclosure, and provider evidence are complete;
- local or managed-provider failover does not expose unapproved data, duplicate calls, or exceed
  budget;
- exact final candidates are approved by hash;
- publishing is still disabled unless it receives a separate future authorization.

## Practical use-case routing

| Practical case | Phase | Expected tools |
|---|---|---|
| Branded social still | 1 | Penpot, Inkscape or GIMP/Krita, ImageMagick, OCR |
| Still-led narrated short | 1 | Penpot/HTML/SVG, Kokoro/Piper, whisper.cpp, Kinocut, FFmpeg |
| Screen tutorial | 1 | Playwright, Chromium, FFmpeg, whisper.cpp, Tesseract |
| Manual timeline correction | 1 | Kdenlive or Shotcut, then controller re-import and QA |
| Educational diagram animation | 1 | Manim, Inkscape/Penpot, FFmpeg |
| High-volume image variants | 1 | libvips or ImageMagick |
| Managed planning, script, or assisted QA | 1 | Exact approved OpenAI GPT or DeepSeek profile |
| Generated campaign still | 1 | Approved fal still endpoint, Penpot/GIMP/Krita, identity and rights QA |
| Generated short clip | 1 | Approved animatic and fal video endpoint, followed by editable composition and QA |
| Private/offline generated campaign still | 2 | ComfyUI, Penpot/GIMP/Krita, identity and rights QA |
| 3D product scene | 2 | Blender, owned product assets, FFmpeg, OpenCV |
| VFX-heavy composite | 2 | Blender or Natron, GIMP/Krita, FFmpeg |
| Character/vector animation | 2 | Synfig or OpenToonz, Audacity/Ardour, FFmpeg |
| Delivery-constrained master | 2 | FFmpeg, MediaInfo, QCTools/MediaConch |
| Provider outage or quality failure | 2 | Explicitly approved alternate GPT, DeepSeek, fal, local, or other service profile |

## Requirements and evidence ledger

| ID | Requirement | Completion evidence |
|---|---|---|
| TP-01 | Exactly two production phases | This roadmap and phase-gate reports |
| TP-02 | Purpose-neutral planning governs tool selection | Migration, recipe, rule, slot-policy, budget, and deadline tests |
| TP-03 | Practical specialist tools are supported | Tool profiles, work-packet fixtures, and re-import receipts |
| TP-04 | Agents cannot bypass the controller | Contract and negative authorization tests |
| TP-05 | Approved managed services are first-class but never blanket-authorized | Provider profiles, decision traces, privacy checks, and budget tests |
| TP-06 | Every operation preserves provenance | Tool receipts, hashes, lineage, and event records |
| TP-07 | Rights are evaluated per asset/model/voice/font | Normalized rights manifests and reviewer dispositions |
| TP-08 | Phase 1 is operational with GPT, DeepSeek, fal, and local tools | Completed real pilot, service receipts, final archive, and reconciled budget ledger |
| TP-09 | Every image/video generation path is bounded | Workflow/endpoint allowlists, animatic approval, and limit tests |
| TP-10 | Failures resume safely | Crash, timeout, retry, and `submission_unknown` evidence |
| TP-11 | Publishing remains separately authorized | Negative publishing test and deferred register |

## Explicitly outside both phases

- a separate Pinaka web UI or public HTTP API;
- unrestricted scraping or stock-site harvesting;
- direct agent writes to SQLite or protected artifact paths;
- automation of a personal ChatGPT browser session or wildcard provider/model routing;
- unreviewed model, voice, font, music, or codec use;
- automatic social publishing;
- distributed workers, queues, or vector databases without measured need;
- production deployment without a separate human-authorized release gate.

## Implementation status (10 November 2026)

The governance layer for both phases is implemented locally and covered by tests:

| Layer | Module | Status |
|---|---|---|
| Creative rules and recipes | `creative_rules.py`, `recipes.py` | CR-01â€“CR-15, 11 versioned recipes, slot derivation, decision traces |
| Rights-aware sourcing | `licensing.py`, `openverse.py`, `wikimedia.py`, `internet_archive.py`, `freesound.py` | Metadata-only search, per-item rights outcomes, origin verification required |
| Font licensing | `fonts.py` | Exact-hash inventory, metadata extraction, embedding/redistribution gate |
| Provider routing | `routing.py` | Role, data classification, cost ceiling, quality/cost/priority policies, bounded fallbacks |
| External spend | `external_spend.py` | Quote, reservation, receipt, refund, orphan and overage reconciliation, envelope authorization |
| Image generation | `adapters/google_image.py`, `adapters/google_vertex_image.py` | Free-tier API-key Nano Banana adapter; ADC Vertex adapter with per-call cost cap and trial-credit accounting |
| Analytics | `analytics.py` | Experiment design, Wilson intervals, two-proportion z-test, Bonferroni adjustment, decision report, CSV ingestion |
| Publication | `publication.py` | Channel profiles, exact-hash authorization, captions, disclosure and metadata preflight; manual-upload dispatch package; never publishes |
| Reliability | `backup.py`, `retention.py` | Hashed backup, integrity verification, restore drill, verified offsite mirror, non-destructive retention plan |
| Localization | `localization.py` | SRT parsing, CPS/line-length/overlap audit, locale plans and timing review |
| Local toolchain | winget + pip + portable bundles | FFmpeg 9.0.2, Blender 5.2.3, Inkscape 1.4.4, GIMP 3.2.6, Krita 5.3.4.1, Audacity 4.0.2, Natron 2.5.0, Synfig 1.5.3, Manim 0.19.1, QCTools 1.4.1, MediaConch 25.04 |
| Local generation | ComfyUI + CPU PyTorch, `adapters/comfyui.py` | Installed; controller adapter, localhost guard, checkpoint allowlist and 256x256 smoke test pass |

Remaining work is execution rather than governance: deeper adapters for Blender, Natron and Manim,
exact paid-provider routes and receipt imports, platform API publication adapters, a content
calendar, retention enforcement, dubbing, and a captioned v2 master. See
`production-flow-and-capability-matrix.md` for the row-level status.

## Related documents

- `open-source-production-toolchain.md` â€” tool roles, licences, and current capability status.
- `architecture.md` â€” controller boundary and state ownership.
- `asset-sourcing-policy.md` â€” source and rights eligibility.
- `next-gate.md` â€” bounded connection-spike evidence.
- `creative-classification-and-production-guide.md` â€” recipes, rules, approvals, and QA.
- `implementation-report.md` â€” verified foundation baseline and deferred work.
