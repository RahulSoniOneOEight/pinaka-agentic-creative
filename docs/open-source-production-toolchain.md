# Open-source production toolchain and roles

- **Status:** architectural direction; production integrations remain gated
- **Updated:** 9 November 2026

This note defines the preferred tools for Pinaka's creative-production pipeline. The default is
open-source-first where practical, hybrid local/managed when quality or delivery requires it,
deterministic where possible, and rights-aware. A tool or service appearing in this document is not
automatically authorized for a job: its adapter and a bounded connection spike must still pass the
controller, privacy, rights, budget, restart, and quality gates in `next-gate.md`.

## Operating boundary

OpenCode is the operator interface. Pinaka does not need a separate product web UI or public HTTP
API for the planned local workflow.

```text
natural-language prompt or /pinaka/* command
                    |
                    v
                 OpenCode
                    |
                    v
          validated proposal contract
                    |
                    v
            CreativeController  <---- human approvals
              |     |     |
              |     |     +---- source/design/generation adapters
              |     +---------- media operations and QA
              +---------------- SQLite, budgets, artifacts and events
```

OpenCode and agents may propose work. Only the Python controller may persist workflow state, import
an asset, reserve money, authorize a provider operation, register an artifact, or advance a project.
Tools must not bypass this boundary by writing directly to SQLite or production artifact paths.

## Selection principles

1. Prefer owned, approved inputs and deterministic local processing.
2. Prefer an open-source local tool when it satisfies the required quality, privacy, cost, and
   deadline; permit an approved managed service when policy selects it.
3. Use official provider APIs or governed manual import; do not scrape or bulk-harvest stock sites.
4. Keep external systems behind small controller adapters with deterministic fakes in tests.
5. Preserve originals and write every derivative to a new path with hashes and lineage.
6. Record tool, build, model, workflow, font, voice, asset, and output rights separately.
7. Treat managed cloud services as first-class but explicitly allowlisted and budgeted operations,
   never as unmetered defaults.
8. Do not add a queue, distributed worker, vector database, web UI, or public API until measured need
   justifies it.

## Recommended core stack

| Tool | Pinaka role | Execution | Licence summary | Adoption |
|---|---|---|---|---|
| **OpenCode** | Operator interface, reusable `/pinaka/*` commands, skills, permissions, and MCP tools | Local interface; its selected model may be local or hosted | MIT; model terms are separate | Required operator |
| **Python** | Controller, policy enforcement, contracts, adapters, and CLI | Local | PSF | Required; implemented |
| **Pydantic** | Strict proposal, project, rights, and job validation | Local | MIT | Required; implemented |
| **SQLite** | Projects, approvals, reservations, jobs, budgets, and event history | Local | Public domain | Required; implemented |
| **Git** | Versioned code, recipes, policies, and documentation | Local | GPL-2.0 | Required; implemented |
| **FFmpeg** | Decode, encode, cut, overlay, composite, mix audio, render captions, and export | Local | LGPL-2.1+ by default; GPL applies to builds using GPL components | Required media engine |
| **ffprobe** | Read streams, codecs, duration, dimensions, frame rate, and media metadata | Local | Same build and licence family as FFmpeg | Required QA probe |
| **Kinocut** (`kino`) | Typed agent-facing video operations, preflight checks, receipts, and QA checkpoints over FFmpeg | Local | Apache-2.0 | Preferred video adapter |
| **ImageMagick** | Resize, crop, mask, compose, convert, and inspect still images | Local | ImageMagick Licence, an Apache-style open-source licence | Preferred initial image engine |
| **libvips** | Memory-efficient high-volume image transformation | Local | LGPL-2.1+ | Optional performance path |
| **MediaInfo** | Independent media metadata and conformance inspection | Local | BSD-2-Clause | Recommended second QA probe |

### Kinocut and FFmpeg

Kinocut is the preferred typed surface for agent-requested video editing. FFmpeg remains the
deterministic execution engine and direct fallback.

```text
controller-authorized operation
          -> Kinocut preflight and typed command
          -> FFmpeg execution
          -> ffprobe / MediaInfo verification
          -> artifact hash, lineage, receipt and event
```

The current published Kinocut runtime requires Python 3.11 or later, while the recorded Pinaka
baseline uses Python 3.10.11. Initially run Kinocut in a separate Python 3.11 environment or sidecar
process. Upgrade the controller runtime only as a reviewed dependency change.

## Design, graphics, and browser rendering

| Tool | Pinaka role | Execution | Licence summary | Adoption |
|---|---|---|---|---|
| **Penpot** | Editable layouts, design tokens, brand templates, diagrams, and reusable components | Self-hosted or hosted; controlled through a scoped MCP bridge | MPL-2.0 | Use when an editable design source is required |
| **Playwright** | Deterministic browser automation, screenshots, HTML rendering, and source verification | Local | Apache-2.0 | Recommended |
| **Chromium** | Headless engine for HTML/CSS layouts, browser captures, and visual comparison | Local, preferably the Playwright-pinned build | BSD-style core plus third-party notices | Recommended with Playwright |
| **Inkscape** | SVG and vector illustration editing | Local | GPL-2.0+ | Optional specialist |
| **Krita** | Raster illustration and painting | Local | GPL-3.0 family | Optional specialist |
| **GIMP** | Photo manipulation and manual image repair | Local | GPL family | Optional specialist |
| **Manim** | Programmatic diagrams and educational animation | Local | MIT | Optional explainer tool |

Penpot is not a replacement operator UI. OpenCode remains the interface for Pinaka; Penpot is an
editable design engine and human review surface. A Penpot adapter must make narrowly scoped edits,
export the selected artifact, and verify reconstruction and fonts before registering the output.

Playwright and Chromium are appropriate for approved source verification, screenshots, visual
regression, and deterministic HTML/CSS title-card rendering. They are not authorization to scrape a
site, bypass access controls, or ignore provider terms.

## Local generation

| Tool | Pinaka role | Execution | Licence summary | Adoption |
|---|---|---|---|---|
| **ComfyUI** | Reproducible node workflows for image, video, audio, and control-model inference | Local GPU or CPU | GPL-3.0; models and custom assets have separate terms | Preferred local generation backend |
| **Ollama** | Optional local language or vision model serving for planning and review | Local | MIT; model terms are separate | Optional |
| **llama.cpp** | Lightweight local model inference where a smaller runtime is preferable | Local | MIT; model terms are separate | Optional alternative |

ComfyUI is an execution framework, not a rights grant. Each checkpoint, text encoder, VAE, LoRA,
ControlNet, custom node, and reference input must be reviewed independently. Many products described
as open-source AI are only open-weight and may restrict commercial use.

The local CPU installation uses ComfyUI v0.44.0 with CPU PyTorch and the SD1.5
`v1-5-pruned-emaonly-fp16.safetensors` checkpoint. SD1.5 is released under the CreativeML Open
RAIL-M licence, which permits commercial use subject to its use restrictions and requires the
licence to travel with the model. CPU inference on the Intel UHD 630 is suitable for still tests,
not high-volume production.

Every generation manifest must record at least:

- workflow JSON and hash;
- tool and custom-node versions;
- model identifiers, source URLs, hashes, and licences;
- seed, sampler, dimensions, duration, and other material parameters;
- input and reference artifact hashes;
- elapsed time, hardware class, attempts, and cost;
- output hash, parents, operation, and rights disposition.

## Speech, transcription, captions, and audio

| Tool | Pinaka role | Execution | Licence summary | Adoption |
|---|---|---|---|---|
| **Kokoro** | Local narration synthesis | Local | Official code and weights are Apache-2.0; runtime dependencies and voices still require review | Preferred TTS candidate |
| **Piper / piper1-gpl** | Fast, lightweight local narration | Local | Active implementation GPL-3.0; voice licences vary | Performance fallback |
| **whisper.cpp** | Local transcription, subtitle timing, and narration-versus-script checks | Local | MIT | Recommended |
| **FFmpeg audio filters** | Resampling, mixing, fades, normalization, loudness analysis, and muxing | Local | FFmpeg build licence | Default automated audio path |
| **Audacity** | Manual cleanup and audio review | Local | GPL-3.0 | Optional human tool |
| **Ardour** | Advanced manual audio editing and mixing | Local | GPL-2.0+ | Optional specialist |

An apparently open TTS engine does not settle voice rights. The selected voice needs its own licence,
training-source review where documented, attribution record, commercial-use decision, and quality
check. Voice cloning and impersonation are disabled by default and require identity, consent, and use
approval.

## Optional specialist production tools

These tools are selected by a recipe only when the core stack cannot satisfy the brief. They are not
default controller dependencies.

| Tool | Best use | Licence summary | Pinaka position |
|---|---|---|---|
| **Kdenlive** | Human timeline correction and manual finishing | GPL-3.0+ | Manual review or rescue tool |
| **Blender** | 3D scenes, motion graphics, compositing, and headless rendering | GPL-3.0+ | Specialist adapter when required |
| **Natron** | Node-based visual compositing | GPL-2.0 | Optional VFX tool |
| **Synfig Studio** | Vector-based 2D animation | GPL-3.0 | Optional animation tool |
| **OpenToonz** | Traditional 2D animation | BSD-3-Clause | Optional animation tool |
| **Shotcut** | Alternative manual video editor | GPL-3.0 | Alternative to Kdenlive |
| **MoviePy** | Python-level video scripting | MIT | Use only for a measured gap in Kinocut/FFmpeg |

For ordinary shorts, explainers, product work, and still-led videos, keep Kinocut plus FFmpeg as the
default. Specialist tools must not silently create detached, untracked production paths.

## Automated QA tools

| Tool | QA role | Licence summary | Priority |
|---|---|---|---|
| **ffprobe** | Streams, codecs, duration, dimensions, frame rate, and container checks | FFmpeg build licence | Core |
| **MediaInfo** | Independent metadata verification | BSD-2-Clause | Core |
| **whisper.cpp** | Narration transcription and approved-script comparison | MIT | Core for voiced work |
| **OpenCV** | Black/frozen frames, blur, similarity, boundaries, and visual measurements | Apache-2.0 | Add for first real video pilot |
| **Tesseract OCR** | Confirm rendered labels, disclaimers, and captions | Apache-2.0 | Add for text-heavy work |
| **QCTools** | Detailed audiovisual signal inspection | GPL-3.0 | Optional specialist |
| **MediaConch** | Policy-driven media format conformance | BSD-2-Clause | Optional delivery gate |

Automated checks attach to the exact candidate hash. They supplement, rather than replace, human
review of product identity, factual meaning, continuity, accessibility, rights, and brand quality.

## Rights-aware sourcing

The sourcing order is:

```text
owned approved assets
    -> official API candidates
    -> governed manual imports
    -> deterministic composition
    -> approved local or managed generation selected by policy
```

| Source or tool | Media | Pinaka handling |
|---|---|---|
| **Owned asset library** | Any | First choice; preserve ownership, releases, hashes, and lineage |
| **Openverse API** | Images and audio | Preferred open-content discovery; verify the exact origin item before import |
| **Wikimedia Commons** | Images, video, and audio | Verify per-file licence, creator, attribution, and source evidence |
| **Freesound** | Audio and effects | Evaluate the Creative Commons licence of each file |
| **Internet Archive** | Mixed media | Treat rights as item-specific; never infer public-domain status from hosting |
| **Google Fonts / Fontsource** | Fonts | Preserve the exact font file and its OFL, Apache, or other licence |
| **Pexels** | Photos and video | Proprietary service terms; use only with an existing approved API key |
| **Unsplash** | Photos | Proprietary API and licence terms; preserve required events and attribution |
| **Mixkit / Videezy** | Stock media | Governed manual import only; no automated harvesting is authorized |

An open-source platform, API client, or search index does not make every item it exposes unrestricted.
The normalized rights contract and eligibility result in `asset-sourcing-policy.md` remain mandatory.

## Managed model and media services

Managed services are permitted in Production Phase 1 and can be selected directly when their approved
quality, privacy, cost, or deadline profile is better for the slot. They remain controller-governed;
availability is not blanket permission to transmit arbitrary project data or incur unbounded spend.

| Service | Pinaka role | Phase 1 target | Important boundary |
|---|---|---|---|
| **OpenAI GPT models** | Planning, research synthesis, scripts, structured proposals, multimodal review, and QA assistance | One allowlisted text/reasoning model and, when needed, one allowlisted vision-capable model | Use an approved OpenAI API/OpenCode provider model; do not automate the consumer ChatGPT website |
| **DeepSeek models** | Reasoning, planning, structured alternatives, script support, and cost/quality fallback or comparison | One exact hosted or approved self-hosted model profile | Hosted-service terms and open-weight licences differ; record the exact endpoint and model |
| **fal** | Hosted image and video generation | One still endpoint first, then one video endpoint after still and animatic gates pass | Paid hosted platform; SDK source availability does not grant model or output rights |
| **Other approved services** | Hosted voice, transcription, search, generation, or specialist processing where selected | Added one provider/model profile at a time | No wildcard provider, endpoint, or credential authorization |

The requested "ChatGPT model" capability is implemented through an approved GPT model exposed by an
OpenAI API or OpenCode provider. A personal ChatGPT browser session is not a production adapter and
does not provide controller-visible budgets, idempotency, or recovery.

Before any managed model or media job, record and approve:

- provider, exact endpoint, model/version, and operation;
- intended role and the hashes of the validated input contract;
- data classification, redaction, privacy, retention, and deletion policy;
- model, generated-output, and commercial-use terms;
- maximum tokens, attempts, dimensions, seconds, and cost as applicable;
- required approvals and permitted references;
- idempotency, timeout, retry, and `submission_unknown` recovery behavior;
- response, usage, actual-cost, and output hashes without secret values.

For fal, still generation must pass first. A video endpoint may then be enabled inside Production
Phase 1 only after an animatic identifies the exact unresolved shot and a human approves its model,
references, seconds, attempts, and maximum cost. Approval of one service does not authorize a
different provider, model, endpoint, or media type.

## Two-phase delivery model

Implementation is organized into exactly two production phases:

1. **Operational hybrid studio:** OpenCode workflow, OpenAI GPT and DeepSeek model adapters, fal
   still/video generation behind staged gates, deterministic media operations, editable design,
   sourcing, narration, captions, QA, and practical specialist handoffs.
2. **Advanced, resilient, and optimized studio:** rights-approved local ComfyUI workflows, Blender,
   Natron, Synfig, OpenToonz, Ardour, deep delivery QA, and policy-based routing across additional
   approved local and managed providers.

Specialist tools are practical conditional capabilities, not ignored extras. Stable CLIs and APIs use
native adapters; GUI applications use governed work packets and controller-verified re-import unless
a reliable headless interface justifies automation. See `two-phase-production-implementation-plan.md`
for work packages, dependencies, use-case routing, and exit gates.

## Current integration status

The capability results below are the recorded foundation baseline, not a permanent claim about the
machine. Re-run capability probes before each connection spike.

### Installed local toolchain (10 November 2026)

| Tool | Version | Install path / source | Status |
|---|---|---|---|
| FFmpeg / ffprobe | 9.0.2 | winget `Gyan.FFmpeg` | Installed; PATH updated |
| Blender | 5.2.3 | winget `BlenderFoundation.Blender` | Installed |
| Inkscape | 1.4.4 | winget `Inkscape.Inkscape` | Installed |
| GIMP | 3.2.6 | winget `GIMP.GIMP` | Installed |
| Krita | 5.3.4.1 | winget `KDE.Krita` | Installed |
| ImageMagick | 7.1.2-34 | winget `ImageMagick.ImageMagick` | Installed |
| Playwright + Chromium | 1.65.0 | pip + `playwright install chromium` | Installed; headless shell downloaded |
| Manim Community | 0.19.1 | pip `manim` | Installed; rendered a test MP4 |
| QCTools CLI | 1.4.1 | MediaArea portable | Installed at `D:\pinaka-tools\qcli` |
| MediaConch CLI | 25.04 | MediaArea portable | Installed at `D:\pinaka-tools\mediaconch` |
| Audacity | 4.0.2 | MSI per-user / `%ProgramFiles%\Audacity 4` | Installed |
| Natron | 2.5.0 | MediaArea/GitHub portable zip | Installed at `D:\pinaka-tools\natron` |
| Synfig Studio | 1.5.3 | winget `Synfig.SynfigStudio` | Installed |
| OpenToonz | 1.8.0 | winget `DWANGO.OpenToonz.Nightly` | Installed |
| ComfyUI | v0.44.0 | Git source + CPU PyTorch | Working; controller adapter and SD1.5 fp16 checkpoint smoke-tested |

Rollback: uninstall winget packages with `winget uninstall --id <id>`; remove the portable folders
`D:\pinaka-tools\qcli`, `D:\pinaka-tools\mediaconch`, `D:\pinaka-tools\natron` and
`D:\pinaka-tools\ComfyUI`; remove the two portable tool paths from the user `PATH`. No project
artifact or archive is touched by a tool uninstall.

QCTools CLI generated a 147,582-byte report for the archived master. MediaConch CLI parsed the same
file and returned `N/A` without a policy, which is expected. Manim rendered a 480p15 test scene.
ImageMagick reports version 7.1.2-34; Playwright downloaded Chromium headless shell 158.0.8127.18.

ComfyUI v0.44.0 generated a 512×512 PNG on CPU in 252 seconds using the SD1.5 fp16 checkpoint. The
smoke-test image contains pseudo-text and is not suitable for delivery; it proves the local,
zero-spend path works. Checkpoint SHA-256:
`e9476a13728cd75d8279f6ec8bad753a66a1957ca375a1464dc63b37db6e3916`.

| Capability | Current repository status |
|---|---|
| Python controller, Pydantic, SQLite, Git | Implemented and verified |
| OpenCode as operator | In use; repository-specific `/pinaka/*` command and skill bundle pending |
| FFmpeg / ffprobe | Installed; controller uses bundled static FFmpeg or PATH |
| ImageMagick | Installed at version 7.1.2-34; available on PATH |
| Kinocut | Unavailable to the controller process at the recorded baseline |
| Penpot | OpenCode tooling may exist separately; controller bridge is not configured or verified |
| Playwright / Chromium | Installed; Chromium headless shell downloaded; controller adapter not yet used |
| MediaInfo, whisper.cpp, ComfyUI, TTS, and advanced QA | MediaInfo and whisper.cpp are available; ComfyUI, QCTools and MediaConch have controller adapters |
| OpenAI GPT / DeepSeek | Permitted by the roadmap; exact models, credentials, and controller adapters are not yet connection-tested |
| fal | Permitted by the roadmap; exact endpoints, credentials, and controller adapter are not yet connection-tested |
| Nano Banana | Zero-cost adapter implemented; blocked without `GEMINI_API_KEY` |
| Publishing | Explicitly deferred and requires separate human authorization |

## Licence and provenance rules

For every production dependency and artifact, distinguish these layers:

1. application or library licence;
2. binary build configuration and bundled component licences;
3. model, checkpoint, LoRA, voice, or font licence;
4. input asset, creator, release, and attribution rights;
5. provider terms, privacy, retention, and generated-output terms;
6. codec and patent considerations in the intended distribution jurisdictions.

In particular:

- FFmpeg's effective licence depends on its build flags and optional components. Store the outputs
  from `ffmpeg -version` and `ffmpeg -buildconf` with capability evidence.
- Using GPL software to create an output does not by itself make that media output GPL, but
  distributing modified software or a bundled runtime can create source and notice obligations.
- Chromium distributions include third-party components and notices; pin and inventory the build.
- ComfyUI's GPL licence does not grant rights to models, training data, references, or outputs.
- Fonts, voices, music, and stock items need per-item evidence even when obtained through an
  open-source tool.
- H.264, H.265, AAC, and other formats can carry patent considerations independent of the software
  licence. Use a reviewed build and delivery profile.

This document is an engineering and governance inventory, not legal advice. Recheck the exact licence
and provider terms when a version, model, voice, font, codec, or endpoint is pinned.

## Related documents

- `architecture.md` — enforcement boundary and ownership.
- `asset-sourcing-policy.md` — normalized source and rights eligibility policy.
- `next-gate.md` — bounded connection-spike requirements.
- `creative-classification-and-production-guide.md` — purpose-neutral planning, recipes, and QA.
- `implementation-report.md` — verified Phase 1 capability baseline and deferred register.
- `two-phase-production-implementation-plan.md` — two production phases, tool modes, and exit gates.
