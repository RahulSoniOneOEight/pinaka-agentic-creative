---
name: Pinaka Project
description: Operate a governed Pinaka creative project from intake through exact-hash approval and archive without bypassing the Python controller.
---

# Pinaka project operator

Use this skill for `/pinaka/*` project work in this repository.

## Enforcement boundary

- Agents and language models propose; `CreativeController` validates and commits every state change.
- Read persisted project status before proposing the next action. Do not rely on conversation history.
- Never call a paid provider outside a controller-authorized, transactionally reserved job.
- Never read or write `.local/`, and never place credentials in prompts, logs, SQLite, manifests, fixtures or Git.
- Preserve originals. Store every derivative at a new path with its hash, parentage and operation receipt.
- Publishing remains separately authorized and is not part of project production.

## Approved pilot

The archived pilot contract is `config/pilots/pinaka-virtual-cfo-10s.json`.

- Client: Pinaka Intelligence
- Output: 10 seconds, 1080×1920, 9:16, English
- Claim: Virtual CFO is explicitly human-approved for this campaign
- CTA: Start a Conversation
- Provider mode: disabled
- Maximum paid spend: INR 0.00

That archived pilot stays zero-spend. New work may use a paid route only when all of these hold:

1. the slot's approved sources include generation (`_enforce_generation_routing`);
2. a current budget plan is `within_target` or approved, with that line item still `planned`;
3. the job is reserved through the controller, one take, no auto-retry;
4. the human authorization names a ceiling.

Hosted stills/edits use `fal.image`; generated motion uses Studio `fal.video` only, one shot at a
time. Veo and Runway are refused in code. `pinaka operations` is the source of truth for routes,
funding and receipts — do not hardcode a provider policy in a prompt. Check a fal credential without
spending anything with `pinaka fal-check`.

## Standard flow

1. Validate the pilot contract.
2. Create the project with `pilot-new`.
3. Persist the deliverable/segment plan with `pilot-plan`.
4. Record human brief approval.
5. Persist typed brand, claims and source grounding with `pilot-ground`.
6. Transition to sourcing and evaluate every external asset before import.
7. Build and approve the editable animatic.
8. Authorize only declared tool operations and record terminal receipts.
9. Run content, rights, accessibility and technical QA against exact artifact hashes.
10. Record final approval with the exact render hash, then archive.

## CLI

Use `python -m pinaka --home workspace <command>` from the repository root. Useful commands:

```text
pilot-validate --contract <path>
pilot-new --contract <path>
pilot-plan <project-id> --contract <path>
status <project-id>
next <project-id>
approve <project-id> <stage> --approved-by <human> --scope-file <json>
pilot-ground <project-id> --contract <path>
produce-animatic <project-id>
qa-animatic <project-id>
capture-frames <project-id> --frames 300 --fps 30
encode-animatic <project-id>
synthesize-narration <project-id> --voice af_heart --speed 1.0
mux-voiced <project-id>
qa-voiced <project-id>
review-video --video <path> --spec <json> --output <json>
caption-video --video <path> --srt <path> --output <path>
transcribe-audio --audio <path> --output <json> --expected-file <path>
derive-formats --video <path> --output-dir <dir>
delivery-packet --master <path> --captioned <path> --captions <path> \
  --formats-manifest <path> --formats-dir <dir> --metadata <path> --output-dir <dir>
preflight <project-id>
evaluate-profile --file <profile-json> --output <decision-json>
creative-plan --file <profile-json> --output <plan-json>
record-creative-plan <project-id> --file <profile-json>
source-search --provider openverse|wikimedia|internet_archive|freesound --query <text> --media image --limit 5 --output <json>
font-inventory --root <font-dir> --output <json>
font-check --inventory <json> --licences <json> --output <report>
experiment-report --file <experiment-json> --output <report>
generate-image <project-id> --prompt <text> --aspect-ratio 9:16 [--copy-to <path>]
generate-local-image <project-id> --prompt <text> [--width 512 --height 512 --steps 12]
generate-vertex-image <project-id> --prompt <text> --model gemini-nano-banana-2.1 --max-cost-cents 5
external-qa --video <path> --output-dir <dir> [--qcli <path> --mediaconch <path>]
mirror-backup --backup <dir> --destination <dir>
publication-package <project-id> --file <publication-json> --output-dir <dir>
route-provider --file <route-request-json> --output <decision-json>
spend-reconcile --file <spend-json> --output <report-json>
retention-plan --file <items-json> --output <report-json>
publication-check <project-id> --file <publication-json> --output <plan-json>
localization-plan --srt <captions.srt> --targets hi,de --output <plan-json>
backup --output <dir>
verify-backup --backup <dir>
restore-backup --backup <dir> --target <empty-home>
restore-drill --backup <dir> --workdir <dir>
register-tool-profile --file <path>
authorize-operation <project-id> --file <path>
record-receipt <operation-id> --file <path>
archive <project-id>
events --project-id <project-id>
```

Commands are operator prompts, not authorization shortcuts. Stop when an approval, rights decision,
provider activation, spend increase, Penpot geometry/token change or publishing action is required.

Nano Banana setup, models, pricing and cost caps: `docs/nano-banana.md`. The private ADC note is
`D:\pinaka-credentials\secrets\nano-banana.md`; never copy credentials from it.

## Deterministic media gate

`produce-animatic` writes content-addressed `designs/animatic-<hash>.html`,
`artifacts/timeline-<hash>.json`, `artifacts/captions-<hash>.srt` and a
`previews/animatic-manifest-<hash>.json` bundle. `qa-animatic` verifies exact copy, prohibited claims,
brand colours, type/spacing tokens, contrast, caption coverage, rights grounding and artifact hashes.

The HTML animatic is an editable render source, not the final delivery. `capture-frames` renders a
content-addressed PNG sequence through headless Chrome; `encode-animatic` produces a silent H.264
master; `synthesize-narration` records local Kokoro model/voice evidence; `mux-voiced` performs a
two-pass loudness-normalized AAC mux; `qa-voiced` checks streams, loudness, narration fit and script
hash. `review-video` runs independent container, black/freeze, silence, scene-cut, OCR and brand-colour
checks against a finished file. `caption-video` creates a soft-subtitled derivative without re-encoding
A/V; it is a review derivative and needs a new exact-hash approval before replacing any archived master.

`transcribe-audio` verifies narration against the approved script with local faster-whisper.
`derive-formats` creates 1:1, 16:9 and 4:5 derivatives using a blurred crop of the master as the
background. `delivery-packet` assembles master, captioned file, SRT, format manifest and metadata with
hashes, but never publishes. `preflight` runs the read-only budget, grounding, approval, artifact, QA,
captions, provider and recovery rules.
Final approval still needs a human review of the exact video hash, then `archive`.
