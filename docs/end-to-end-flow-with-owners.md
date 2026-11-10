# End-to-end flow with owners

One row per step. **Owner** produces the output. **OpenMontage** shows whether Studio is the owner,
a preferred surface, a fallback, advisory only, or absent. **Gate** is what must pass before the next
phase.

## Status key

| Mark | Meaning |
|---|---|
| `Done` | Implemented and tested |
| `New` | Implemented in this change |
| `Partial` | Works, with a named limitation |
| `Missing` | Not implemented |
| `Blocked` | Implemented in design, unavailable in this session |

## OpenMontage role key

| Role | Meaning |
|---|---|
| **Owner** | Studio produces the artifact; the controller mirrors its hash |
| **Primary** | Preferred execution surface under the preference policy |
| **Fallback** | Second choice when the primary is unavailable |
| **Advisory** | Produces evidence a person may use; cannot approve |
| **Library** | Registers or versions a file; never publishes to a channel |
| **None** | Not involved |

## Phase 0 — Intake and rights

| # | Step | Owner | OpenMontage | Output | Gate | Status |
|---:|---|---|---|---|---|---|
| 1 | Validate brief, budget, rights, provider policy | Controller | None | Contract + hash | G1 | Done |
| 2 | Record plan, grounding, deliverables, segments | Controller | None | Plan, grounding bundles, segments | G1 | Done |
| 3 | Register owned media with signed authorisation | Controller + Human | None | Immutable original + rights record | G1 | New |
| 4 | Source stock when owned media is insufficient | Controller | None — `stock.acquire` has no ready route | Candidates + eligibility | Review | Done |
| 5 | Archive evidence, download, register asset | Controller | None | Reference artifacts + original | Review | Done |
| 6 | Approve brief | Human | None | Approved brief hash | G1 | Done |

## Phase 1 — Plan and direction

| # | Step | Owner | OpenMontage | Output | Gate | Status |
|---:|---|---|---|---|---|---|
| 7 | Create the production workspace | **OpenMontage Studio** | **Owner** — `production create` | Production ID | — | Blocked |
| 8 | Publish scene plan and shot storyboard | Controller, or **OpenMontage Studio** | **Owner** in Studio; `record-scene-plan` locally | Plan document + shots/slots | Review | Done (local document) |
| 9 | Approve animatic and direction | Human | **Advisory** — review packet | Approved animatic hash | G2 | Done |

## Phase 2 — Asset finalization

| # | Step | Owner | OpenMontage | Output | Gate | Status |
|---:|---|---|---|---|---|---|
| 10 | Analyze candidates | Local | None | Observation + proposed criteria | — | Done |
| 11 | Decide treatment per slot | Controller + Human | None | Decision record | Rights per verdict | Done |
| 12 | Apply treatment: inpaint, upscale, crop | **fal** first | **Fallback** — Studio `image.cleanup`, `image.upscale` | Derivative + provenance | Recorded `correct` decision | Partial (fal auth) |
| 13 | Judge tone, brand, composition, legibility, safety | GPT‑5.6 first | **Fallback** — Studio review packet | Judgement + review decision | Autonomy policy | Done (judge), advisory in Studio |
| 14 | Assign primary + backup, optional A/B | Controller | None | `slot_assets` bindings | `R-SLOTS-001` | Done |

## Phase 3 — Assembly

| # | Step | Owner | OpenMontage | Output | Gate | Status |
|---:|---|---|---|---|---|---|
| 15 | Compose and render the master | **OpenMontage Studio** | **Owner** — HyperFrames renderer | Master candidate | One renderer per deliverable | Blocked (Studio); local fallback works |
| 16 | Narration, music, captions | Local | **Fallback** — studio TTS routes | Audio, SRT, captioned derivative | CPS audit | Done (local) |
| 17 | Export package | Controller | **Library** — optional registration | Delivery packet + hashes | — | Done |
| 18 | Technical and external QA | Local | **Advisory** — review packet | QA reports | All pass | Done |

## Phase 4 — Revision and archive

| # | Step | Owner | OpenMontage | Output | Gate | Status |
|---:|---|---|---|---|---|---|
| 19 | Register child revision project | Controller | None | Parent-linked lineage | — | Done |
| 20 | Approve exact final master hash | Human | None | Final approval | G3 | Done |
| 21 | Archive revision, preserve original archive | Controller | None | Immutable revision archive | — | Done (child project archive) |

## Phase 5 — Publication

| # | Step | Owner | OpenMontage | Output | Gate | Status |
|---:|---|---|---|---|---|---|
| 22 | Record publication authorization | Human | None | `authorization_id` | G4 | Done |
| 23 | Preflight and dispatch package | Controller | None | Manual-upload package | — | Done |
| 24 | Publish to the channel | Operator / channel adapter | **Library only** — never a channel publish | Published asset | G5 | Partial (manual) |

## Where OpenMontage is genuinely the owner

| Step | Studio capability | Why Studio rather than local |
|---|---|---|
| 7 | Production, intent, budget, scope | The production is Studio's unit of work |
| 8 | Scene plan, shots, storyboard revisions | Studio tracks plan and shot revisions for review |
| 15 | HyperFrames renderer | Studio owns the renderer and its pinned browser |
| 17 | `workspace.publish` | Versioned library registration for handoff |
| 9, 13, 18 | Review packets | Advisory inspection without approving |

OpenMontage is deliberately **not** an owner for sourcing, rights, approval, archive or publication.
Those stay with the controller.

## Where OpenMontage is a fallback

| Step | Primary | Fallback | Reason |
|---|---|---|---|
| 12 | fal direct | Studio fal routes | fal is headless and per-call |
| 13 | GPT‑5.6 | Studio review | Better model first, human-grade review second |
| 16 | Local Kokoro | Studio hosted TTS | Free local voice before paid hosting |

## Remaining structural work

| Gap | Blocks | Needed |
|---|---|---|
| Studio gateway adapter | Steps 7, 15 | Studio session, membership, production mapping |
| Channel adapter | Step 24 | Authorized posting integration |
