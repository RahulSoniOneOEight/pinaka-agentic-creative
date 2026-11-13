# Pinaka Agentic Creative Production

Controller-first foundation for the creative-production system described in
`Pinaka_Agentic_Creative_Production_Specification.txt`.

This milestone implements the **enforcement layer**, not autonomous production. It provides
validated contracts, SQLite-owned state, human approvals, dependency invalidation, transactional
budget reservations, recoverable provider-job states, immutable original handling, artifact
lineage, capability probes, an audit stream, and a deterministic mock provider.

## Safety boundary

- Agents may propose; `CreativeController` decides and commits.
- Provider work cannot be reserved beyond the project budget.
- Jobs requiring an approval cannot run without an active approval.
- Reused idempotency keys return the original job; changed requests are rejected.
- A changed fact set invalidates only dependent approvals and artifacts.
- Originals are imported content-addressably and never overwritten by controller APIs.
- Secrets are rejected from persisted request/response fields.
- Paid generation exists only through a funded budget item; publication remains a separate human act.

## Install on Windows

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install --upgrade pip
.\.venv\Scripts\python -m pip install -e ".[dev]"
```

## Verify

```powershell
.\.venv\Scripts\python -m pytest
.\.venv\Scripts\python -m mypy
.\.venv\Scripts\ruff check src tests
.\.venv\Scripts\ruff format --check src tests
.\.venv\Scripts\python -m compileall -q src tests
```

## Run the no-network vertical slice

```powershell
.\.venv\Scripts\python -m pinaka --home .\workspace demo --name "Foundation Demo"
```

The command creates a project, records a brief approval and fact set, reserves a simulated cost,
runs the deterministic mock adapter, stores a generated test artifact, settles the budget, and
prints the resulting state. It makes no external or paid call.

Useful commands:

```powershell
.\.venv\Scripts\python -m pinaka --home .\workspace capabilities
.\.venv\Scripts\python -m pinaka --home .\workspace fal-check
.\.venv\Scripts\python -m pinaka --home .\workspace list-projects
.\.venv\Scripts\python -m pinaka --home .\workspace operations
.\.venv\Scripts\python -m pinaka --home .\workspace draft-metadata <project-id> --channel youtube_shorts --output .\workspace\metadata.json
.\.venv\Scripts\python -m pinaka --home .\workspace grade-asset <project-id> <artifact-id> --lut .\assets\look.cube
.\.venv\Scripts\python -m pinaka --home .\workspace key-asset <project-id> --background <id> --foreground <id>
.\.venv\Scripts\python -m pinaka --home .\workspace change-speed <project-id> <artifact-id> --speed 0.5
.\.venv\Scripts\python -m pinaka --home .\workspace detect-beats --audio .\assets\track.mp3 --output .\workspace\beats.json
.\.venv\Scripts\python -m pinaka --home .\workspace assemble-film <project-id> --beat-grid .\workspace\beats.json
.\.venv\Scripts\python -m pinaka --home .\workspace avatar-generate <project-id> --source <portrait> --audio <narration> --plan <plan> --item <item> --consent "<release>"
.\.venv\Scripts\python -m pinaka --home .\workspace events
.\.venv\Scripts\python -m pinaka --home .\workspace evaluate-profile --file .\config\pilots\pinaka-virtual-cfo-profile.json
.\.venv\Scripts\python -m pinaka --home .\workspace creative-plan --file .\config\pilots\pinaka-virtual-cfo-profile.json
.\.venv\Scripts\python -m pinaka --home .\workspace source-search --provider openverse --query "business finance"
.\.venv\Scripts\python -m pinaka --home .\workspace font-inventory --root .\workspace\fonts
.\.venv\Scripts\python -m pinaka --home .\workspace experiment-report --file .\config\tests\experiment-example.json --metrics-csv .\config\tests\experiment-metrics-example.csv
.\.venv\Scripts\python -m pinaka --home .\workspace preflight <project-id>
.\.venv\Scripts\python -m pinaka --home .\workspace external-qa --video <path> --output-dir .\workspace\qa
.\.venv\Scripts\python -m pinaka --home .\workspace publication-package <project-id> --file <publication-json> --output-dir .\workspace\dispatch
.\.venv\Scripts\python -m pinaka --home .\workspace mirror-backup --backup .\workspace\backups\run-a --destination <offsite-dir>
.\.venv\Scripts\python -m pinaka --home .\workspace backup --output .\workspace\backups\run-a
.\.venv\Scripts\python -m pinaka --home .\workspace restore-drill --backup .\workspace\backups\run-a
```

Publication is never performed by the CLI. `publication-check` evaluates a plan, and
`publication-package` writes a hashed manual-upload folder; both remain blocked until a separate,
exact-hash publication authorization exists.

Nano Banana is available as a zero-cost, free-tier-only image route:

```powershell
.\.venv\Scripts\python -m pinaka --home .\workspace register-tool-profile --file .\config\tools\google-nano-banana-image.json
.\.venv\Scripts\python -m pinaka --home .\workspace register-provider-profile --file .\config\providers\google-nano-banana-image.json
.\.venv\Scripts\python -m pinaka --home .\workspace generate-image <project-id> --prompt "..." --aspect-ratio 9:16
```

It requires `GEMINI_API_KEY` or `D:\pinaka-credentials\secrets\gemini-api-key` and
refuses paid billing. The key is read from the environment or that file only; never write it into a
prompt, config, SQLite record, log or Git. Before the first call, confirm in Google AI Studio that
the account is on a free-tier/quota-limited project with billing disabled; the adapter cannot verify
Google's billing state for you. The default model is `gemini-2.5-flash-image`; pass `--model` to
select another image model.

Local ComfyUI generation is also exposed through the controller:

```powershell
.\.venv\Scripts\python -m pinaka --home .\workspace register-tool-profile --file .\config\tools\comfyui-local-image.json
.\.venv\Scripts\python -m pinaka --home .\workspace generate-local-image <project-id> --prompt "..." --width 512 --height 512 --steps 12
```

The adapter only connects to loopback ComfyUI, allows only the configured checkpoint and a fixed
text-to-image workflow, and refuses any non-zero cost.

Vertex Nano Banana and the ADC image route are **retired**. Their configuration files are kept
under `config/retired/` for history only; no CLI verb registers or runs them. `docs/vertex-adc.md`
and `docs/nano-banana.md` describe the historic experiments and why the route was removed.

## Runtime layout

Runtime data defaults to `workspace/` and is excluded from Git:

```text
workspace/
â”œâ”€â”€ state/pinaka.sqlite
â””â”€â”€ projects/<project-id>/
    â”œâ”€â”€ inputs/                 â”œâ”€â”€ references/
    â”œâ”€â”€ assets/originals/       â”œâ”€â”€ assets/derived/
    â”œâ”€â”€ assets/generated/       â”œâ”€â”€ designs/
    â”œâ”€â”€ audio/                  â”œâ”€â”€ previews/
    â”œâ”€â”€ renders/                â”œâ”€â”€ qa/
    â””â”€â”€ archive/
```

## Current scope and next gate

Implemented: the Phase 1 controller and production pipeline, the Phase 2 governance layer, and the
Phase 3 governed-Studio foundation â€” creative rules, recipes and slots, rights-aware sourcing,
provider routing, external-spend reconciliation, publication preflight, font licensing, localisation
planning and A/B analytics.

The governed-Studio foundation adds:

- a deterministic raw-text planner that derives a workable brief, shots and scene plan;
- versioned reusable elements with shot bindings and continuity invalidation;
- per-creative budget plans with a 50% variance tolerance and an approval loop for overruns;
- a USD-or-INR per-run Studio cap with human approval above the automatic limit;
- per-asset generation cost recorded against the slot, artifact and provider;
- local-first routing and primary/backup coverage gates before paid work;
- session-scoped Studio readiness checks; and
- verified-hash final approval that requires passing QA.

Hosted stills and edits use **fal**, behind funded plan items; narration and transcription stay
local (Kokoro / faster-whisper). Vertex/ADC entry points are retired. Script proposals can be supplied
by an agent or operator without an implicit hosted call. `pinaka operations` describes the supported
routes and funding policy; it does not claim live provider readiness or balances.
Captions are plan-timed by default; `transcribe-audio --srt` builds speech-timed cues from local ASR,
and both paths apply the locale line/character rules with a `caption-drift` comparison. Finishing
includes LUT/EQ grade, green-screen key/composite and pitch-correct speed change. Music-led edits can
cut on detected beats; avatar/lip-sync is implemented against fal's published schemas behind a
likeness-consent gate, with no live call yet.
Editable HyperFrames-style compositions are authored from the shot plan with no provider at all.
Generated motion comes only from Studio's `fal.video` route, one shot at a time and keyframe-first;
Veo and Runway are excluded and refused by the controller.

Implementation is organized by feature in `use_cases/`, aggregate in `store/`, and request/record
type in `contracts/`, with compatible controller/repository/model imports. See
[architecture](docs/architecture.md), [audio layers](docs/audio-layers.md), and
[the delivery ledger](docs/implementation/remaining-items.md).

## Production policy gates

The controller now enforces the gap-register policies in code, not only in documentation.

- **Brief as contract**: `plan-from-text` freezes the workable brief, shots and slots into a hashed
  production contract. Paid work is refused if the brief changes afterwards.
- **Generation last**: `add-slot`, `record-sourcing-decision` and `record-sourcing-candidate` record
  the owned/stock/manual/deterministic/generated cascade. A paid `image.generate`,
  `video.generate` or `avatar.generate` item needs a `slot_id`, an approved generated source and a
  decision with providers, candidates considered and a rejection reason.
- **Two-pick**: `pick-slot-assets` requires an eligible primary and an independent backup. Final
  approval refuses a visual slot without both.
- **Motion**: `record-motion-level` records M0-M5 per shot; character shots must reach M1 before
  animatic approval.
- **Flow ledger**: `record-flow-step` and `flow-log` keep the append-only G0-G7 record. Skipped and
  not-applicable steps need a reason and approver; blocked steps need a next action.
- **A/B**: `declare-ab`, `bind-ab-variant` and `ab-status` enforce a declared experiment with two
  distinct hashed renders.
- **Effects and sound**: `record-effects-plan` validates camera, transition, light, particle and SFX
  cues, their rights and the kids flash-rate limit.
- **Delivery**: `production-record` exports hashed JSON and Markdown; `change-request` writes
  `CHANGE-REQUEST.md` and YAML for every element, slot and artifact hash; `quality-bar` checks all
  gates, not only technical QA.

`quality-bar` is the release gate: it reports each gate as pass/fail and lists the failures. A
technical QA pass alone does not pass the bar.

### Verified policy run

`workspace/rhyme-flow-qc` is one real 12-second rhyme production under these gates:

- project `prj_75f91f43382d4b05bb3d501935d45de4`, archived, USD 1.00 cap, **USD 0.00 spent**;
- 20 sourced candidates, three slots, a primary and backup bound for each;
- 1080x1920 H.264/AAC output with local Kokoro narration and burned ASR captions;
- A/B variants (ungraded and graded), production record and change-request packet;
- `quality-bar` passed 10/10 gates and `flow-log` completed G0-G7;
- motion is **M0** by the doc's definition (camera push/pan on stills, no character pixels moving); it was recorded as M1 by mistake, so the motion policy is **not met**.

The run found and fixed two gaps: full-bleed composition for `primary visual` layers and a
change-request join that now shows bound slot artifacts and credits. A slot-coverage gate at final
approval and a Pexels evidence fallback remain open.

The archived 10-second pilot remains the verified production baseline.

Local tools now include FFmpeg, ImageMagick, Playwright/Chromium, Blender, Inkscape, GIMP, Krita,
Audacity, Natron, Synfig, OpenToonz, Manim, QCTools CLI and MediaConch CLI. ComfyUI v0.44.0,
QCTools/MediaConch and A/B analytics are controller-integrated; ComfyUI uses CPU PyTorch and an
SD1.5 checkpoint, and a controller-owned 256x256 smoke test completed in 25.5 seconds at zero spend.
Hosted stills and edits use fal behind funded plan items; the optional free-tier Gemini route still
requires `GEMINI_API_KEY` and refuses paid billing. Check the fal credential without spending
anything with `pinaka fal-check`, which reports `authenticated`, the key source and its shape.

See `docs/implementation-report.md` for verified evidence, capability findings, deferred work, and
residual risks. See `docs/openmontage-controller-integration.md` for the Studio work-packet boundary,
`docs/studio-gateway.md` for the live production gateway, `docs/creative-budget-planning.md` for
budget plans and variance approval, `docs/raw-text-planner.md` for text intake,
`docs/studio-session-readiness.md` for the per-session Studio check, and `docs/vertex-adc.md` for
Gemini on Vertex with ADC and the fal cost model. See `docs/shot-motion.md` for budgeted per-shot
Studio video, `docs/hyperframes-composition.md` for the editable composition format, and
`docs/reference-roles.md` for governed shot references, and
`docs/publication-handoff.md` for recording publication authority and building a manual-upload
package. See `docs/open-source-production-toolchain.md` for the open-source-first hybrid
production tools and managed services, their roles, licensing boundaries, and current integration
status. See `docs/two-phase-production-implementation-plan.md` for the two-phase delivery roadmap.
See `docs/nano-banana.md` for the exact ADC/API-key image routes, models, pricing, commands and
troubleshooting.
