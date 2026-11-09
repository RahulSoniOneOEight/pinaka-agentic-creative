# Pinaka Creative Classification and Production Guide

**Status:** Proposed canonical product note and agent guide for the purpose-neutral v2 workflow  
**Version:** 0.1  
**Date:** 9 November 2026  
**Audience:** Product owners, creative operators, reviewers, engineers, and production agents

## Source and authority

This note consolidates and normalizes:

- `Pinaka_Creative_Classification_and_Production_Guide.txt`, SHA-256
  `A672B571F4DF88C340C55D3CE7C17964030A29D35AE34FD6DF898E0248293025`;
- `Pinaka_Agentic_Creative_Production_Specification.txt`, SHA-256
  `0F0ED40542D22A707A61586EF259F17BBC0733DEE22A5C0073A3791D5904EBF9`;
- the controller-first implementation in this repository; and
- `asset-sourcing-policy.md` for provider and licence governance.

This document describes the intended product and operating model. The Python controller remains the
enforcement boundary. If this note and implemented policy differ, agents must report the difference
and follow the stricter implemented controller rule until an approved migration changes it.

Normative terms:

- **MUST / MUST NOT** — mandatory policy.
- **SHOULD / SHOULD NOT** — expected default; deviations require a recorded reason.
- **MAY** — optional behavior within all applicable ceilings and gates.

---

## 1. Product objective

Pinaka turns a creative brief into an auditable, editable production plan and final output for many
purposes, including:

- product and service promotion;
- informative and educational reels;
- tutorials and demonstrations;
- commentary and news analysis;
- comedy, fictional stories, and emotional pieces;
- list, trivia, series, and loop formats;
- short, mid-length, and long-form video; and
- still creatives.

The system must not force every project into either “product” or “entertainment.” It describes a
creative through independent variables, derives the required production policy, and applies the
strictest relevant rules at project, segment, shot, and slot level.

The operating principle is:

> Agents propose. The controller validates, authorizes, commits, and records. Humans approve
> consequential creative, factual, rights, budget, and final-output decisions.

---

## 2. Product principles

1. **Classify before planning.** Record what the creative is, what it should achieve, and what
   constraints apply before selecting a recipe or generating assets.
2. **Keep dimensions orthogonal.** Output type, product role, form, structure, outcome, truth policy,
   subject, tone, and commercial use are separate.
3. **Use recipes as defaults, not permissions.** A recipe accelerates planning but cannot override a
   rights, safety, factual, identity, platform, budget, or treatment restriction.
4. **Apply rules deterministically.** The controller evaluates versioned rules. An LLM may propose
   inputs or alternatives but must not silently decide enforcement outcomes.
5. **Use the strictest applicable constraint.** Overlapping rules combine by taking the highest risk,
   lowest treatment ceiling, union of required gates, and exactest slot requirement.
6. **Govern at the smallest relevant unit.** A project may contain factual and fictional segments;
   exact and generic slots; and different treatment ceilings per shot.
7. **Ground according to purpose.** Product facts, evidence-backed claims, tutorial steps, source
   context, and fictional continuity are different grounding bundles.
8. **Rights precede aesthetics.** An attractive asset with incompatible or unclear rights cannot
   enter production.
9. **Preserve editability.** Text, branding, captions, exact values, product layers, diagrams, and
   effects should remain separate where practical.
10. **Generate only what is necessary.** Prefer owned assets, eligible stock, deterministic edits,
    and still-based composition before generated motion.
11. **Budget and deadline are policy inputs.** Agents may offer fallbacks; they cannot exceed a cap or
    silently trade accuracy for speed.
12. **Every consequential result is reproducible.** Store profile version, recipe version, matched
    rules, grounding hashes, selected assets, job IDs, costs, approvals, and final output hash.

---

## 3. Conceptual hierarchy

```text
Project
├── shared brief, audience, budget, deadline, language, series context
├── creative profile and rule trace
├── grounding bundles
├── Deliverable 1 — e.g. 30s vertical video
│   ├── Segment — one truth/realism context
│   │   ├── Shot — timed visual/narrative unit
│   │   │   └── Slot — replaceable visual, text, product, audio, or data component
│   │   └── Shot ...
│   └── Segment ...
└── Deliverable 2 — e.g. square still adaptation
```

Definitions:

- **Project:** the governed production unit with shared budget, deadline, approvals, and audit trail.
- **Deliverable:** one required output variant with its own format and destination.
- **Segment:** a contiguous section with one truth, realism, or disclosure policy.
- **Shot:** a timed beat or visual unit.
- **Slot:** the smallest replaceable component with explicit source, exactness, treatment, and
  fallback rules.

---

## 4. Canonical classification model

Classification describes the creative. It does not directly authorize an asset, transformation, or
provider call.

### 4.1 Deliverable variables

| Variable | Values / shape | Guidance |
|---|---|---|
| `output_kind` | `video`, `still` | Output kind is independent from content purpose. “Still” is not a creative intent. |
| `duration_seconds` | Positive number or null for stills | Store the exact duration; derive a length band. |
| `orientation` | `vertical`, `horizontal`, `square`, `custom` | Store separately from duration and purpose. |
| `aspect_ratio` | Examples: `9:16`, `16:9`, `1:1` | Do not infer solely from orientation when a precise ratio is required. |
| `resolution` | Width and height | Set per destination or delivery specification. |
| `destination` | Platform, channel, placement, or file handoff | Drives safe areas, captions, rights, disclosure, and audience checks. |
| `language` | BCP-47-style language identifier where possible | Record narration, captions, and localized text requirements. |

Length bands are derived, not independently edited:

- **15s Short:** one hook and one message or payoff.
- **30s Short:** hook, a few supporting beats, and a clear ending.
- **60s Short:** concise story, demonstration, or example.
- **Custom Short:** below two minutes but not represented by a named preset.
- **Mid-length:** at least two minutes and below eight minutes.
- **Long-form:** eight minutes or more. Exactly 480 seconds routes to Long-form.

### 4.2 Product role

| Value | Meaning | Policy effect |
|---|---|---|
| `none` | No featured product | No product grounding is required unless commercial claims still reference one. |
| `cameo` | Product appears briefly or contextually | The depicted product/variant still requires an approved reference. |
| `hero` | Product is the main focus | Product identity, branding, variant, labels, offer, and claim slots are exact. |

Product role is independent of form. An explainer, tutorial, comedy, or story can contain a cameo or
hero product.

### 4.3 Creative form

The normalized form describes how the message is delivered:

| Form | Meaning |
|---|---|
| `direct` | A direct announcement, offer, benefit, CTA, or concise message. |
| `explainer` | Makes a concept, fact, mechanism, or process easier to understand. |
| `tutorial` | Shows the actual steps needed to reproduce a result. |
| `commentary` | Interprets real material while distinguishing evidence from opinion. |
| `comedy` | Uses setup, escalation, and payoff to entertain. |
| `story` | Follows a character or event through a narrative arc. |

Compatibility mapping from the source guide:

- **Ad** remains a recipe/product label, not an exclusive form. It normally combines commercial
  usage, a product role, a direct or other creative form, and a sell/trust outcome.
- **List** moves to structure so that explainers, trivia, comedy, and tutorials can all be lists.
- **Loop** moves to structure so that any suitable form can reconnect its ending to its opening.

### 4.4 Structure patterns

One creative can use more than one compatible structure:

| Structure | Meaning |
|---|---|
| `linear` | Opening to ending without a special repeated structure. |
| `list` | Distinct tips, items, claims, facts, or examples in a repeatable sequence. |
| `loop` | Final visual/audio state reconnects to the opening for replay. |
| `chapters` | Multiple named sections, generally for mid-length or long-form work. |

### 4.5 Subject domains

A profile records one primary subject and optional additional subjects. Every matched subject rule is
evaluated.

| Subject | Key concerns |
|---|---|
| `lifestyle` | Everyday settings, routines, and experiences. |
| `tech` | Real tools, devices, software, screen capture, and workflow accuracy. |
| `money` | Verified figures, claims, advice boundaries, and designated review. |
| `health` | Careful sourcing, claim review, advice boundaries, and designated review. |
| `food` | Ingredients, preparation, packaged-product identity, and safety claims. |
| `science` | Evidence, diagrams, uncertainty, and distinction between model and observation. |
| `history` | Archive provenance and clear identification of reconstruction. |
| `news` | Current authenticated sources, date, context, and synthetic-media restrictions. |
| `sports_pop_culture` | Likeness, teams, logos, characters, clips, and entertainment-property rights. |
| `kids` | Age-appropriate language, pacing, imagery, sound, and platform requirements. |
| `fiction` | Invented characters, places, events, and continuity. |

### 4.6 Tone

Record one primary tone and optional secondary tones:

- `neutral` — straightforward and restrained;
- `serious` — measured and suited to analysis or sensitive topics;
- `friendly` — conversational and approachable;
- `humorous` — playful writing, reactions, and timing;
- `dramatic` — tension through contrast, pacing, and sound;
- `emotional` — imagery, character, and music support a feeling;
- `urgent` — fast delivery highlights a time-sensitive action or consequence;
- `calm` — gentle motion and more space between beats.

Tone changes presentation, not truth, rights, or identity requirements.

### 4.7 Intended outcomes

Record exactly one primary outcome and zero or more secondary outcomes:

- `inform` — help viewers understand a topic or update;
- `teach` — help viewers perform a task or apply a concept;
- `entertain` — hold attention and deliver a satisfying payoff;
- `evoke_emotion` — leave viewers with a chosen feeling;
- `drive_engagement` — encourage a comment, save, share, or other response;
- `bring_viewers_back` — support an ongoing series or next instalment;
- `sell` — prompt purchase, enquiry, sign-up, or another commercial action;
- `build_trust` — increase confidence through evidence, transparency, or a credible demo.

Outcome does not determine commercial asset rights by itself. A monetized entertainment video can be
commercial even when its primary outcome is `entertain`.

### 4.8 Truth policy

| Value | Meaning | Minimum governance |
|---|---|---|
| `source_bound` | Factual statements and depictions must follow approved evidence. | Claim/source grounding and traceable citations. |
| `mixed_fact_opinion` | Real evidence is combined with analysis or opinion. | Separate facts, quotations, inference, and opinion. |
| `fictional` | Characters, settings, or events are invented. | Story/character continuity and originality/IP review. |
| `satire` | Fictional or exaggerated treatment references real people/events. | Clear stylisation, satire review, and authentic/satirical segment separation. |
| `reconstruction` | A recreated depiction represents a historical or unavailable event. | Evidence basis, reconstruction label, and realism/disclosure review. |

Truth policy may differ by segment. A factual project must not hide a realistic synthetic or
reconstructed event inside an authentic-footage segment.

### 4.9 Usage and distribution context

Record independently from creative outcome:

- use class: `commercial`, `noncommercial`, or `editorial`;
- whether the destination is monetized;
- whether the creative is advertising or sponsored;
- whether modification is planned;
- whether attribution can be delivered;
- intended platforms and territories;
- whether a call to action, sponsor disclosure, or paid-placement disclosure is required.

These values feed the asset-rights evaluator in `asset-sourcing-policy.md`.

### 4.10 Audience and accessibility

At minimum record:

- audience description and expected prior knowledge;
- age band when relevant;
- language and localization requirements;
- caption requirement;
- narration/transcript requirement;
- readability and safe-area constraints; and
- known accessibility requirements such as flashing limits, color dependence, or audio-only meaning.

### 4.11 Budget and deadline

Record:

- hard external-spend cap and currency;
- optional stage/shot generation allowances;
- maximum attempts per generated slot;
- contingency policy;
- target or hard deadline, timezone, review availability, and QA buffer;
- priority order, such as accuracy → deadline → budget → visual ambition; and
- preapproved fallback categories.

Agents cannot infer the priority order when constraints conflict.

---

## 5. Conditional intake variables

### 5.1 Quality tier

| Tier | Default behavior |
|---|---|
| `test` | Lean plan; deterministic composition and generated stills only unless explicitly approved. |
| `standard` | Normal allowance for the selected recipe. |
| `flagship` | Additional craft and bounded iteration; MAY include one hero effect at the payoff. |

Quality tier does not relax evidence, rights, or identity rules.

### 5.2 Series pattern

- `one_off` — complete in one creative;
- `series` — reuse a versioned guide for character, voice, format, style, prior topics, and recurring
  elements.

Series work must check both continuity and novelty.

### 5.3 Product complexity

- `simple_pack` — one approved photo/clip may represent the product;
- `variants_or_bundles` — each featured variant/item needs verified assets;
- `needs_demonstration` — show real use through verified material;
- `service_or_software` — show an authentic workflow, screen capture, or service example.

This field is applicable only when product role is cameo/hero or commercial claims depend on a
specific product/service.

### 5.4 Asset readiness

- `photos_and_footage` — current assets cover still and motion needs;
- `photos_only` — compose from stills and source eligible real motion as required;
- `low_quality_assets` — first test permitted deterministic corrections;
- `no_assets` — source eligible material and request exact references for protected slots.

### 5.5 Source material

- `own_footage_or_voice` — start with creator/client material;
- `stock_or_archive` — preserve source, rights, creator, context, and licence evidence;
- `idea_only` — source or create assets according to each slot policy;
- `third_party_clips` — verify provenance, context, and permission before use.

---

## 6. Parameters derived by the rule engine

Derived values must store their rule IDs and rationale. They are not silently invented by an agent.

### 6.1 Production lane

| Lane | Use |
|---|---|
| `template_data` | Fill a reusable layout with approved facts and assets. |
| `stills_led` | Build around photos, cutouts, diagrams, or graphics. |
| `stock_led` | Use licensed stock as the primary visual material. |
| `footage_led` | Edit existing footage and add context or annotation. |
| `narrated_visual` | Narration drives diagrams, graphics, stills, and supporting visuals. |
| `real_capture` | Record the actual task, workflow, product, or action. |
| `character_story` | Build scenes around characters and a continuity guide. |
| `loop` | Design picture and sound to reconnect ending and opening. |

A project has a dominant lane, but segments and shots may use different lanes.

### 6.2 Risk level

- `low` — straightforward material or clearly fictional content;
- `medium` — claims, identifiable real references, or sensitive context need extra checks;
- `high` — stricter sourcing, specialist review, disclosure, or platform constraints apply.

Money and health are at least Medium. Other rules may raise them to High.

### 6.3 Realism level

- `stylised` — visibly illustrative or animated;
- `realistic_unchanged` — preserves what an authentic source depicts;
- `realistic_ai_altered` — materially changes a realistic existing source;
- `realistic_synthetic` — creates a scene that could be mistaken for real.

Realism is evaluated per segment and affected shot. Realistic synthetic treatment triggers
disclosure and context review and is blocked for synthetic factual event footage.

### 6.4 Treatment ceiling

The ceiling is an upper bound; each slot may be stricter:

1. `as_is` — approved asset as supplied;
2. `match` — crop, framing, grade, speed, or normalization without changing depicted facts;
3. `effects` — designed motion, transitions, overlays, and annotations;
4. `ai_enhance` — improve an eligible existing asset within approved limits;
5. `composite` — combine layers, each with explicit source and permission;
6. `ai_created` — create a new eligible asset.

An operation must satisfy both the project/segment ceiling and its slot permission.

### 6.5 Effects level

- `none` — clean cuts or static layouts;
- `light` — subtle motion and simple emphasis;
- `standard` — visible effects serving content and tone;
- `hero_moment` — one stronger payoff effect, normally Flagship only.

Defaults:

- Neutral, Serious, and Calm → Light;
- Friendly, Humorous, Dramatic, Emotional, and Urgent → Standard;
- Hero moment → only when the recipe, quality tier, budget, and risk allow it.

### 6.6 Slot exactness

| Exactness | Meaning |
|---|---|
| `exact` | Preserve approved product identity, branding, factual value, quotation, or required wording. |
| `faithful` | Surrounding treatment may vary while approved references and meaning remain intact. |
| `generic` | Flexible sourcing for mood, setting, supporting people, or non-specific visuals. |

Exactness is per slot, not a single project-wide flag.

### 6.7 Effort

- `small` — existing assets and a simple template cover most work;
- `medium` — additional preparation, sourcing, generation, or review;
- `large` — more shots, deeper research, complex capture, heavy continuity, or specialist review.

Effort informs scheduling and operator expectations but does not itself authorize spend.

### 6.8 Additional derived values

- required grounding bundle types;
- required approval and specialist-review gates;
- recipe ID/version and beat template;
- ending type: CTA, takeaway, reveal, resolution, next episode, or loop;
- primary success metric: enquiry, conversion, completion, save, share, replay, trust, or return;
- generated seconds and attempt allowance;
- forecast cost, critical path, latest safe starts, and fallbacks; and
- per-shot duration, source, exactness, crop, motion, text position, transition, and audio cue.

---

## 7. Classification and rule-resolution process

```text
brief
  ↓
validate deliverables and mandatory intake
  ↓
normalize form, structure, outcomes, truth, usage, subject, and product role
  ↓
select closest versioned recipe
  ↓
apply explicit brief choices
  ↓
evaluate all subject, risk, realism, product, platform, and slot rules
  ↓
resolve conflicts using stricter constraints
  ↓
derive grounding, lanes, gates, effort, budget, schedule, and fallbacks
  ↓
emit reviewable production-plan proposal
```

### 7.1 Rule precedence

From strongest to weakest:

1. legal, rights, safety, privacy, and platform blocks;
2. truth, subject, realism, disclosure, and designated-review rules;
3. product identity, source authenticity, and slot exactness restrictions;
4. explicit brief choices that remain within the above constraints;
5. recipe defaults;
6. aesthetic ranking and cost/deadline optimization.

### 7.2 Conflict resolution

When multiple rules match:

- risk = highest matched risk;
- treatment ceiling = strictest matched ceiling;
- exactness = exactest required level;
- gates = union of all required gates;
- disclosures and attribution = union of obligations;
- generation allowance = lowest applicable cap unless a human explicitly approves more;
- incompatible requirements = `needs_human_input`, never silent guessing.

### 7.3 Decision trace

Store with every approved plan:

- guide/schema version and source hash;
- normalized intake;
- selected recipe ID/version;
- matched rule IDs and derived decisions;
- explicit overrides, approver, reason, and scope;
- unresolved uncertainties;
- plan hash and approval dependency; and
- selected fallbacks.

---

## 8. Grounding model

Replace the assumption that every creative needs one product-style fact set with versioned grounding
bundles.

| Bundle | Used for | Typical contents |
|---|---|---|
| `product_facts` | Product/service cameo, hero, sell, or product claims | SKU, variant, labels, logo, price, currency, offer dates, approved claims, approved assets |
| `claim_evidence` | Explainers, lists, science, money, health, factual story | Claim, evidence, source, date, confidence, uncertainty, approved wording |
| `tutorial_steps` | Tutorial or demonstration | Starting state, ordered steps, required views, expected result, reproducibility check |
| `source_context` | Commentary, news, archive, third-party clips | Source, author, publication/date, original context, excerpt boundaries, authenticity |
| `story_bible` | Fiction, comedy, narrative | Character appearance, voice, relationships, world, locations, props, continuity |
| `series_bible` | Recurring formats and episodes | Format rules, recurring elements, prior topics, character/voice/style continuity |
| `brand_pack` | Sponsored, commercial, or branded work | Brand voice, logo rules, approved wording, prohibited claims, CTA/disclosure requirements |
| `platform_policy` | Kids, ads, sponsored content, destination-specific work | Audience, disclosures, safe areas, caption rules, platform restrictions |

Each bundle must have an ID, type, version, content hash, evidence references, reviewer status,
created time, and current/superseded state.

A project may require several bundles. Example: a sponsored money explainer needs claim evidence,
product facts, brand rules, and platform policy. A fictional comedy may need only a story bible and
platform policy.

Later approvals depend on the exact bundle versions they use. Replacing a bundle invalidates only
dependent plans, shots, captions, assets, and approvals.

---

## 9. Segment, shot, and slot planning

### 9.1 Segments

Use segments whenever truth, realism, source, or disclosure policy changes.

Example:

```text
News-comedy project
├── Segment A: authenticated source footage
│   ├── truth: source_bound
│   ├── realism: realistic_unchanged
│   └── ceiling: match
├── Segment B: explanatory graphics
│   ├── truth: source_bound
│   ├── realism: stylised
│   └── ceiling: effects
└── Segment C: satire
    ├── truth: satire
    ├── realism: stylised
    └── ceiling: ai_created where eligible
```

### 9.2 Shots

Every shot should record:

- stable key and ordinal;
- segment ID;
- narrative beat and purpose;
- duration;
- narration/dialogue and on-screen text;
- realism and treatment ceiling;
- source plan and fallback;
- expected cost/time;
- continuity references;
- required checks and reviewer; and
- transition and audio cue.

### 9.3 Slots

Every mandatory slot should record:

- semantic role, such as background, product, person, diagram, headline, caption, narration, music;
- exactness: exact, faithful, or generic;
- identity and continuity requirement;
- factual/grounding requirement;
- allowed source types and provider constraints;
- transformation ceiling and prohibited operations;
- text/safe zone;
- attribution/disclosure obligation;
- selected artifact and content hash; and
- approved fallback.

Generation is authorized per unresolved eligible slot, not for a whole project without bounds.

---

## 10. Approval and state model

Recommended state progression:

```text
DRAFT
  → PLANNED
  → BRIEF_APPROVED
  → GROUNDING_LOCKED
  → SOURCING
  → ANIMATIC_READY
  → ANIMATIC_APPROVED
  → FINAL_CANDIDATE
  → FINAL_APPROVED
  → ARCHIVED
```

`GROUNDING_LOCKED` replaces the product-biased concept of `FACTS_LOCKED`. The current implementation
still uses `FACTS_LOCKED` and requires migration before agents may rely on the new name.

### Approval 1 — brief and production plan

Approve:

- normalized classification;
- recipe and beat direction;
- grounding requirements and source plan;
- risk, realism, treatment, and disclosure decisions;
- budget, generation allowances, deadline, and fallback priorities; and
- brand, subject, and platform review assignments.

### Approval 2 — sourcing and selected assets

Approve:

- current grounding bundle versions;
- exact product/identity references;
- selected owned, stock, archive, capture, and generated candidates;
- source, context, rights, licence, attribution, and uncertainty records;
- unresolved-slot plan; and
- allowed cost for the animatic and any still generation.

### Approval 3 — animatic and motion allowance

Approve:

- pacing, story, visual hierarchy, captions, narration, music direction, and transitions;
- realism/disclosure treatment;
- exact shots that require generation;
- provider, attempt, generated-seconds, and cost caps; and
- deadline fallbacks.

### Approval 4 — final output

Approve the hash of the exact final candidate after:

- shared technical QA;
- triggered purpose/subject/platform QA;
- attribution and disclosure checks;
- budget reconciliation; and
- archive-manifest verification.

An approval never authorizes unlimited retries, unapproved providers, changed grounding, or a
different output hash.

---

## 11. Asset sourcing and generation policy

Use the governed cascade:

```text
1. Owned approved asset
      ↓ unavailable
2. Official API source with documented terms
      ↓ no eligible candidate
3. Governed manual import with per-item evidence
      ↓ no eligible candidate
4. Deterministic crop, grade, correction, or composition
      ↓ unresolved eligible slot
5. Governed still generation
      ↓ only when real motion is essential
6. Governed video generation after animatic approval
```

For every external candidate:

1. Normalize provider, creator, item, licence, attribution, terms date, evidence, and uncertainty.
2. Derive usage context from commerciality, destination, modification, and attribution capability.
3. Evaluate rights before visual quality.
4. Discard `blocked`; hold `needs_review`; rank only `eligible` candidates.
5. Import selected originals content-addressably with source-manifest and content hashes.

Provider-specific rules are in `asset-sourcing-policy.md`. Mixkit and Videezy remain manual-only;
Openverse requires origin verification; Unsplash API use requires hotlink/download tracking and
attribution; Pexels API use requires an existing key and its API linking rules.

### Generation rules

- Agents MUST NOT call a generation provider directly.
- The controller MUST verify approval, provider permission, idempotency, budget reservation, slot
  eligibility, input references, and treatment ceiling before submission.
- Product labels, logos, exact prices, quotations, captions, and mandatory wording SHOULD remain
  deterministic overlays rather than generated pixels.
- Realistic synthetic factual event footage is blocked.
- History/news reconstructions must be clearly classified and reviewed.
- Fictional generation must follow the approved story/character bible.
- Video generation remains disabled until the animatic and exact motion allowance are approved.
- Every charged attempt is recorded, including rejected creative takes.

---

## 12. Default form and structure guidance

| Form / pattern | Default production lane | Default ceiling | Notes |
|---|---|---|---|
| Direct promotional | `stills_led` or `template_data` | `composite` | Keep product, offer, logo, price, CTA, and required text exact/editable. |
| Explainer | `narrated_visual` | `ai_enhance` | Claims and diagrams remain tied to evidence. |
| Tutorial | `real_capture` | `match` for instructional footage | Annotations/captions are separate layers; missing steps require recapture. |
| Commentary | `footage_led` | `match` for factual footage | Preserve context and distinguish fact from opinion. |
| Comedy | `character_story` | `ai_created` for eligible fiction | Real people, brands, and events trigger additional review. |
| Story | `character_story` or `narrated_visual` | `ai_created` for eligible fiction; `ai_enhance` for eligible stock restyling | Preserve character/world continuity. |
| List structure | `narrated_visual` | `ai_enhance` | Apply the underlying form and subject rules too. |
| Loop structure | `loop` | `ai_enhance` | Verify picture and audio seam by playing twice. |

These defaults may only be tightened by subject, truth, risk, product, platform, source, or slot rules.

---

## 13. Combination rules

### CR-01 — Hero/cameo product with missing or low-quality assets

- **Approach:** begin with the actual SKU and approved variant references.
- **Guidance:** inspect resolution, label readability, required angle, and identity. Try only permitted
  crop, exposure correction, or cutout. Request replacement imagery, footage, or a verified 3D view
  if the source fails.
- **Gate:** pause affected product shots until an acceptable exact asset is approved.

### CR-02 — Sell outcome with any form

- **Approach:** product, price, offer, logo, and CTA are exact components even when the creative is
  an explainer, tutorial, comedy, or story.
- **Guidance:** use approved SKU assets and approved commercial records. Keep price, CTA, logo, and
  required wording editable.
- **Gate:** verify variant, price, currency, offer validity, claim support, and disclosure before export.

### CR-03 — Promotional work with humorous or dramatic tone

- **Approach:** place humour/tension in setup, characters, or edit—not in altered product identity or
  unsupported claims.
- **Guidance:** storyboard the strongest joke/dramatic beat and preserve factual accuracy.
- **Gate:** review brand fit and treatment at brief approval.

### CR-04 — Tutorial with instructional steps

- **Approach:** record the real task, product, or software workflow in order.
- **Guidance:** capture starting state, key actions, and result. Add zooms, highlights, and captions as
  separate layers. Recapture missing steps.
- **Gate:** a reviewer can follow the visible steps and reproduce the result.

### CR-05 — Commentary or news

- **Approach:** lead with authenticated, attributed material and clearly separated commentary.
- **Guidance:** store source, date, and context. Crop/grade only where meaning is preserved. Keep
  factual footage, captions, quotations, and opinion distinct.
- **Gate:** review authenticity/context; block realistic synthetic event footage in factual segments.

### CR-06 — News with comedy

- **Approach:** create distinct authentic and stylised-satire segments.
- **Guidance:** signal satire through opening and visual language. Use illustration, caricature, or
  graphics within applicable likeness/rights rules.
- **Gate:** satire must read clearly without depending on a tiny disclaimer or prior viewer knowledge.

### CR-07 — Money or health

- **Approach:** sourced explainer with approved claims and clear diagrams.
- **Guidance:** store source/date for every number and claim. Default ceiling is `ai_enhance` or lower;
  stricter risk rules take precedence.
- **Gate:** assign at least Medium risk and route designated advice/claim sign-off.

### CR-08 — Kids

- **Approach:** match language, pacing, imagery, and sound to the intended age.
- **Guidance:** identify audience and destination platform before locking the plan.
- **Gate:** resolve age/platform review before export.

### CR-09 — Comedy/story with realistic synthetic visuals

- **Approach:** start with an approved character/world guide.
- **Guidance:** identify every realistic synthetic shot; review treatment/disclosure and check character,
  voice, prop, and location continuity.
- **Gate:** approve realism and disclosure; stricter subject rules override fiction defaults.

### CR-10 — Sports/pop culture with generated visuals

- **Approach:** prefer original characters, generic settings, and sourced material for specific people,
  teams, or entertainment properties.
- **Guidance:** inspect prompts, references, and frames for likenesses, characters, logos, and marks.
- **Gate:** confirm intended use and rights for every identifiable element; replace uncertainty.

### CR-11 — Long-form with heavy generation

- **Approach:** allocate generated motion only to selected beats.
- **Guidance:** use narration, verified footage, diagrams, still motion, and deterministic graphics for
  the remainder. Estimate generated seconds and cost at animatic stage.
- **Gate:** compare against project cap and deadline; shorten, replace, or replan over-cap shots.

### CR-12 — Short format with long-form depth

- **Approach:** select one question, claim, or takeaway.
- **Guidance:** hook → strong example → payoff. Split additional ideas into separate shorts or remove
  them from the short adaptation.
- **Gate:** the output is self-contained and fits duration/readability constraints.

### CR-13 — Idea-only with missing assets

- **Approach:** classify each missing slot before choosing source/treatment.
- **Guidance:** search approved owned/stock sources first; generate eligible fictional/generic assets;
  request real product/capture material for protected slots.
- **Gate:** each generated asset matches approved treatment, grounding, rights, budget, and deadline.

### CR-14 — Urgent + emotional, or calm + sell

- **Approach:** give emotion or commercial action its own readable beat.
- **Guidance:** urgency must leave enough time for emotion to land; calm selling still needs a clear CTA.
- **Gate:** animatic review confirms both intended feeling and intended action.

### CR-15 — List in a recurring series

- **Approach:** reuse recognizable series structure while varying episode content.
- **Guidance:** compare recent hooks, items, examples, layouts, and endings; store episode history.
- **Gate:** episode adds something new and remains recognizably part of the series.

---

## 14. Recipe model

A recipe is a versioned starting point, not a permission grant. It contains:

- recipe ID, version, and description;
- selector hints over product role, form, structure, duration, subject, tone, and outcomes;
- default production lane, treatment ceiling, effects, and ending;
- beat sequence;
- grounding requirements;
- default checks and fallbacks;
- cost/time profile; and
- examples and known exclusions.

The Planner selects the closest recipe, fills unset preferences, then applies all explicit brief and
combination rules. Every use stores the exact recipe version.

### 14.1 Offer Short

- **Profile:** Hero product; direct promotional form; 15s; often food/retail; urgent; sell.
- **Defaults:** stills-led, composite ceiling, Standard effects.
- **Beats:** product hook → offer → CTA.
- **Checks:** identity, variant, price, currency, offer dates, CTA, and text legibility.

### 14.2 Benefits Short

- **Profile:** Hero product; direct/explainer; 30s; often tech; friendly; build trust with optional sell.
- **Defaults:** stills-led, composite ceiling, Standard effects.
- **Beats:** problem → benefit → proof → CTA.
- **Checks:** evidence for benefits and accuracy of any demonstration.

### 14.3 Explainer Short

- **Profile:** No required product; explainer; 60s; source-bound subject; neutral; inform.
- **Defaults:** narrated-visual, AI-enhance ceiling, Light effects.
- **Beats:** question → concept → example → takeaway.
- **Checks:** figures, claims, citations, and diagram meaning.

### 14.4 Long Explainer

- **Profile:** No required product; explainer; 480s+; often science/history; serious; inform/return.
- **Defaults:** narrated-visual, AI-enhance ceiling, Light effects, chapters.
- **Beats:** hook → chapters → examples → recap → next instalment where applicable.
- **Checks:** depth, repetition, chapter logic, evidence, and generated-seconds cap.

### 14.5 Captured Tutorial

- **Profile:** Tutorial; mid-length by default; often tech/food; friendly; teach.
- **Defaults:** real-capture, Match ceiling for instructional footage, Standard annotation effects.
- **Beats:** result → starting state → ordered steps → verification.
- **Checks:** step order, visibility, reproducibility, and authentic screen/action capture.

### 14.6 News Commentary

- **Profile:** Commentary; mid-length; news; serious; build trust/inform.
- **Defaults:** footage-led, Match ceiling for factual footage, Light effects.
- **Beats:** context → sourced material → analysis → conclusion.
- **Checks:** source, date, context, authenticity, quotation boundaries, and fact/opinion separation.

### 14.7 Comedy Skit

- **Profile:** Comedy; usually 30s; fiction/lifestyle; humorous; entertain.
- **Defaults:** character-story, AI-created only for eligible fiction, Standard effects.
- **Beats:** setup → escalation → punchline.
- **Checks:** timing, originality, character continuity, likeness/IP, and disclosure when realistic.

### 14.8 Animated Story

- **Profile:** Story; usually 60s; fiction; emotional; evoke emotion.
- **Defaults:** character-story, AI-created where eligible, Standard effects.
- **Beats:** character hook → conflict → resolution.
- **Checks:** character appearance, props, voice, location, and scene continuity.

### 14.9 Trivia List

- **Profile:** List structure with explainer/comedy form; usually 30s; sports/pop culture; humorous;
  engage.
- **Defaults:** narrated-visual, AI-enhance ceiling, Standard effects.
- **Beats:** hook → items → reveal/question.
- **Checks:** facts, source dates, identifiable elements, rights, and episode variation.

### 14.10 Seamless Loop

- **Profile:** Loop structure; usually 15s; lifestyle/visual concept; calm or playful; engage/replay.
- **Defaults:** Loop lane, AI-enhance ceiling, Light effects.
- **Beats:** opening state → visual/audio change → return to opening.
- **Checks:** play twice; verify no visible or audible seam.

### 14.11 History Mystery

- **Profile:** Story/explainer; long-form; history; dramatic; bring viewers back.
- **Defaults:** narrated-visual, AI-enhance for eligible stock restyling, Standard effects.
- **Beats:** mystery → evidence → competing explanations → reveal/remaining uncertainty.
- **Checks:** archive sources, uncertainty, and clear reconstruction labels.

---

## 15. End-to-end production approach

### Phase 1 — Intake and feasibility

1. Capture deliverables, purpose-neutral classification, audience, platform, assets, budget, deadline,
   and review availability.
2. Validate mandatory fields and incompatible combinations.
3. Select candidate recipe(s), evaluate rules, and estimate effort/cost/time.
4. Present feasibility, uncertainties, and fallback options.

### Phase 2 — Grounding and creative plan

1. Create required grounding bundles.
2. Draft hook, script/story, beats, segments, shots, slots, captions, narration, ending, and metrics.
3. Assign exactness, treatment ceiling, source plan, cost, reviewer, and fallback to each slot.
4. Record recipe/rule trace and obtain Approval 1.

### Phase 3 — Sourcing and preparation

1. Search owned assets first.
2. Search permitted official APIs and approved manual sources.
3. Apply rights eligibility before visual ranking.
4. Prepare deterministic crops, cutouts, grades, diagrams, and composites without overwriting originals.
5. Generate only approved unresolved still slots within reservations.
6. Present candidate evidence and obtain Approval 2.

### Phase 4 — Editable composition and animatic

1. Build editable layouts, keeping exact overlays separate.
2. Assemble proxy timing with captions, narration, music direction, and transitions.
3. Validate pacing, readability, continuity, claims, context, rights, and deadline feasibility.
4. Identify only shots that genuinely require generated motion.
5. Obtain Approval 3 with exact provider/attempt/seconds/cost limits.

### Phase 5 — Final production

1. Submit controller-authorized jobs with idempotency and reserved budgets.
2. Poll/reconcile jobs; never blind-resubmit unknown submissions.
3. Evaluate takes against the shot/slot contract.
4. Add deterministic exact text, logos, values, citations, captions, and disclosures.
5. Mix narration, music, and effects; render a final candidate.

### Phase 6 — QA, approval, and archive

1. Run shared technical QA and all triggered conditional checks.
2. Reconcile spend, attempts, obligations, and deadline decisions.
3. Obtain Approval 4 for the exact output hash.
4. Archive final output, editable references, manifests, captions, audio, evidence, licences, costs,
   rule trace, approvals, and reproduction instructions.
5. Publishing remains a separate explicitly authorized action.

---

## 16. Budget behavior

Maintain:

```text
hard limit
  - confirmed spend
  - active reservations
  = available budget
```

Also track forecast final cost, contingency, per-stage allocation, generated seconds, attempts, and
operator-effort target.

Before any paid call:

1. validate provider and operation permission;
2. validate approval and slot eligibility;
3. reserve the maximum expected cost transactionally;
4. persist the job and idempotency key; and
5. only then submit through the adapter.

On completion, convert actual cost to spend and release the remainder. A creative rejection is not a
free technical retry. Unknown submissions retain their reservation until reconciled.

When budget pressure occurs, offer explicit alternatives:

- owned/stock asset instead of generation;
- still motion instead of generated video;
- simpler scene or effect;
- fewer generated beats;
- revised scope;
- increased budget with approval.

Never silently exceed the cap or reduce factual/product accuracy.

---

## 17. Deadline behavior

Store target/hard deadline, timezone, human review windows, QA buffer, task estimates, and priority
order. Schedule backward from delivery and expose the critical path.

Status values:

- `on_track`;
- `at_risk`;
- `blocked`;
- `missed`.

When a proposed job threatens the latest safe start, present approved fallbacks before submission:

- generated motion → still with deterministic movement;
- new generation → eligible stock;
- complex action → simpler readable scene;
- full animation → diagram/typography;
- reviewed proxy by deadline → final later, only if the requester approves split delivery.

Deadline priority never authorizes unsupported claims, wrong products, unclear rights, undisclosed
synthetic factual footage, or skipped final approval.

---

## 18. Quality assurance

### 18.1 Shared technical QA

- expected dimensions, aspect ratio, duration, frame rate, codec, and readability;
- missing/corrupt assets;
- black, frozen, duplicated, or unexpectedly blank frames;
- audio clipping, silence, loudness, and channel issues;
- captions within safe areas and synchronized with speech;
- text legibility, spelling, language, and required wording;
- flashing and other known accessibility risks;
- current approval and dependency versions;
- content, source-manifest, and output hashes.

### 18.2 Source-bound/informational QA

- every claim and figure maps to approved evidence;
- narration, captions, diagrams, and on-screen numbers agree;
- uncertainty and attribution are not lost in editing;
- historical reconstruction and synthetic illustration are labelled as required.

### 18.3 Tutorial QA

- starting state and expected result are visible;
- steps are complete, ordered, and reproducible;
- zooms/captions do not obscure the action;
- software/product workflow matches the current approved version.

### 18.4 Commentary/news QA

- source, date, context, quotation boundaries, and authenticity are intact;
- fact, inference, and opinion remain distinct;
- satire/reconstruction is visibly separated;
- no realistic synthetic event footage appears as authentic evidence.

### 18.5 Entertainment/fiction QA

- story coherence and payoff;
- character appearance, voice, relationships, props, and location continuity;
- originality, likeness, trademark, and entertainment-property review;
- realism/disclosure requirements.

### 18.6 Product/commercial QA

- exact product, variant, label, logo, price, currency, offer, CTA, and expiry;
- supported benefit and feature claims;
- authentic demonstration where required;
- brand and sponsor/advertising disclosure compliance.

### 18.7 Rights and platform QA

- each selected asset remains linked to its immutable original and licence evidence;
- attribution and share-alike obligations are delivered;
- no editorial/noncommercial asset drifted into commercial use;
- platform/audience requirements, especially for kids and ads, are satisfied.

---

## 19. Revision and invalidation behavior

Revisions should invalidate only dependent work.

Examples:

- changed price → invalidate price overlays, related narration/captions, product claims, affected
  renders, and approvals; preserve unrelated backgrounds;
- changed factual claim → invalidate dependent script lines, diagrams, captions, shots, and QA;
- changed source context → invalidate excerpts/commentary that depend on it;
- changed character design → invalidate scenes using that character; preserve unrelated environments;
- replaced shot → rebuild that shot and downstream renders; preserve approved shots;
- changed platform/destination → re-evaluate format, safe areas, rights, attribution, disclosure, and
  audience gates.

Every approved plan, bundle, artifact, and output should declare dependencies by stable ID and hash.

---

## 20. Agent operating guide

### 20.1 Roles

Agents may specialize, but all return proposals to the controller:

- **Brief classifier:** normalizes intake and identifies missing information.
- **Planner:** selects recipe, applies rules, and proposes segments/shots/slots, cost, schedule, and
  fallbacks.
- **Research/grounding agent:** proposes claims, evidence, source context, and uncertainty.
- **Script/story agent:** writes within the approved form, truth policy, tone, and grounding.
- **Sourcing agent:** searches approved sources and returns normalized candidates; it does not approve
  rights or import files directly.
- **Visual/layout agent:** proposes editable composition within slot and treatment policies.
- **Generation coordinator:** proposes exact unresolved jobs, references, attempts, cost, and fallback;
  the controller submits authorized jobs.
- **Audio/caption agent:** proposes narration, voice, music, effects, captions, and timing.
- **QA agent:** evaluates the exact candidate against all triggered checks and reports evidence.

### 20.2 Required agent behavior

Every agent MUST:

1. read the current project profile, recipe/rule trace, grounding versions, approvals, and scope;
2. use stable project/segment/shot/slot IDs;
3. distinguish observed inputs, derived decisions, proposals, and uncertainties;
4. cite the bundle/artifact/source IDs supporting a claim or decision;
5. remain within treatment, exactness, provider, budget, deadline, and approval constraints;
6. provide alternatives and fallbacks when blocked;
7. report estimated cost/time and actual cost when known;
8. avoid secrets in prompts, logs, outputs, fixtures, and records;
9. avoid direct paid-provider, download, publishing, or state-changing calls; and
10. stop at required human gates.

Agents MUST NOT:

- treat a recipe as authorization;
- invent facts, prices, offers, citations, licences, or approval;
- hide uncertainty to improve a candidate score;
- select a visually stronger but rights-ineligible asset;
- overwrite an original;
- bypass idempotency, budget reservation, or deadline risk policy;
- depict synthetic factual footage as authentic;
- collapse factual and satirical segments when separation is required;
- silently substitute a different product, character, provider, or output; or
- mark their own consequential proposal approved.

### 20.3 Standard agent result

Conceptual contract:

```json
{
  "schema_version": "creative-agent-result/v2",
  "status": "completed | needs_revision | needs_human_input | failed",
  "role": "planner",
  "project_id": "prj_...",
  "scope_ids": ["segment-01", "shot-03"],
  "inputs_used": [
    {"entity_type": "grounding_bundle", "entity_id": "...", "hash": "..."}
  ],
  "proposed_decision": {},
  "matched_rule_ids": ["CR-07", "CR-12"],
  "artifacts": [],
  "alternatives": [],
  "evidence": [],
  "uncertainties": [],
  "estimated_cost": {"amount": "0.00", "currency": "INR"},
  "estimated_duration_seconds": 0,
  "deadline_effect": "on_track",
  "next_action": "human review"
}
```

This is a product-level target contract, not yet the exact implemented schema.

### 20.4 Planner procedure

The Planner should follow this deterministic order:

1. Validate mandatory intake and list missing fields.
2. Normalize legacy labels into canonical form/structure/outcomes.
3. Identify all applicable subjects, truth modes, usage contexts, and product roles.
4. Select the closest recipe and record its version.
5. Apply explicit brief choices.
6. Evaluate all matching rules and resolve stricter constraints.
7. Determine grounding bundles and reviewers.
8. Propose segments, beats, shots, and slots.
9. Assign exactness, source policy, treatment, cost, duration, fallback, and checks per slot.
10. Forecast budget and deadline, including contingency and latest safe starts.
11. Return a reviewable plan and uncertainties; do not mutate state.

---

## 21. Classification examples

### 21.1 Informative turbulence reel

```text
Deliverable:       30s, vertical 9:16 video
Product role:      none
Form:              explainer
Structure:         linear
Subject:           science
Tone:              friendly
Primary outcome:   inform
Truth:             source_bound
Usage:             commercial if monetized, otherwise declared noncommercial
Derived lane:      narrated_visual
Risk:              medium if claims/safety advice require it
Ceiling:           ai_enhance
Grounding:         claim_evidence
```

Use sourced claims, simple airflow diagrams, eligible footage/stills, narration, captions, and a
takeaway. No product fact set is required.

### 21.2 Sponsored humorous budgeting explainer

```text
Deliverable:       30s vertical video
Product role:      cameo
Form:              explainer
Structure:         list
Subject:           money, tech
Tone:              humorous
Primary outcome:   inform
Secondary:         build_trust, sell
Truth:             source_bound
Usage:             commercial, monetized, sponsored
Derived lane:      narrated_visual
Risk:              at least medium
Ceiling:           ai_enhance globally; exact product/claim slots
Grounding:         claim_evidence, product_facts, brand_pack, platform_policy
```

Humour can affect examples and edit timing, but not figures, sponsor disclosure, app depiction, or
unsupported financial claims.

### 21.3 Entertainment comedy skit

```text
Deliverable:       30s vertical video
Product role:      none
Form:              comedy
Structure:         linear
Subject:           fiction
Tone:              humorous
Primary outcome:   entertain
Truth:             fictional
Usage:             commercial if monetized
Derived lane:      character_story
Risk:              low unless likeness/IP/realistic synthesis raises it
Ceiling:           ai_created for eligible fictional slots
Grounding:         story_bible; series_bible when recurring
```

No factual claim bundle is mandatory, but continuity, originality, rights, and realistic-synthetic
disclosure still apply.

### 21.4 News satire hybrid

```text
Deliverable:       mid-length horizontal video
Product role:      none
Form:              commentary + comedy treatment
Structure:         chapters
Subject:           news
Tone:              serious with humorous satire segment
Primary outcome:   inform
Secondary:         entertain, build_trust
Truth:             source_bound and satire in separate segments
Usage:             editorial or declared commercial/monetized context
Derived lanes:     footage_led + narrated_visual/character_story
Risk:              high where real people/events are involved
```

Authentic footage and commentary must remain distinct from visibly stylised satire. Realistic
synthetic event footage is blocked.

---

## 22. Current implementation mapping

### Already implemented

- controller-owned state transitions and approvals;
- SQLite records and append-only audit events;
- versioned fact sets and dependency invalidation;
- exact budget reservation, idempotent jobs, settlement, and recovery;
- immutable original storage, hashes, artifact lineage, and no-overwrite policy;
- provider adapter boundary and deterministic mock adapter;
- normalized external-asset rights manifests and eligibility gate; and
- provider-specific sourcing policy for Pexels, Openverse, Unsplash, Mixkit, and Videezy.

### Not yet implemented

- the purpose-neutral creative profile in this note;
- multiple deliverables;
- duration above the current five-minute model limit;
- normalized form/structure/outcome/truth variables;
- versioned recipe and rule engine;
- grounding bundles beyond the legacy fact-set model;
- segments and per-segment truth/realism policy;
- typed treatment, exactness, risk, realism, effort, and conditional gates;
- stage budget allocations and provider pricing;
- deadline/timezone/critical-path/fallback scheduling;
- final-output attribution/disclosure automation; and
- real stock, media, design, voice, generation, or publishing adapters.

Agents must not claim these deferred capabilities are active merely because this guide describes
them.

---

## 23. Recommended migration plan

### Phase A — vocabulary and fixtures

1. Approve canonical fields, values, compatibility aliases, and rule precedence.
2. Freeze this guide as `creative-classification/v1` with a content hash.
3. Convert CR-01 through CR-15 and every ready recipe into table-driven fixtures.
4. Add reference profiles for product promotion, informative content, tutorial, commentary/news,
   entertainment, long-form, series, and hybrid satire.

### Phase B — additive schema v2

Add, without destroying legacy data:

- `creative_profiles` — versioned normalized intake and derived rule trace;
- `deliverables` — format, destination, and output policy;
- `grounding_bundles` — typed product/claim/tutorial/source/story/series/brand/platform data;
- `segments` — truth, realism, treatment, disclosure, and grounding dependencies;
- typed shot/slot policy fields or versioned policy JSON; and
- recipe/rule version and decision-trace records.

Legacy mapping for migration:

- `ContentMode.PRODUCT` → requires human normalization of product role, form, outcomes, and usage;
- `ContentMode.ENTERTAINMENT` → likely entertain/fictional, but must not be auto-assumed;
- `ContentMode.STILL` → deliverable `output_kind=still`, not a purpose;
- legacy fact set → grounding bundle after review (`product_facts` or `claim_evidence`);
- `FACTS_LOCKED` → compatibility projection of `GROUNDING_LOCKED` during migration.

Use dual-read/dual-write only for a bounded migration period and record the schema/profile version.

### Phase C — deterministic rule engine

Implement pure functions that:

- validate intake;
- normalize aliases;
- derive length band;
- match recipe and rules;
- resolve strictest risk/treatment/exactness;
- emit required bundles, gates, QA, budget/deadline inputs, and fallbacks; and
- return a complete decision trace.

The controller validates and persists the result; agents only propose intake or plans.

### Phase D — policy-aware approvals and invalidation

- make later approvals depend on triggered grounding bundles rather than one universal fact set;
- invalidate only dependent segments/shots/slots/artifacts;
- add conditional specialist, platform, realism, and disclosure gates; and
- retain exact final-hash approval.

### Phase E — production integrations

After schema/rules pass:

1. controlled Openverse, Unsplash, and eligible Pexels connection spikes;
2. governed Mixkit/Videezy manual-import exercises;
3. FFmpeg/ffprobe and KinoCut fallback validation;
4. Penpot editable-layout spike;
5. bounded still generation and voice adapters; and
6. first pilots across informative, entertainment, tutorial, and product profiles.

---

## 24. Required tests for the v2 implementation

- exact 480-second boundary routes to Long-form;
- still is an output kind, not intent;
- sell + tutorial/explainer/story preserves exact product/offer slots;
- money/health risk is at least Medium with designated review;
- news/commentary blocks realistic synthetic event footage;
- news satire creates separate authentic and satire segments;
- kids requires destination/audience review;
- realistic synthetic fiction triggers realism/disclosure review;
- sports/pop-culture generation requires identifiable-element rights review;
- no-assets brief still enforces exactness and treatment ceilings;
- noncommercial/editorial assets cannot enter a commercial/monetized use;
- product-free fiction can advance without a product fact set;
- changed claim invalidates only dependent script/caption/diagram/render work;
- changed character guide invalidates only affected scenes;
- generation cannot exceed project/shot attempts, spend, or generated-seconds caps;
- deadline pressure presents fallbacks instead of silently skipping gates;
- every recipe stores version and matched rule IDs; and
- final approval is bound to the exact output hash and current dependencies.

---

## 25. Definition of done for a creative project

A creative is complete only when:

- classification and deliverables are approved;
- recipe/rule trace is stored and reproducible;
- all required grounding bundles are current and approved;
- every mandatory shot and slot has source, exactness, treatment, fallback, and checks;
- all selected external assets are rights-eligible with evidence;
- paid jobs are authorized, reserved, reconciled, and within cap;
- all base and conditional QA checks pass or have explicit disposition;
- required credits, disclosures, captions, and source context are delivered;
- the exact final output hash is approved;
- costs, deadline decisions, manifests, lineage, and approvals are archived; and
- publishing, if requested, receives separate authorization.

---

## 26. Quick agent checklist

Before proposing work, ask:

1. What are the deliverables and destinations?
2. What are the product role, form, structure, subjects, tones, and outcomes?
3. What is the truth policy per segment?
4. Is the use commercial, monetized, sponsored, or editorial?
5. Which recipe is closest and which rules match?
6. What grounding bundles and reviewers are required?
7. Which slots are exact, faithful, or generic?
8. What sources and treatment are permitted per slot?
9. What are the budget, attempts, generated seconds, deadline, QA buffer, and fallback priorities?
10. What approval is required next, and what exact evidence should the reviewer see?

If any answer is missing and affects safety, truth, rights, identity, budget, deadline, or approval,
return `needs_human_input` rather than guessing.
