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
- No real generation, stock, Penpot, upload, or publishing adapter is enabled yet.

## Install on Windows

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install --upgrade pip
.\.venv\Scripts\python -m pip install -e ".[dev]"
```

## Verify

```powershell
.\.venv\Scripts\python -m pytest
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
.\.venv\Scripts\python -m pinaka --home .\workspace list-projects
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

Vertex Nano Banana uses the existing Google Cloud ADC login instead of an API key:

```powershell
.\.venv\Scripts\python -m pinaka --home .\workspace register-tool-profile --file .\config\tools\google-vertex-nano-banana.json
.\.venv\Scripts\python -m pinaka --home .\workspace generate-vertex-image <project-id> --prompt "..." --model gemini-nano-banana-2.1 --aspect-ratio 16:9 --max-cost-cents 5
```

This route draws on project `spheric-host-505921-t5` at `global` and its finite Google Cloud trial
credit, **not** the AI Studio free tier. List prices put a 1K Nano Banana 2.1 image at roughly
**$0.034** and a 1K Gemini 3.1 Flash Image at roughly **$0.067**. The adapter requires an explicit
per-call ceiling and never uses an API key. If ADC expires, run `gcloud auth application-default login`.
A verified controller sample produced a 1376×768 image for **4 cents of trial credit**; the spend is
recorded as an `external_spend` artifact in the project.

## Runtime layout

Runtime data defaults to `workspace/` and is excluded from Git:

```text
workspace/
├── state/pinaka.sqlite
└── projects/<project-id>/
    ├── inputs/                 ├── references/
    ├── assets/originals/       ├── assets/derived/
    ├── assets/generated/       ├── designs/
    ├── audio/                  ├── previews/
    ├── renders/                ├── qa/
    └── archive/
```

## Current scope and next gate

Implemented: the Phase 1 controller and production pipeline, the Phase 2 governance layer, and the
Phase 3 governed-Studio foundation — creative rules, recipes and slots, rights-aware sourcing,
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

Gemini runs through **Vertex AI with Application Default Credentials** (no API keys): image
generation, narration and transcription, with a free model-availability check. fal work is priced per
model through `pinaka.pricing`, so a creative can be planned against a small dollar target before any
paid call. Editable HyperFrames-style compositions are authored from the shot plan with no provider at all.
Generated motion comes only from Studio's `fal.video` route, one shot at a time and keyframe-first;
Veo and Runway are excluded and refused by the controller.

The archived 10-second pilot remains the verified production baseline.

Local tools now include FFmpeg, ImageMagick, Playwright/Chromium, Blender, Inkscape, GIMP, Krita,
Audacity, Natron, Synfig, OpenToonz, Manim, QCTools CLI and MediaConch CLI. ComfyUI v0.44.0,
QCTools/MediaConch and A/B analytics are controller-integrated; ComfyUI uses CPU PyTorch and an
SD1.5 checkpoint, and a controller-owned 256x256 smoke test completed in 25.5 seconds at zero spend.
Nano Banana is implemented as a free-tier-only route and requires `GEMINI_API_KEY`; no paid provider
is enabled.

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
