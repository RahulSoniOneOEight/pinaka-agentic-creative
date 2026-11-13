# Quality without a reference video

**Question:** if the next project is a different idea and there is no
benchmark video, how do we know the production is good?

**Answer:** a reference video is optional. The benchmark is the **frozen
creative contract plus the approved pre-production artifacts**, checked by
absolute craft standards, intent traceability, internal consistency and a
human creative review. An external video is one input, not the definition of
quality.

## 1. The principle

Do not ask “does it look like this reference?” Ask four separate questions:

1. **Did we specify what good means for this idea?**  
   Audience, platform, duration, genre, emotional arc, must-have beats,
   characters, world, style, sound, captions and success signals.
2. **Did we approve a visual/audio target before spending?**  
   Character sheet, world bible, style frames, storyboard, animatic, scratch
   audio, palette, typography.
3. **Does the output match that target and the craft minimums?**  
   Absolute checks plus consistency and intent coverage.
4. **Would the intended audience respond the way we planned?**  
   Human rubric, model second opinion, and a small audience test when possible.

A reference video can inform step 1 and 2. It cannot replace them.

## 2. The five quality layers

### Layer 1 — Creative quality contract (before production)

One artifact per project. It is the project’s definition of “good”.

| Field | Examples |
|---|---|
| Audience | 2–5 year olds, parents, B2B buyers |
| Platform / format | YouTube Shorts, 9:16, 15–30 s |
| Genre / tone | counting rhyme, warm, playful, calm |
| Must-have beats | two ducklings, mother duck, pond, hill, quack, count, return |
| Character rules | count, identity, palette, silhouette, expressions, scale |
| World rules | pond, reeds, hill, sky, lighting, depth |
| Style rules | flat 2D vector, line weight, palette, texture |
| Motion rules | swim, splash, quack, wing flap, secondary motion |
| Audio rules | song or chant, music bed, SFX, loudness/peak |
| Caption rules | phrase length, CPS, lines, size, safe area |
| Success signals | clarity, recall, repeat-view intent, completion |
| Anti-patterns | blob characters, dead frames, centered sameness, text overload |
| Hard minimums | 1080×1920, 24 fps, no black gap, −16 LUFS, ≤−2 dBTP |

The contract is hashed and frozen. A later change is a revision, not a silent
drift.

### Layer 2 — Pre-production benchmark (the internal reference)

For an original idea, these artifacts become the reference:

- character sheet: front/side/expression/scale/palette
- world bible: location, materials, lighting, palette, depth layers
- style frames: 3–5 key frames rendered at delivery resolution
- storyboard: one panel per beat, with action and camera
- animatic: timed, with scratch narration/music
- shot contract: subject, action, setting, motion, duration, transition
- audio direction: voice, music, SFX, mix targets
- caption script: phrase-level timings

Freeze their hashes. The final is checked against these, not against a
different creator’s video.

### Layer 3 — Production QC (per stage)

Every stage gets a critique with findings, severity and evidence:

| Stage | Automated checks | Human/model checks |
|---|---|---|
| Script | approved, length, beat coverage, entities | tone, rhyme, age-appropriate language |
| Shots | count, duration, subject/action/setting, variety | does each shot earn its place? |
| Style | preset/style IDs, style-fit, palette drift | does it feel like the intended world? |
| Assets | resolution, sharpness, alpha/cutout, blank/dark, rights | character appeal, silhouette, expression |
| Motion | subject ROI, displacement, two-part articulation, continuity, secondary-motion visibility | acting, weight, anticipation, beat sync |
| Visual | luma continuity, dead frames, letterbox, composition, safe area | lighting, depth, colour, finish |
| Captions | coverage, CPS, cue length, lines, contrast | phrasing, reading rhythm, emphasis |
| Audio | LUFS, true peak, clipping, silence, sync | music, voice, SFX, emotional fit |
| Delivery | A/B packet, credits, archive, exact hash | title, thumbnail, end card, platform fit |

High-impact findings (blocker/high) create a corrective action and block the
next gate until the action is closed with evidence and a re-critique.

### Layer 4 — Assembly and intent QC

A beat matrix connects the idea to the output:

```text
lyric / beat -> shot -> asset(s) -> motion -> audio -> caption -> evidence
```

Every must-have beat must have a timestamped visual and audio evidence item.
The gate fails on missing, contradictory or unexplained beats. This is how an
original idea is checked without a reference video.

Also check:

- pacing: beat length and cut rhythm vs the declared audience
- transitions: no unintended black, jump or dead frame
- repetition: no accidental repeated shot or loop
- retention shape: hook in the first 2 s, payoff before the end
- brand: title, thumbnail, end card, credits

### Layer 5 — Creative review and audience signal

A named human reviewer scores the exact final hash on a 1–5 rubric:

1. Character appeal and expression
2. Silhouette and composition
3. World-building, depth, lighting, texture
4. Story/lyric alignment
5. Perceived character and secondary motion
6. Shot variety, pacing, transitions
7. Caption readability and typography
8. Music, SFX, narration, sync
9. Overall audience engagement and finish

**Pass:** no dimension below 3, average ≥4.0, all hard checks pass.

A vision-language model may provide a second opinion and flag likely issues.
It must not approve taste. When possible, test with 3–10 target viewers:
comprehension, recall, emotional response, repeat-view intent. Record the
sample and the result; do not claim statistical significance from a small
test.

## 3. How to make “good” measurable for an arbitrary idea

Use four kinds of checks, not one:

### A. Absolute craft minimums
Resolution, fps, duration, no black gaps, sharpness, caption CPS, loudness,
true peak, clipping, safe area. These are objective and idea-independent.

### B. Internal consistency
Character identity, scale, position, palette, line weight, world layout,
lighting direction, caption style, audio mix. Compare the final to the frozen
pre-production artifacts and across shots.

### C. Intent traceability
Each must-have beat has a planned and delivered visual/audio/caption. Each
shot has a purpose. No orphan assets, no missing entities, no contradictions
(two ducks in the lyric, one on screen).

### D. Audience-response proxies
Clarity, recall, emotional beat, repeat-view intent, completion. Use a human
rubric and a small audience test; treat model scores as signals, not verdicts.

## 4. What to compare against when there is no reference

1. **The contract**: did we deliver what we said?
2. **The approved pre-production artifacts**: character sheet, style frames,
   storyboard, animatic.
3. **The project’s previous approved work**: is this better or worse than the
   last accepted piece?
4. **Genre gold standards**: a curated internal library of 3–5 exemplars per
   genre, used for principles, never for copying.
5. **The audience**: the only external truth that matters.

Do not use a single external video as the sole benchmark. It imports another
project’s cast, budget, style and IP, and it makes every new idea look like a
failure.

## 5. Implementation in Pinaka

Already implemented:

- per-stage critiques with findings and severity
- mandatory corrective actions for blocker/high findings
- a `creative_qc` gate in the quality bar
- automated visual QC: luma continuity, dead frames, letterbox, resolution,
  fps, sharpness, caption coverage/CPS, audio QA, motion/continuity
- an A/B delivery packet and archive evidence

Next additions:

1. `CreativeQualityContract` artifact and `quality-contract` CLI.
2. `intent-matrix` artifact: beat → shot → asset → motion → audio → caption.
3. `review-packet`: contact sheet, boundary strips, motion overlay, caption
   safe-area preview, luma/audio plots.
4. `record-creative-review`: rubric scores, reviewer, exact hash.
5. `auto-critique` reads thresholds and must-have beats from the contract
   instead of hardcoded constants.
6. `creative_qc` gate requires contract + all stage critiques + closed
   high-impact actions + human review + intent matrix.

## 6. Example: a new idea with no reference

Idea: “Five little crabs count shells on a beach.”

The contract defines five crabs, one mother crab, a beach, shells, counting
5→0, a wave, and a return. The character sheet fixes each crab’s colour and
expression. The storyboard maps each lyric line to a shot. The animatic fixes
timing. Style frames fix the palette and lighting. The final is judged on:

- five crabs, not one
- beach, shells, wave, mother crab present
- each count visible and timed to the lyric
- crab motion, wave motion, shell interactions
- captions at 3–5 word phrases
- music/SFX and mix targets
- the human rubric and a small audience test

No external video is needed. The benchmark was created from the idea.

## 7. Rules that keep quality from drifting

- No production without a frozen quality contract.
- No animatic approval without a storyboard and style frames.
- No final approval without all stage critiques and a human review.
- High-impact findings require a corrective action and a re-critique.
- A policy pass is not a creative pass.
- A model score is a signal, not an approval.
- A single reference video is inspiration, not the definition of quality.
- Every project leaves a review packet and a scorecard for the next project.
