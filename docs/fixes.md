# Pinaka — creative-quality fixes

**Date:** 2026-11-13  
**Updated:** 2026-11-14 — stage-critique layer implemented; latest run status  
**Reviewed with:** GPT‑5.6 Sol (OpenAI)  
**Scope:** the 8-second rhyme fixture: the original 30/30 run
(`prj_75ad612d53a9490ca81a13a389755c64`) and the latest 1080×1920 / 24 fps
run (`prj_2b6fc3acc335480c803ca04c4e0fca55`).

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
| Per-stage critique layer | **Implemented** | Schema v26 `stage_critiques`/`corrective_actions`; `auto-critique`, `record-critique`, `close-corrective-action`, `critique-status`; `creative_qc` gate; 693 tests |
| Black cut | **Fixed** | Crossfade replaces stacked 1 s fades; latest run has luma 90–116, zero dark runs, 1080×1920/24 |
| 1080×1920 / 24 fps delivery | **Fixed in fixture; partial in QA** | Latest render is 1080p24; `qa_render` still accepts caller-supplied expectations |
| Character/world/motion/captions/audio | **Open** | Creative QC blocks the latest run on motion continuity/coverage and caption CPS |
| Human creative review / review packet | **Open** | Rubric and review packet are specified, not yet wired to final approval |
| Reference-independent quality model | **Documented; contract pending** | `docs/quality-model.md`; `CreativeQualityContract` and intent matrix not yet built |

## Defect classification

The original 30/30 run used 540×960 / 12 fps. The latest fixture uses
1080×1920 / 24 fps and a crossfade; the remaining creative findings are
motion continuity/coverage and caption speed.

| Observation | Class | Why |
|---|---|---|
| Procedural OpenCV duck and gradient background | Fixture artifact | Acceptable for a deterministic USD 0 flow test, but must be marked non-publishable. |
| 540×960 at 12 fps | Fixture artifact **and** product defect | Too slow to render at delivery quality; also below the 1080×1920 / 24 fps delivery profile. |
| No music bed, SFX, title or end card | Fixture shortcut **and** product defect | A kids rhyme needs sound design and a finished opening/closing. |
| Weak character, world, composition and shot variety | Product defect | No character sheet, expression, silhouette craft, depth or location storytelling. |
| One duck despite “two little ducks”; no mother duck or hill | Product defect | The picture contradicts the lyric. |
| Centered bob with camera push doing most of the movement | Product defect | Technically M2, visually not a performance. |
| ~1 s near-black cut from 3.25 s to 4.25 s | **P0 renderer defect** | 13 frames below mean brightness 60; at 4.0 s mean 19.4 and 99% of pixels dark. |
| 4.42 s first caption cue, small default burn style | Product defect | Coverage is complete; reading load and typography are not delivery-ready. |
| Motion gate passes residual pixels | Gate-design defect | It measures movement, not acting or subject quality. |
| Secondary motion passes from HTML markers | Gate-design defect | It checks that effects were authored, not that they are visible or motivated. |
| Style fit is a metadata score | Gate-design defect | `style_id` + `asset_class` + media type cannot prove the look. |
| Technical QA accepts caller-supplied 540×960 / 12 fps | Gate-design defect | It checks conformance to the request, not the delivery contract. |
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

### P0 — release blockers

| Action | Implementation | Status | Acceptance | Evidence |
|---|---|---|---|---|
| Separate the two claims | Rename the existing result **Policy QC**. Add independent **Creative QC**. Both are required for `publishable=true`. Fixture runs carry `purpose=flow_fixture` and `publication_allowed=false`. | **Partial** — Creative QC gate implemented; fixture/publishable flags pending | A policy-only pass cannot receive final publication approval. This run is visibly non-publishable. | `creative_qc` gate blocks on missing/stale critiques or open high-impact actions; 693 tests |
| Remove the black cut | Delete automatic `duration/4` scene fades. Use a direct cut or a true overlapping 6–8 frame crossfade. Never stack a scene fade with `transition_dissolve`. | **Done** | For this bright preset: no more than two consecutive frames with mean Y below 40; no cut-window dip greater than 35 luma levels against both adjacent 250 ms medians. | Latest 1080p24 run: luma 90–116, zero dark runs, 192 frames |
| Bind QA to the delivery contract | Read resolution, fps, duration, orientation and audio from the frozen contract, not from caller-supplied `expected_*` values. | **Partial** — critique enforces 1080×1920/24; `qa_render` contract binding pending | Standard vertical delivery refuses anything below 1080×1920 or 24 fps unless explicitly classified as a fixture/proxy. | Latest render 1080×1920/24; visual critique passes resolution/fps |
| Add a publication review gate | Require named human creative approval after viewing the exact final hash with audio and captions. | **Open** — rubric and review packet specified, not wired | No publication when any rubric dimension is below 3/5 or a hard check fails. | Human review record, artifact hash, reviewer notes, preview contact sheet |

### P1 — before the next creative run

| Workstream | Steps | Status | Acceptance |
|---|---|---|---|
| Story/shot contract | Convert the lyric into a beat matrix: required entities, action, setting, emotion and shot evidence. Mark **two ducklings**, **mother duck**, **pond**, **hill/far-away movement** and **quacking** as critical. | **Partial** — shots critique exists; intent matrix pending | Every critical beat is visibly represented; a single duck cannot stand in for two. Human story score ≥4/5. |
| Character/world | Build or source approved character sheets for two distinguishable ducklings and a mother duck: front/side poses, palette, expressions, silhouette, scale, layered parts. Build pond, reeds, hill, sky, foreground foliage, reflections, texture and lighting layers. | **Open** — fixture has one cleaner cutout duck, no cast/world | Identity stable across shots; silhouettes readable at phone size; foreground/midground/background depth present. |
| Perceived motion | Use a layered M2 rig or approved motion source. Animate swim translation, head turns, wing flap, mother’s beak/quack, water ripples, reed sway and parallax. Measure the character ROI, not whole-frame residuals. | **Partial** — motion critique catches failures; output still fails continuity/coverage | Each character shot has a readable action; subject travel or articulated-part travel reaches at least 2% of frame dimension; at least two independently moving character parts; secondary motion affects ≥0.5% of frame for ≥25% of the shot. |
| Captions | Generate phrase-level cues, then apply a kids preset: max two lines, ~32 characters per line, one phrase per cue, 1–3 s cue duration, ≤15 CPS, ≥52 px at 1080 width, safe margins, outline/backplate, contrast ≥4.5:1. | **Partial** — CPS improved 25 → 18.33; still over 15, and “quack” is split one word per cue | No orphan words, clause overload, action obstruction, unsafe placement or drift >100 ms. |
| Audio | Source licensed/owned light music, quack, splash and ambience; record rights and backups. Duck the bed under narration and sync cues to action. | **Open** — narration only, no music/SFX | Narration intelligible; bed at least 8 dB below speech; −16 LUFS ±1; true peak ≤−2 dBTP; no clipping or unintended silence. |
| Review tooling | Produce contact sheets, boundary strips, motion overlays, caption-safe-area previews and luma/audio plots for the exact final artifact. | **Open** — visual QC metrics exist; review packet pending | Review packet covers every shot and transition and is attached to approval. |

### P2 — polish

- Gentle bloom, water highlights, atmospheric particles, reflections and restrained texture.
- Varied shot scale and composition instead of repeated centered framing.
- Colour-script and rhythm review across the full 8 seconds.
- A meaningful A/B axis (caption treatment or opening composition), not a grade-only variant.
- Test title/end-card retention once the compliant master exists.

## Stage-critique layer (implemented)

This is the critique part of the flow, now in code.

### Stages and checks

| Stage | Automated critique | Blocking examples |
|---|---|---|
| Script | script exists, approved, ≥8 words | no script, unapproved script |
| Shots | shot count, duration, subject/action/camera, variety | no shots, shot with no subject/motion |
| Style | preset/style IDs, style-fit per selected asset | missing preset/style, off-style picks |
| Assets | resolution, alpha/cutout, sharpness, dark/blank, primary/backup presence | full-frame asset in a cutout slot, missing file, low resolution |
| Motion | levels/evidence, final motion verdict, continuity, motion-area coverage | failed continuity, <0.5% motion coverage, missing evidence |
| Visual | 1080×1920, 24 fps, luma continuity, dark/dead frames, letterbox, sharpness | any near-black gap, stale/high finding, soft render |
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

- Schema v26; 693 tests pass.
- Latest render: 1080×1920, 24 fps, 192 frames, luma 90–116, no dark runs;
  visual critique **passes**.
- Latest creative QC: motion **revise** (continuity, low motion coverage) and
  captions **revise** (18.33 CPS); all other stages pass.
- The run is correctly blocked. The next step is to fix those two output
  issues, close the actions, and re-critique — not to lower the gate.

## Revised quality model

### Layer 1 — Policy QC

Keep the existing 30 gates: contracts, budget, rights, sourcing, approvals,
lineage, flow completeness, backups, effects-plan presence, motion evidence
presence, caption coverage and container integrity.

A pass means **governed, reproducible, compliant and technically formed**.

### Layer 2 — Creative QC

#### Automated hard minimums

| Area | Minimum |
|---|---|
| Delivery | 1080×1920, 24 fps, correct duration/orientation, H.264-compatible master, audio when required. |
| Luma continuity | No unintended blackout longer than two frames; cut-window rule from P0. Intentional fades are exempt but reviewed. |
| Story | Every critical beat entity/action has timestamped visual evidence; no contradictory count, identity or setting. |
| Character continuity | Subject-tracked colour, scale, identity and location within approved tolerances; no unexplained swaps or disappearance. |
| Motion | Camera-compensated subject-ROI measurement; visible action plus two-part articulation for M2; quantified secondary-motion visibility. |
| Composition | Subject inside safe area; no accidental crop, occlusion, letterboxing or persistent dead-centre repetition. |
| Captions | 100% dialogue coverage, ≤100 ms drift, ≤15 CPS, 1–3 s cues, ≤2 lines, safe area, contrast ≥4.5:1. |
| Audio | −16 LUFS ±1, true peak ≤−2 dBTP, intelligible narration, no clipping/dropouts; required music/cues present and rights-cleared. |

Model-based story/style checks should produce findings and confidence scores,
but must not alone approve subjective quality.

#### Human/model review rubric

Score 1–5 on:

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
- [ ] Build/source a consistent layered character set and pond world at 1080×1920.
- [ ] Render at 24 fps with articulated M2 motion, water ripples, reed sway and parallax.
- [ ] Replace independent scene fades with cuts or overlapping 6–8 frame crossfades.
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

## Immediate next actions

1. **Close the two open fixture findings:** fix motion continuity/coverage and
   get captions to ≤15 CPS with phrase-level cues; re-critique and close the
   actions.
2. Add `purpose=flow_fixture` / `publication_allowed=false` and refuse
   publication for fixtures at final approval.
3. Bind `qa_render` to the frozen delivery contract instead of caller-supplied
   `expected_*` values.
4. Add `CreativeQualityContract`, the intent matrix and the review packet;
   require them in `creative_qc`.
5. Re-run the $1 rhyme with a real character sheet, world, motion, music and
   captions, then complete the human rubric.
