# Raw Text to Workable Brief

**Status:** Implemented (deterministic planner)

## Purpose

A creative request usually arrives as one informal sentence. The planner turns that text into a
governed package without asking a long list of questions, and without inventing facts.

```text
raw text
  → preserved as an immutable original artifact
  → deterministic extraction (duration, audience, age, family, copy, destination)
  → workable brief with decision provenance
  → shots + scene plan
  → proposed reusable elements and slots
```

The planner is **deterministic**: it uses explicit pattern rules, not a language model. Every derived
value is recorded with its provenance, so a reviewer can see what was supplied, derived, or assumed.

## What is extracted

| Field | Rule |
|---|---|
| Duration | `5-second`, `10s`, `2 minutes`; otherwise assumed 10 seconds |
| Audience | `children`, `kids`, `toddlers`, `teens`, `adults`, `professionals` |
| Age range | `aged 4 to 6`, `ages 4-6`, `5 year old` |
| Content family | `rhyme`, `explainer`/`tutorial`, `ad`/`promo`, `story` |
| Spoken copy | The first quoted passage |
| Destination | `youtube`, `instagram`, `tiktok`, `reels`, `shorts` |
| Output format | 9:16 for short-form destinations, otherwise 9:16 review master |

## Decision provenance

- **supplied** — duration or copy stated in the request
- **derived** — audience and content family inferred from the wording
- **assumed** — visual style, voice, and a missing duration (all reversible)
- **blocked** — a price, percentage, or superlative that needs approved grounding

A blocked claim does not stop the storyboard. It blocks `final_approval` and `publication` only, so
planning and visual work can continue.

## Output

`plan-from-text` returns:

- the preserved source artifact and its hash;
- the analysis that drove the plan;
- the recorded workable brief;
- created shots whose durations sum to the deliverable duration;
- a recorded scene plan;
- proposed reusable elements (from a small noun lexicon); and
- proposed slots that allow **owned and stock** sources only.

Proposed slots deliberately exclude paid generation. Widening a slot to allow generation is an
explicit routing decision.

## CLI

```powershell
pinaka plan-from-text <project-id> --file text-brief-request.json
```

## What it does not do

- It does not write the final script or copy; supplied copy is carried through verbatim.
- It does not fabricate facts, prices, claims, or protected identities.
- It does not record slots or elements automatically; it proposes them for review.
- It does not spend money.
