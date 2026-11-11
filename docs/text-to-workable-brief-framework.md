# Text-to-Workable-Brief Framework

**Status:** Operating framework

**Version:** 1.0

**Date:** 11 November 2026

**Audience:** Creative planners, operators, reviewers, engineers, and production agents

## 1. Purpose and authority

This framework turns incomplete natural-language input into an auditable, production-ready brief. It
uses the classification, combination rules, and production guidance in
[`creative-classification-and-production-guide.md`](creative-classification-and-production-guide.md),
while minimizing unnecessary interruptions for the person requesting the work.

The operating rule is:

> Safely assume, record, and proceed for reversible creative and technical decisions. Ask only for a
> decision that cannot be safely delegated, and pause only the work that depends on it.

This framework does not weaken the enforcement boundary:

- agents propose decisions;
- `CreativeController` validates and atomically records every project state change;
- rights, identity, claims, budget, provider, safety, final-output, and publication gates still apply;
- paid work requires a controller-authorized, transactionally reserved operation;
- final approval and publication authorization remain separate; and
- originals are preserved, while derivatives receive new paths, hashes, lineage, and receipts.

If this document conflicts with an implemented controller rule, follow the stricter controller rule
and report the difference.

## 2. Definition of a workable brief

A workable brief is more than a description of the desired output. It is a versioned package from
which the controller and production tools can determine:

1. what the person explicitly requested;
2. what was derived from that request;
3. which safe defaults and creative assumptions were applied;
4. which decisions remain non-delegable;
5. what must appear and be heard in every beat or shot;
6. which primary and backup assets are required;
7. which rights, treatment, budget, and provider rules apply;
8. how success will be measured; and
9. who must approve the consequential outputs.

A brief is not workable if the script exists but the visual requirements, asset requirements, or
acceptance criteria are absent.

## 3. Decision provenance

Every normalized value must carry one of these states. The state describes provenance; it does not
replace controller approval.

| State | Meaning | May production continue? |
|---|---|---|
| `supplied` | Explicitly present in the original input or an attached approved source | Yes |
| `derived` | Deterministically follows from supplied information or a versioned rule | Yes |
| `defaulted` | A safe technical or policy default was applied | Yes |
| `assumed` | A reversible, low-risk creative decision was selected using best practice | Yes, provisionally |
| `approved` | The authorized reviewer accepted the value for the recorded scope/version | Yes |
| `blocked` | The decision cannot be safely delegated and no authority has supplied it | Only unaffected work |

An assumption record should contain:

```json
{
  "field": "visual.background",
  "value": "simple stylised pond",
  "basis": "The script describes ducks swimming in a row",
  "state": "assumed",
  "risk": "low",
  "reversible": true,
  "alternatives_considered": [
    "abstract blue environment",
    "paper-cut lake"
  ],
  "affected_shots": ["shot-01", "shot-02"],
  "review_gate": "animatic"
}
```

Never present `derived`, `defaulted`, or `assumed` information as if the person supplied it.

## 4. The autonomy test

The planner may decide and continue without waiting when **all** of the following are true:

- the choice is reversible without invalidating protected originals;
- no unapproved spend or paid-provider activation is created;
- no fact, claim, price, offer, or legal statement is invented;
- no uncertain licence, consent, or ownership is accepted;
- no recognizable person, protected character, logo, or exact product identity is fabricated;
- no safety or platform restriction is weakened;
- no publication is authorized;
- a reasonable production default or creative best practice exists; and
- the choice and its basis can be recorded for later review.

If any condition fails, mark the decision `blocked`. Continue every unaffected planning and
production task instead of stopping the entire project.

## 5. Decisions that should be processed automatically

The following are normally safe to derive, default, or assume, subject to the autonomy test:

| Area | Normal automatic action |
|---|---|
| Classification | Derive product role, family, subject, outcome, source readiness, risk, and likely lane |
| Structure | Derive beats, shot count, and pacing from message, script, and duration |
| Visual treatment | Select an audience-appropriate style, environment, palette, framing, and motion language |
| Characters | Propose original, generic character designs without protected likenesses or marks |
| Background and props | Add only what is necessary to communicate the action and maintain continuity |
| Text | Keep it editable; minimize it when the audience or platform makes reading unsuitable |
| Assets | Create mandatory slots and route owned/approved material before stock or generation |
| Coverage | Require a primary and backup for each mandatory visual or audio slot |
| Audio | Derive voice delivery, music character, tempo, ambience, and mix from audience and tone |
| Technical profile | Apply resolution, frame rate, codecs, safe areas, captions, naming, and hash conventions |
| QA | Apply content, accessibility, rights, technical, platform, and subject-specific checks |

These decisions may advance through a draft or animatic without an interruption. Consequential
assumptions are presented together at the next meaningful review gate rather than as a series of
small questions.

## 6. Decisions that cannot be safely assumed

Only dependent work should wait for these decisions.

### 6.1 Rights, identity, and consent

- ownership or licence status of supplied material;
- permission to use a recognizable person, a real child, a protected character, logo, or trademark;
- acceptance of a licence obligation not already allowed by policy; and
- an exact product, SKU, package, or brand reference that is required but missing.

### 6.2 Facts and claims

- factual, health, financial, safety, developmental, or performance claims without approved grounding;
- prices, offers, availability, dates, and legally required wording; and
- any statement that cannot be verified from an approved source.

### 6.3 Money and provider authority

- increasing a project or provider budget;
- activating an unapproved paid provider, model, or endpoint;
- exceeding a per-operation limit; and
- retrying an uncertain paid submission when the original may have incurred a charge.

### 6.4 External commitments

- publication authorization;
- account, credential, or external-access decisions;
- final approval of an exact output hash when human approval is required; and
- disclosures or legal acceptance that require an authorized person.

### 6.5 Fundamental ambiguity

Ask only when the available input cannot reasonably establish what is being created, its central
objective, its intended audience, or an exact protected subject that must be represented. If a
platform is unspecified, produce a neutral review master where possible and block only
platform-specific packaging or publication.

## 7. Partial-blocking and safe degradation

A missing decision blocks the smallest affected unit.

| Missing decision | Continue | Pause or substitute |
|---|---|---|
| Exact product photograph | Script, setting, audio, timing, and unrelated shots | Product slot until an approved reference exists |
| Music rights evidence | Visuals, narration, and a temporary silent review | Final use of that track; evaluate an eligible backup |
| Destination platform | Platform-neutral review master | Platform-specific packaging and publication |
| Publication approval | Planning, production, QA, archive, and delivery packet | Actual publication |
| Budget increase | Local, owned, stock, and zero-spend work | Paid generation beyond the approved cap |
| Background or colour direction | Assume a coherent, accessible treatment and record it | Nothing, unless it creates a protected identity issue |
| Unverified claim | Rewrite the affected beat without the claim where possible | Claim-bearing version |

When safe, degrade to a generic, fictional, or neutral alternative rather than blocking the whole
creative. Do not silently replace an exact product, person, fact, or protected identity.

## 8. End-to-end workflow

```text
Raw text and attachments
  -> preserve original input and hash
  -> extract supplied facts and constraints
  -> classify using versioned rules
  -> audit completeness
       -> safely assumable: record and continue
       -> non-delegable: block only dependent work
  -> generate creative treatment
  -> write or normalize script and timed beats
  -> derive scene and shot plan
  -> create visual and audio asset slots
  -> route primary and backup sourcing
  -> assemble editable storyboard/animatic
  -> review consequential assumptions as one package
  -> produce candidate
  -> run content, rights, accessibility, and technical QA
  -> approve exact hash
  -> archive
  -> authorize publication separately, if requested
```

### Gate 0 — Preserve source input

Retain the original text unchanged. Record its source, time, author when known, attachments, and
content hash. Every normalized brief is a derivative of this record.

### Gate 1 — Extract supplied facts

Extract without embellishment:

- objective and intended outcome;
- audience, language, and destination when provided;
- output type, orientation, and duration;
- subject, product role, message, and CTA;
- tone and explicit creative references;
- supplied assets and rights statements;
- budget, deadline, and provider restrictions; and
- named reviewers and approval constraints.

Unknown fields remain unknown until classified as safely assumable or non-delegable.

### Gate 2 — Classify

Record the guide's core variables:

1. product role;
2. content family;
3. format and length;
4. subject;
5. tone; and
6. intended outcome.

Then record applicable conditional variables: quality tier, one-off or series, product complexity,
asset readiness, and source material. Derive the production lane, risk, realism, treatment ceiling,
effects level, exactness, effort, cost cap, gates, ending, and primary metric. Store the rule or
reason that produced every derived value.

### Gate 3 — Resolve incompleteness

Classify each missing field as:

1. safely derivable;
2. safe technical default;
3. safe creative assumption;
4. optional and unnecessary; or
5. non-delegable and blocked.

Do not ask for optional or safely assumable information. If questions are necessary, consolidate
them into one decision request with the recommended choice, alternatives, reason, and exact work
that is blocked.

### Gate 4 — Create the treatment

The treatment should define enough of the creative world to plan shots and assets:

- one-sentence concept and single message;
- intended audience experience;
- visual style and realism;
- world, location, and background system;
- subjects or characters, appearance, and continuity;
- required props and supporting objects;
- colour, lighting, composition, camera, and motion language;
- text and caption policy;
- audio character;
- ending or payoff; and
- prohibited elements.

For a recurring series, create or reference a versioned character/world/format guide.

### Gate 5 — Script and timed beats

Every beat must record:

- stable ID, purpose, start, end, and exact duration;
- narration, dialogue, or on-screen copy;
- required visual action and subject;
- background state and required props;
- motion, transition, and audio cue; and
- constraints and prohibited content.

Durations must sum exactly to the deliverable duration. A narration-only beat is not visually
complete; the planner must derive or assume its visual requirement.

### Gate 6 — Scene and shot plan

Each shot must record:

| Field | Required decision |
|---|---|
| Purpose and timing | What the shot communicates and its exact start/end |
| Background/location | The environment and continuity state |
| Subject/character | Identity, appearance, count, action, and position |
| Props/objects | Every supporting element required for comprehension or continuity |
| Framing/crop | Composition and safe-area treatment |
| Camera and motion | Still, move, subject action, and motion ceiling |
| Text | Editable content and position, or explicitly none |
| Transition | Entry, exit, cut, dissolve, match, or loop behavior |
| Audio cue | Narration, music, ambience, and effects timing |
| Source and exactness | Owned, stock, composed, generated; exact, faithful, or generic |
| Treatment | As-is, match, effects, AI-enhance, composite, or AI-created |
| Review and fallback | Reviewer, primary approach, and fallback |

### Gate 7 — Asset-slot and rights plan

Convert every visible and audible requirement into a governed slot. Each mandatory slot records:

- semantic role and affected shots;
- required content and technical specification;
- source method and provider restrictions;
- exactness and treatment ceiling;
- rights and evidence requirements;
- primary and backup assets;
- optional, genuinely distinct A/B variant;
- parentage, dependencies, and expected lineage; and
- reviewer decision.

Use this routing order unless a stricter rule applies:

```text
owned or already approved
  -> eligible stock with evidence
  -> deterministic composition or edit
  -> eligible local generation
  -> approved paid generation within a reserved budget
  -> request exact real material for protected slots
```

### Gate 8 — Audio plan

Record voice language, type, delivery, speed, script hash, and restrictions. Record music mood,
tempo, instrumentation, duration, loop behavior, source, licence, primary, and backup. Record sound
effects and ambience with timing and provenance. Define loudness, peak, narration intelligibility,
ducking, silence, and clipping checks.

### Gate 9 — Acceptance and QA

Define measurable acceptance criteria before final production:

- message and beat comprehension;
- visual correspondence to the script;
- character, background, prop, and world continuity;
- exact copy and prohibited-content checks;
- duration, resolution, aspect ratio, frame rate, codecs, and audio format;
- safe areas, contrast, captions, flash rate, loudness, and true peak;
- rights evidence, attribution, and treatment compliance;
- provider receipts and budget reconciliation;
- primary/backup slot coverage;
- artifact hashes and lineage; and
- exact-hash final approval.

## 9. Question policy

The system should not conduct a long interview merely because the initial text is incomplete.

When a non-delegable question is required:

1. ask only for the minimum decision needed;
2. consolidate related blockers into one review packet;
3. lead with the recommended safe option;
4. state why the system cannot assume it;
5. state exactly which work is blocked;
6. continue unaffected work; and
7. save the answer verbatim with its scope and revision.

Routine assumptions should be reviewed together at the treatment or animatic checkpoint. A request
to revise an assumption invalidates only dependent shots, slots, or artifacts.

## 10. Workable-brief package

The completed planning package should contain:

```text
01-original-input
02-normalized-brief
03-classification-and-triggered-rules
04-assumption-ledger
05-blocked-decision-register
06-creative-treatment
07-script-and-timed-beats
08-scene-and-shot-plan
09-asset-slot-and-rights-plan
10-audio-plan
11-technical-and-qa-specification
12-approval-record
```

At minimum, the normalized brief should expose this shape:

```yaml
source:
  input_artifact: "..."
  input_hash: "..."

project:
  objective: "..."
  audience: "..."
  destination: "..."
  deliverable: "..."
  duration_seconds: 0
  orientation: "..."
  budget: "..."

classification:
  product_role: "..."
  content_family: "..."
  subject: "..."
  tone: "..."
  intended_outcome: "..."
  quality_tier: "..."
  asset_readiness: "..."
  source_material: "..."
  production_lane: "..."
  risk: "..."
  realism: "..."
  treatment_ceiling: "..."

treatment:
  concept: "..."
  visual_style: "..."
  world: "..."
  characters: []
  props: []
  palette: "..."
  camera: "..."
  motion: "..."
  audio_character: "..."
  prohibited_elements: []

beats: []
shots: []
asset_slots: []
audio_plan: {}
acceptance: {}
assumptions: []
blocked_decisions: []
approvals: []
```

The exact persisted schema remains controller-owned. This shape describes required planning
information rather than authorizing agents to write project state directly.

## 11. Worked example: incomplete kids-rhyme input

Raw input:

> Make a five-second counting rhyme for children aged four to six: “One, two, three little ducks,
> swimming in a row. Quack, quack, quack!”

Initial extraction:

| Field | Value | State |
|---|---|---|
| Duration | Five seconds | `supplied` |
| Audience | Children aged four to six | `supplied` |
| Learning goal | Count from one to three | `derived` |
| Product role | None | `derived` |
| Subject | Kids | `derived` |
| Source material | Idea only | `derived` |
| Spoken copy | Supplied rhyme | `supplied` |
| Background | Not supplied | Eligible for assumption |
| Character design | Not supplied | Eligible for assumption |
| Props | Not supplied | Eligible for assumption |
| Visual style | Not supplied | Eligible for assumption |

The planner may proceed with a recorded low-risk treatment: clearly stylised original ducklings, a
simple pond environment, restrained supporting props, gentle motion, no reading dependency, and a
warm synthetic voice. These are assumptions, not facts supplied by the person. The planner derives
timed beats, shot requirements, and visual/audio slots; sources primary and backup assets; and builds
an animatic for bundled review.

The planner must stop only if the treatment calls for a protected character, a recognizable child,
an asset with unclear rights, a claim, unapproved paid generation, or publication. A missing colour,
background, prop, camera angle, or transition is not by itself a reason to interrupt the person.

## 12. Readiness checklist

A brief is ready for governed production when:

- [ ] the original input is preserved and hashed;
- [ ] supplied facts are separated from derived/defaulted/assumed values;
- [ ] classification and triggered rules are recorded;
- [ ] no unresolved blocker affects the next production step;
- [ ] a coherent treatment defines world, subjects, background, props, style, and motion;
- [ ] the script and beat durations equal the approved runtime;
- [ ] every beat has a visual requirement;
- [ ] every mandatory visual and audio element has a slot;
- [ ] every mandatory slot has a primary and backup plan;
- [ ] rights, treatment, exactness, budget, and provider restrictions are known;
- [ ] audio and technical delivery profiles are defined;
- [ ] acceptance and QA criteria are measurable;
- [ ] consequential assumptions have a named review gate; and
- [ ] final approval and publication authorization remain separate.

## 13. Related documents

- [`creative-classification-and-production-guide.md`](creative-classification-and-production-guide.md)
- [`asset-sourcing-policy.md`](asset-sourcing-policy.md)
- [`end-to-end-flow-with-owners.md`](end-to-end-flow-with-owners.md)
- [`openmontage-controller-integration.md`](openmontage-controller-integration.md)
- [`architecture.md`](architecture.md)
