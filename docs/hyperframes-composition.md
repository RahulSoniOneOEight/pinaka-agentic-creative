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

## Render

```powershell
pinaka render-composition <project-id> --fps 24
pinaka render-composition <project-id> --fps 10 --width 540 --height 960
```

```text
authored composition
  → validate + read declared duration
  → frame_count = ceil(duration × fps)      (default limit 600)
  → headless Chrome captures frame-N with ?t=<seconds>
  → FFmpeg encodes H.264, yuv420p, faststart
  → renders/composition-<hash>.mp4
  → artifact depends on the composition artifact
```

- The composition freezes animation at `?t=` so every frame is deterministic.
- Frame count is capped (`max_frames`, default 600) to prevent runaway renders.
- A tampered or invalid composition is refused before capture.
- Identical content renders to the same artifact path, so re-rendering is idempotent.

## What the plan still needs

The generated source is a **scaffold**: timed scenes with beat, narrative and element labels. Real
visual layers (character artwork, backgrounds, props) are added by the render/composition step, not
invented here.

## Asset layers

Assigned slot assets are embedded into the composition as image layers, so the animatic shows the
actual artwork rather than text placeholders.

```text
slot asset assignment (primary/backup)
  → image read from the project artifact store
  → base64 data URI inside the scene
  → layer class from the slot role
```

| Slot role | Layer treatment |
|---|---|
| `background`, `world`, `location`, `environment` | full-bleed, `object-fit: cover` |
| anything else | contained subject, centred near the bottom |

Limits: 1.5 MB per layer, 4 MB embedded per composition. Oversized or non-image layers are skipped
and reported in `layers_skipped`, never silently dropped.

## Narration and mux

```powershell
pinaka synthesize-narration-vertex <project-id> --script "One, two, three…" --voice Kore
pinaka mux-narration <project-id> --video <render-artifact> --audio <audio-artifact>
```

- Narration is Vertex TTS over ADC, registered as an `audio` artifact with model, voice, character
  count, duration, text hash and an estimated cost.
- Mux normalizes loudness (two-pass) and produces a new `render` artifact that depends on both the
  silent video and the narration.
- Mux refuses a non-audio artifact or an unknown duration.

## Verified render


Authored from the kids-rhyme plan: 3 shots, 10 seconds, valid, 3.2 KB, rendered through Chromium at
540×960 with no clipping, correct 9:16 framing, beat label and element chip visible.

Rendered end to end: 100 frames at 10 fps in 81 seconds → 10.0 s H.264 MP4, 540×960, 34 KB, no audio,
hash verified. A frame extracted at t=4 s shows the second scene with correct timing.

Full pass with artwork and narration: 6 embedded layers (pond background, duck subject) across three
shots, 100 frames at 10 fps, live Vertex TTS narration (8.5 s), loudness-normalized mux → 10.0 s
H.264 + AAC, 476 KB, verified. Inspected frames show full-bleed background, the duck subject and
legible text.

## Not implemented

- Caption burn-in inside the composition.
- Character animation inside a shot (the subject is static per scene).
- Multi-pass render or partial re-render of a single shot.
