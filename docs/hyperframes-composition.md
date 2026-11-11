# HyperFrames Composition

**Status:** Implemented (authoring + validation). Rendering is not implemented.

## What it does

Turns the governed plan into one **editable** composition source: HTML, CSS and a small inline
script, stored as a project artifact and versioned by content hash.

```text
workable brief + shots + reusable elements
  → deterministic HTML/CSS composition
  → structural validation
  → scenes/composition-<hash>/index.html
  → artifact with dependencies on brief, shots and elements
```

No provider, no spend, no media binaries. The source is the deliverable that a later render step
consumes.

## Structure

| Element | Purpose |
|---|---|
| `<meta name="pinaka-schema">` | identifies the composition format |
| `.stage[data-duration]` | fixed pixel canvas, scaled to the viewport |
| `section.scene[data-shot][data-start][data-end]` | one timed scene per shot |
| `@keyframes scene-N` | deterministic fade timing derived from shot durations |
| `.element[data-element]` | reusable element chip bound to the creative element |
| inline `fit()` script | scales the canvas to any viewport without cropping |

The stage is authored at the deliverable size (default 1080×1920) and scaled with
`transform: scale()` so previews match the render exactly.

## Validation

`validate_composition` fails on:

- a missing doctype, schema marker, style block, stage or duration marker;
- zero scenes, or duplicate `data-shot` identifiers;
- any `http://` or `https://` reference — compositions must render offline.

`author-composition` refuses to register a composition that fails validation.

## CLI

```powershell
pinaka author-composition <project-id>
pinaka author-composition <project-id> --width 1920 --height 1080
pinaka author-composition <project-id> --title "Counting rhyme v1"
```

Re-running with the same inputs returns the same artifact (content-addressed path), so re-authoring
is idempotent.

## What the plan still needs

The generated source is a **scaffold**: timed scenes with beat, narrative and element labels. Real
visual layers (character artwork, backgrounds, props) are added by the render/composition step, not
invented here.

## Verified

Authored from the kids-rhyme plan: 3 shots, 10 seconds, valid, 3.2 KB, rendered through Chromium at
540×960 with no clipping, correct 9:16 framing, beat label and element chip visible.

## Not implemented

- Rendering frames to PNG or video (Chromium capture + FFmpeg encode).
- Layer placement for real assets.
- Audio track and caption burn-in inside the composition.
