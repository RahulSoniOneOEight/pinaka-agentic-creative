# Pinaka â€” creative-quality fixes

**Date:** 2026-11-13  
**Updated:** 2026-11-14 â€” stage-critique layer implemented; latest run status  
**Reviewed with:** GPTâ€‘5.6 Sol (OpenAI)  
**Scope:** the 8-second rhyme fixture: the original 30/30 run
(`prj_75ad612d53a9490ca81a13a389755c64`) and the latest 1080Ã—1920 / 24 fps
run (`prj_71319ead9d694e6f8baed2e6ece5160d`,
`renders/captioned-2916d6fc01ca.mp4`).

## Verdict

The run proves Pinaka can execute its governed workflow end to end: budget,
sourcing decisions, rights, layer contracts, motion evidence, A/B binding,
captions, QA, archive, change request and revision.

It does **not** prove publication-grade creative quality.

> **30/30 means Policy QC passed. It does not mean the video is good.**

The run must be labelled a **flow-proof fixture**, not a publication candidate.
The defects below are not polish; they are release blockers for a kids channel.

## Implementation status (2026-11-14)

| Item | Status | Evidence |
|---|---|---|
| Per-stage critique layer | **Implemented** | Schema v26 `stage_critiques`/`corrective_actions`; `auto-critique`, `record-critique`, `close-corrective-action`, `critique-status`; `creative_qc` gate; 698 tests |
| Executable production plan | **Implemented** | `ProductionPlanCreate` contract: format/style/delivery/method, seven-pass payload, shot directions, asset routes, rough cut; `production_plan` quality-bar gate |
| Beat-to-delivery intent matrix | **Implemented** | `IntentMatrixCreate` with delivered/blocked beats and evidence; `intent-matrix` CLI; readiness gate |
| Quality contract + creative review | **Implemented** | `QualityContractCreate` / `CreativeReviewCreate`; fixture versus production purpose; human-review gate; `quality-readiness` gate |
| Publication gate | **Implemented** | Fixture contract sets `publication_allowed=false`; publication authorization refuses it and requires readiness |
| Explicit delivery contract for QA | **Implemented** | `qa_render` checks contract resolution, fps, LUFS and true peak |
| Plan generator from a 50â€“100-word idea | **Implemented (rule-based)** | `generate-production-plan` builds the contract from the brief/shot plan; LLM-assisted generation is optional |
| Exact counts / product / claim binding | **Open** | Structural fields exist; automatic verification against approved copy/data is not wired |
| Black cut | **Fixed** | Latest 10 s run: luma 141â€“184, zero dark runs, 1080Ã—1920/24 |
| Character/world | **Partial** | Fixture now has mother + five ducklings and a layered pond; still procedural, not final art |
| Motion / captions / audio | **Partial** | Motion and captions pass on the 10 s fixture; no music/SFX yet |
| 10-second counting rhyme run | **Pass** | `prj_93ab2414â€¦`, 33/33 gates, QA 13/13, quality readiness true, USD 0.00 spent |
| Review packet | **Implemented** | Contact sheet + luma/motion/QA/caption evidence packet for the exact hash |
| Reference-independent quality model | **Implemented** | Contract + intent matrix + review + planning framework |

## Defect classification

The original 30/30 run used 540Ã—960 / 12 fps. The latest fixture uses
1080Ã—1920 / 24 fps and a crossfade; the remaining creative findings are
motion continuity/coverage and caption speed.

| Observation | Class | Why |
|---|---|---|
| Procedural OpenCV duck and gradient background | Fixture artifact | Acceptable for a deterministic USD 0 flow test, but must be marked non-publishable. |
| 540Ã—960 at 12 fps | Fixture artifact **and** product defect | Too slow to render at delivery quality; also below the 1080Ã—1920 / 24 fps delivery profile. |
| No music bed, SFX, title or end card | Fixture shortcut **and** product defect | A kids rhyme needs sound design and a finished opening/closing. |
| Weak character, world, composition and shot variety | Product defect | No character sheet, expression, silhouette craft, depth or location storytelling. |
| One duck despite â€œtwo little ducksâ€; no mother duck or hill | Product defect | The picture contradicts the lyric. |
| Centered bob with camera push doing most of the movement | Product defect | Technically M2, visually not a performance. |
| ~1 s near-black cut from 3.25 s to 4.25 s | **P0 renderer defect** | 13 frames below mean brightness 60; at 4.0 s mean 19.4 and 99% of pixels dark. |
| 4.42 s first caption cue, small default burn style | Product defect | Coverage is complete; reading load and typography are not delivery-ready. |
| Motion gate passes residual pixels | Gate-design defect | It measures movement, not acting or subject quality. |
| Secondary motion passes from HTML markers | Gate-design defect | It checks that effects were authored, not that they are visible or motivated. |
| Style fit is a metadata score | Gate-design defect | `style_id` + `asset_class` + media type cannot prove the look. |
| Technical QA accepts caller-supplied 540Ã—960 / 12 fps | Gate-design defect | It checks conformance to the request, not the delivery contract. |
| No luma, story, composition or appeal gate | Gate-design defect | The main reason a visibly weak output can pass all 30 gates. |

## Root causes

1. **The fixture is the input.** `scripts/qc_complete_flow.py` draws the duck with
   five `cv2` primitives and a 6-pixel sine bob. No amount of downstream QA can
   turn that into a designed character.
2. **The gates are proxies.** Motion = residual pixels; secondary motion = cue
   markers; style = metadata; coverage = runtime; technical QA = container.
   None measures appeal, composition, story alignment or perceived motion.
3. **The authoring layer is thin.** One background and one character layer per
   shot; CSS effects; 1 s fades with no overlap. There is no art direction.
4. **There is no creative review.** No human or model scores the exact final
   hash for character, world, story, motion, typography or sound before approval.

## Corrective plan

### P0 â€” release blockers

| Action | Implementation | Status | Acceptance | Evidence |
|---|---|---|---|---|
| Separate the two claims | Rename the existing result **Policy QC**. Add independent **Creative QC**. Both are required for `publishable=true`. Fixture runs carry `purpose=flow_fixture` and `publication_allowed=false`. | **Done** | A policy-only pass cannot receive final publication approval. This run is visibly non-publishable. | `creative_qc` + `quality_readiness` gates; fixture contract public=false; 698 tests |
| Remove the black cut | Delete automatic `duration/4` scene fades. Use a direct cut or a true overlapping 6â€“8 frame crossfade. Never stack a scene fade with `transition_dissolve`. | **Done** | For this bright preset: no more than two consecutive frames with mean Y below 40; no cut-window dip greater than 35 luma levels against both adjacent 250 ms medians. | 10 s run: luma 141â€“184, zero dark runs, 240 frames |
| Bind QA to the delivery contract | Read resolution, fps, duration, orientation and audio from the frozen contract, not from caller-supplied `expected_*` values. | **Done** | Standard vertical delivery refuses anything below 1080Ã—1920 or 24 fps unless explicitly classified as a fixture/proxy. | `contract.resolution`, `contract.frame_rate`, `contract.loudness` all pass |
| Add a publication review gate | Require named human creative approval after viewing the exact final hash with audio and captions. | **Done** | No publication when any rubric dimension is below 3/5 or a hard check fails. | Production contract requires human review; fixture contract blocks publication |

### P1 â€” before the next creative run

| Workstream | Steps | Status | Acceptance |
|---|---|---|---|
| Story/shot contract | Convert the lyric into a beat matrix: required entities, action, setting, emotion and shot evidence. Mark **five ducklings**, **mother duck**, **pond**, **count 1â€“5** and **quacking** as critical. | **Done** â€” production plan + intent matrix | Every critical beat is visibly represented; a single duck cannot stand in for five. Human story score â‰¥4/5. |
| Character/world | Build or source approved character sheets for five distinguishable ducklings and a mother duck: front/side poses, palette, expressions, silhouette, scale, layered parts. Build pond, reeds, hill, sky, foreground foliage, reflections, texture and lighting layers. | **Partial** â€” fixture has mother + five ducklings and a layered pond; still procedural | Identity stable across shots; silhouettes readable at phone size; foreground/midground/background depth present. |
| Perceived motion | Use a layered M2 rig or approved motion source. Animate swim translation, head turns, wing flap, motherâ€™s beak/quack, water ripples, reed sway and parallax. Measure the character ROI, not whole-frame residuals. | **Pass on fixture** â€” continuity identity 1.0006 | Each character shot has a readable action; subject travel or articulated-part travel reaches at least 2% of frame dimension; at least two independently moving character parts; secondary motion affects â‰¥0.5% of frame for â‰¥25% of the shot. |
| Captions | Generate phrase-level cues, then apply a kids preset: max two lines, ~32 characters per line, one phrase per cue, 1â€“3 s cue duration, â‰¤15 CPS, â‰¥52 px at 1080 width, safe margins, outline/backplate, contrast â‰¥4.5:1. | **Pass on fixture** â€” 2 cues, 0.994 coverage, â‰¤15 CPS | No orphan words, clause overload, action obstruction, unsafe placement or drift >100 ms. |
| Audio | Source licensed/owned light music, quack, splash and ambience; record rights and backups. Duck the bed under narration and sync cues to action. | **Partial** â€” narration only; âˆ’16.61 LUFS, âˆ’4.45 dBTP | Narration intelligible; bed at least 8 dB below speech; âˆ’16 LUFS Â±1; true peak â‰¤âˆ’2 dBTP; no clipping or unintended silence. |
| Review tooling | Produce contact sheets, boundary strips, motion overlays, caption-safe-area previews and luma/audio plots for the exact final artifact. | **Done** â€” contact sheet + metrics packet generated | Review packet covers every shot and transition and is attached to approval. |

### P2 â€” polish

- Gentle bloom, water highlights, atmospheric particles, reflections and restrained texture.
- Varied shot scale and composition instead of repeated centered framing.
- Colour-script and rhythm review across the full 8 seconds.
- A meaningful A/B axis (caption treatment or opening composition), not a grade-only variant.
- Test title/end-card retention once the compliant master exists.

## Planning framework review (implemented)

The framework is now a frozen `production-plan/v1` artifact, not a prose
brief. It separates the four decisions and carries the seven passes and the
shot handoff fields.

### Four decisions, kept independent

| Decision | Field | Examples |
|---|---|---|
| Production format | `production_format` | cinematic_story, sing_along, narrative_brand_film, product_showcase, creator_ad, launch_promo, explainer, tutorial, documentary |
| Visual style | `visual_style` | anime, flat_2d_cartoon, watercolor, stylized_3d, cinematic_live_action, studio_product, motion_graphics, mixed |
| Delivery format | `delivery_format` | vertical_reel, widescreen_ad, square_social, episode |
| Production method | `production_method` | real_footage, licensed_stock, layered_2d_animation, image_to_video, 3d_render, screen_capture, compositing, generated_image |

A product ad can therefore open with an anime scene, cut to real product
footage, and close with an approved pack shot without collapsing format,
style, delivery and method into one label.

### Seven passes captured in the plan

| Pass | Contract section |
|---|---|
| 1. Interpret the idea | `brief`: objective, audience, language, required_content, constraints, assumptions, missing_inputs |
| 2. Creative treatment | `treatment`: premise, emotional_progression, visual_language, pacing, sound |
| 3. Script in beats | `beats`: beat_id, purpose, viewer_need (see/hear/understand/feel), script_line, visual_action, duration, shot_keys |
| 4. Assets and continuity | `asset_manifest` + `continuity_notes`: asset_id, source, method, approval, fallback, reuse |
| 5. Direct each shot | `shot_directions`: purpose, timing, composition, performance, lighting, graphics, audio, assets, method, fallback, acceptance checks, reviewer check |
| 6. Timed rough cut | `rough_cut`: duration, beat timings, audio reference, caption notes, holds |
| 7. Produce, assemble, review | Critiques + `creative_qc` + `production_plan` gate; final human review still open |

### Shot record

Every `ShotDirection` is required to carry:

- purpose and timing cue
- duration
- composition: framing, angle, subject placement, focal point, camera movement, caption room
- performance: action start, action end, pause/reaction, eyeline
- lighting: direction, softness, contrast, palette, change
- graphics: effects, trigger, duration, intensity, placement, editable
- audio: dialogue, narration, music, ambience, SFX, silence, carry-over
- asset IDs, production method and fallback
- acceptance checks and a reviewer check

A validator rejects unknown shot or asset IDs, duplicate IDs, missing beat
timings and non-positive durations.

### Asset routing and readiness

- Each shot names the asset IDs it uses.
- Each asset records source, method, approval status, fallback and reuse.
- `production-plan-status` reports missing shots, missing assets, planned
  assets, rough-cut duration mismatches and brief missing inputs.
- The `production_plan` gate in `quality_bar` fails until the plan is
  structurally ready.
- `missing inputs` and `planned assets` are warnings, not silent passes; a
  missing asset is a hard issue.

### Commands and evidence

```text
record-production-plan <project> --file plan.json
production-plan <project>
production-plan-status <project>
```

- 698 tests pass.
- The end-to-end policy test records a complete plan and passes the new gate.
- The latest fixture records a plan; the gate is structurally ready, while
  creative QC still blocks on motion and captions.

### What this framework still does not prove

1. **Idea-to-plan generation.** The contract accepts a complete plan; the
   agent that expands a 50â€“100-word idea into every field is the next build.
2. **Delivered intent matrix.** Beats link to shots and shots link to assets,
   but there is no separate gate that says â€œthis beat was actually delivered
   on screen, in audio, and in the captionâ€ with timestamped evidence.
3. **Exact data binding.** Counts, prices, claims, product labels and offer
   copy are structural fields; they are not yet verified against approved
   copy or data.
4. **Rough-cut proof.** The rough cut is a timed plan, not an automatically
   validated render; reveal timing and counting pauses still need an animatic
   or render check.
5. **Human creative review.** The 1â€“5 rubric and review packet are not yet a
   final-approval requirement.
6. **Studio capability routing.** `openmontage_capability` is a planning field;
   live Studio discovery, cost quoting and automated production routing are
   not yet wired.

## OpenMontage capability integration (planning â†’ production)

OpenMontage is not a single production method. It is a studio service with a
capability library. Planning chooses a **capability**; production executes it
through Studio or a recorded fallback. The production method stays independent.

### Planning phase

1. **Discover, do not assume.** Load Studio bootstrap/orchestration and read
   account readiness, credits, provider connections and the current capability
   library. Costs and availability change.
2. **Choose a capability per beat/shot.** Record it on
   `ShotDirection.openmontage_capability` or
   `AssetRoute.openmontage_capability`.
3. **Record the production contract for that capability:** required inputs,
   output spec, cost/credit range, provider/account, rights and consent, AI
   labelling, delivery format and fallback.
4. **Budget it.** Studio credits and own-provider dollars are separate.
   Pinaka's funded budget, approval and one-take guards still apply.
5. **Define the fallback before production.** If Studio is unavailable or the
   budget is refused, the shot uses the recorded local/stock/owned route, not
   an unapproved retry.

### Capability map (representative)

| Capability | Planning use | Production method | Studio route | Fallback |
|---|---|---|---|---|
| Cinematic shots | Chases, frozen moments, scale reveals | generated / image-to-video | opening still â†’ shot plan â†’ clips | stock + local compositing |
| AI presenter | Split-screen host, consistent presenter | generated image + video + TTS | reference sheet â†’ clips â†’ captions | human presenter or VO + stock |
| Impossible one-take | Continuous world-change shot | image-to-video | one unbroken camera plan | layered composition / cuts |
| 3D worlds | Character/world film, editable scene | 3d_render | Meshy + Blender | 2D layers / stock |
| Product explode | Product photo â†’ parts â†’ reassemble | image-to-video | first/last frame product shot | product stills + compositing |
| Motion graphics | Data, type, particles, simulations | motion_graphics / compositing | code-drawn animation | local HTML/CSS/Remotion |
| UGC ads | Creator-style vertical ad | generated image + video + TTS | creator sheet â†’ clips â†’ captions/price | actor + phone footage |
| Product commercials | Macro, light, end card | image-to-video + graphics | shot plan + end card | product photography + edit |
| Narrated story | Painted storybook, narration, score | generated images + audio | images â†’ camera moves â†’ voice/music | illustration/stock + VO |
| Pixar-style animation | Character short | image-to-video + audio | character consistency â†’ clips | 2D/3D animation |
| Documentary | Stock footage, maps, timeline | licensed_stock + compositing | script â†’ stock â†’ maps/timeline | archive/owned footage |
| Explainer | Document â†’ charts | screen_capture / motion_graphics | read document â†’ charts â†’ narration | manual charts / slides |
| Hand-drawn anime | Title/sequence with on-model characters | image-to-video | character sheet â†’ shots | 2D frame animation |
| Grade / recolour / key / swap / track | Post-production look and compositing | compositing | local Studio tools | manual VFX |
| Voice / languages / lip-sync | Multilingual presenter or narration | TTS / avatar | translate â†’ voice â†’ lips | recorded VO + subtitles |
| Sound / music / captions / edit | Mix, SFX, styled captions, beat cuts, reframe, upscale | audio / compositing | local Studio tools | library/owned assets + manual edit |

### Production phase

- Use Studio through its MCP tools: bootstrap/orchestration, account/connections,
  production create/invoke, jobs read/wait, workspace publish/import.
- Use one stable request ID per accepted job; never repeat a render to retrieve
  a result; never retry a refused submission without the person's explicit ask.
- Import finished media into the Pinaka artifact store with hashes, provider job
  records, receipts, credits and the AI-generated label.
- Record Studio vs local ownership in the flow step (`execution_owner`,
  `studio_role`, `fallback_of`, `trade_off`).
- If Studio is unavailable, record `BLOCKED` with the exact reason and execute
  the plan's fallback.
- Pinaka remains the system of record. Studio is the renderer/service; it does
  not own approvals, budgets, rights or the archive.

### Rules

- A capability choice never overrides rights, consent, claims or brand approval.
- Do not infer an unsupported capability; discover it.
- Do not copy reference numbers, prices or claims; use approved data.
- Do not bypass a refused sign-in, membership, budget or consent with another
  route or credential.
- Keep `production_format`, `visual_style`, `delivery_format` and
  `production_method` independent of the capability name.

### Current status

- `ShotDirection.openmontage_capability` and
  `AssetRoute.openmontage_capability` are implemented as optional planning
  fields.
- The Studio MCP tools are available but were not invoked for this fixture;
  Studio is in private alpha and no credits or provider spend were used.
- Capability discovery snapshot, cost estimation and automated
  capability-to-production routing are still open.

## Stage-critique layer (implemented)

This is the critique part of the flow, now in code.

### Stages and checks

| Stage | Automated critique | Blocking examples |
|---|---|---|
| Script | script exists, approved, â‰¥8 words | no script, unapproved script |
| Shots | shot count, duration, subject/action/camera, variety | no shots, shot with no subject/motion |
| Style | preset/style IDs, style-fit per selected asset | missing preset/style, off-style picks |
| Assets | resolution, alpha/cutout, sharpness, dark/blank, primary/backup presence | full-frame asset in a cutout slot, missing file, low resolution |
| Motion | levels/evidence, final motion verdict, continuity, motion-area coverage | failed continuity, <0.5% motion coverage, missing evidence |
| Visual | 1080Ã—1920, 24 fps, luma continuity, dark/dead frames, letterbox, sharpness | any near-black gap, stale/high finding, soft render |
| Captions | coverage, max cue, CPS, lines | coverage <90%, cue >4 s, >15 CPS, >2 lines |
| Audio | loudness, true peak, audio presence, warnings/fails | failed loudness/peak, no audio QA |
| Delivery | A/B completeness, A/B packet existence | missing A/B packet |

### How the gate behaves

- Each critique records `findings` with `severity` (`blocker`, `high`,
  `medium`, `low`), evidence and a `corrective_action`.
- Every open **blocker/high** finding creates a row in `corrective_actions`.
- A stage gate fails if the critique is missing, stale for the final hash,
  verdict is `block`, or any high-impact action is still open.
- Closing an action requires evidence and a named closer; then the stage must
  be re-critiqued. A pass critique cannot hide an open high finding.
- The `creative_qc` gate requires all nine stages; a policy-only pass cannot
  reach delivery.

### Commands

```text
auto-critique <project> <stage> [--artifact ARTIFACT_ID]
record-critique <project> --file critique.json
close-corrective-action <action_id> --by NAME --result TEXT --evidence TEXT
critique-status <project> [--artifact ARTIFACT_ID]
```

### Current evidence

- Schema v26; 698 tests pass.
- Latest render (`prj_71319â€¦`, `captioned-2916â€¦`): 1080Ã—1920, 24 fps,
  192 frames, luma 95.9â€“122.6, no dark runs; visual critique **passes**.
- Latest creative QC: motion **revise** (continuity, low motion coverage) and
  captions **revise** (18.33 CPS); all other stages pass.
- The run is correctly blocked. The next step is to fix those two output
  issues, close the actions, and re-critique â€” not to lower the gate.

## Revised quality model

### Layer 1 â€” Policy QC

Keep the existing 30 gates: contracts, budget, rights, sourcing, approvals,
lineage, flow completeness, backups, effects-plan presence, motion evidence
presence, caption coverage and container integrity.

A pass means **governed, reproducible, compliant and technically formed**.

### Layer 2 â€” Creative QC

#### Automated hard minimums

| Area | Minimum |
|---|---|
| Delivery | 1080Ã—1920, 24 fps, correct duration/orientation, H.264-compatible master, audio when required. |
| Luma continuity | No unintended blackout longer than two frames; cut-window rule from P0. Intentional fades are exempt but reviewed. |
| Story | Every critical beat entity/action has timestamped visual evidence; no contradictory count, identity or setting. |
| Character continuity | Subject-tracked colour, scale, identity and location within approved tolerances; no unexplained swaps or disappearance. |
| Motion | Camera-compensated subject-ROI measurement; visible action plus two-part articulation for M2; quantified secondary-motion visibility. |
| Composition | Subject inside safe area; no accidental crop, occlusion, letterboxing or persistent dead-centre repetition. |
| Captions | 100% dialogue coverage, â‰¤100 ms drift, â‰¤15 CPS, 1â€“3 s cues, â‰¤2 lines, safe area, contrast â‰¥4.5:1. |
| Audio | âˆ’16 LUFS Â±1, true peak â‰¤âˆ’2 dBTP, intelligible narration, no clipping/dropouts; required music/cues present and rights-cleared. |

Model-based story/style checks should produce findings and confidence scores,
but must not alone approve subjective quality.

#### Human/model review rubric

Score 1â€“5 on:

1. Character appeal and expression
2. Silhouette and composition
3. World-building, depth, lighting, texture
4. Story/lyric alignment
5. Perceived character and secondary motion
6. Shot variety, pacing, transition continuity
7. Caption readability and typography
8. Music, SFX, narration, sync
9. Overall child-audience engagement and finish

**Pass:** no dimension below 3, average at least 4.0, all hard checks pass.
A vision-language model may provide a second opinion; a human owns appeal,
humour, emotional clarity and final taste.

## USD 1 rhyme re-run plan

- [ ] Keep the USD 1 cap and generation-last policy; search owned/open-licensed assets first.
- [ ] Freeze a four-beat storyboard:
  1. Pond establishing shot with **two** ducklings and an integrated title.
  2. Ducklings swim toward and over a layered hill.
  3. Mother duck reacts and visibly quacks.
  4. Distant ducklings/resolution with a brief end lockup.
- [ ] Build/source a consistent layered character set and pond world at 1080Ã—1920.
- [ ] Render at 24 fps with articulated M2 motion, water ripples, reed sway and parallax.
- [ ] Replace independent scene fades with cuts or overlapping 6â€“8 frame crossfades.
- [ ] Add licensed music, ambience, splash and quack cues with narration ducking.
- [ ] Burn the designed kids-caption preset after phrase-level timing review.
- [ ] Run technical QA, luma continuity, story matrix, subject-ROI motion, continuity, caption, audio and rights checks.
- [ ] Generate the review packet and complete human Creative QC.
- [ ] Re-run all 30 Policy QC gates on the exact final hash.
- [ ] Publish only if **Policy QC = pass**, **Creative QC = pass** and final human approval is recorded.

## Documentation correction

`pinaka-flow-gaps.txt` and `docs/update-register.md` currently say Revision 18
closes all listed items. Correct the wording to:

- Revision 18 closes the listed **governance implementation** items.
- Creative-production validation remains open.
- This run is a successful **flow fixture** and a **failed publication candidate**.
- 30/30 is a Policy QC result, not an overall quality certification.

## Reference-independent version

A benchmark video is optional. The full model for judging an original idea
without one is in [quality-model.md](quality-model.md): a frozen creative
quality contract, approved pre-production artifacts, absolute craft minimums,
intent traceability, internal consistency and human/audience review.

## 10-second counting rhyme run (2026-11-14)

A dedicated fixture now produces a 10-second counting rhyme under the USD 1.00
cap and passes the policy, creative, plan and quality gates.

| Item | Value |
|---|---|
| Project | `prj_93ab241405be456ab09eb35f16a9c32e` |
| Archive | `archive_715774cf204f4e8582492099f7256dd2` |
| Final artifact | `art_1b45d7a8d78a4e11a536ed2bca8cecf2` |
| Hash | `f09140e639f6eea0ea005249d22d57be1530f4c186493ab55eebe26f3e193103` |
| Video | 1080Ã—1920, 24 fps, 10.0 s, 240 frames |
| Quality bar | **33/33 gates pass, 0 failed** |
| QA | 13 checks, 0 failing |
| Luma | 141â€“184, zero dark runs, zero dead frames |
| Motion | continuity identity 1.0006, scale 1.0006, colour 0.0057, position 0.0053 |
| Captions | ASR, 2 cues, 9.94/10 s, ratio 0.994 |
| Audio | âˆ’16.57 LUFS, âˆ’4.38 dBTP |
| Budget | USD 1.00 cap, USD 0.00 spent; A/B labels 1/1 |
| A/B packet | `40bcd5b7f8f3fa3a0ebef24052db39725e934075aa8e632fad8463ea6dbae304` |
| Quality contract | `flow_fixture`; publication **not allowed**; intent beats delivered |
| Review packet | `art_c807ba9964f34d40b11afe5940c4db41` (contact sheet + metrics) |
| Plan | generated from the shot plan and frozen |
| Change request | `crq_9f9fb078...` â†’ revision `prj_0e213405...` |
| Flow / critiques | complete / complete |

The output is a **flow fixture**, not a publication-quality creative. The
quality contract makes that explicit; publication authorization refuses it.
The remaining gaps are creative production, not software gates.

## Immediate next actions

1. Add Studio bootstrap/orchestration discovery, a capability/cost snapshot and
   the capability-to-production router, with the plan's fallback enforced.
2. Bind exact counts, prices, claims, labels and offer copy to approved data.
3. Replace the fixture family/pond with a real character sheet and world, add
   music/SFX, and produce a 1080p24 counting rhyme that is worth publishing.
4. Complete the human 1â€“5 review for a production contract; the fixture uses a
   model review and blocks publication.
5. Publish only from a `purpose=production` contract with human review and
   `publication_allowed=true`.

