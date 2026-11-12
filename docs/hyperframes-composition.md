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

## Dynamic subjects

Each shot declares a subject behaviour, so scenes differ instead of repeating one static image.

```json
"requirements": {
  "subject": { "action": "swim", "count": 3, "scale": 0.62 }
}
```

| Action | Behaviour |
|---|---|
| `swim` | drifts left → right with a gentle vertical dip |
| `bob` | rises and settles |
| `wave` | rotates ±6° |
| `count` | copies appear one at a time |
| `enter` | slides in from the left |
| `still` | no motion |

A shot may also supply an explicit `path` of waypoints, which overrides the built-in curve:

```json
"subject": {
  "action": "swim",
  "count": 1,
  "path": [
    { "t": 0.0, "x": 0, "y": 0 },
    { "t": 0.5, "x": -20, "y": -8 },
    { "t": 1.0, "x": 25, "y": 0 }
  ]
}
```

`t` is progress through the shot; `x` and `y` are percentages of the stage. Malformed points are
ignored rather than failing the render.

The planner assigns one action per shot (`swim`, `bob`, `count`, …) and derives `count` from the
spoken copy (“three little ducks” → 3). Multiple copies are scaled down and spaced so they read as
separate characters rather than a single blob.

Frames are **deterministic**: at `?t=` the inline script stops CSS animation and computes each
scene's opacity and each subject's transform from the timestamp, so a capture at 8.5 s always shows
the same frame.

## Captions

```powershell
pinaka export-captions <project-id>
pinaka burn-captions <project-id> --video <mp4-artifact> --captions <srt-artifact> [--hard]
```

- The SRT is derived deterministically from shot durations and narratives — no ASR needed for
  supplied copy.
- Burning attaches a soft `mov_text` track by default; `--hard` burns the text into the picture with
  a re-encode (H.264 + AAC, outlined white text, bottom margin).
- Both artifacts are hash-verified and linked as dependencies.

## Narration budget

```powershell
pinaka synthesize-narration-vertex <project-id> --script-file script.txt --reserve
```

With `--reserve`, the estimated TTS cost is reserved as a controller budget job before synthesis and
settled afterwards; a failed synthesis cancels at zero. USD projects only.

## Element → slot binding

Reusable elements carry their artwork, so a shot can be dressed without assigning files by hand.

```powershell
pinaka bind-elements <project-id>
```

```text
element (character.prop/location/world) + bound shot + matching slot
  → slot primary assignment (via the normal treatment decision)
  → composition layer
```

| Element kind | Matching slot roles |
|---|---|
| `character`, `prop` | subject, character, prop, visual, hero, presenter |
| `location`, `world` | background, world, location, environment, scene |

Rules:

- an element without artwork is skipped with a reason;
- a slot that already has a primary asset is never overwritten;
- roles that do not match are ignored quietly;
- re-running is idempotent.

Verified: two elements across three shots assigned six slot primaries with no skips, producing twelve
composition layers (three duck subjects per scene plus one background each).

## Governed finish

```powershell
pinaka qa-render <project-id> <artifact-id> --duration 10 --width 540 --height 960 --fps 10
```

QA records one result per check against the exact artifact:

| Check | Pass condition |
|---|---|
| `technical.container` | mp4 / mov / m4a |
| `technical.codec` | h264 (hevc warns) |
| `technical.duration` | within 0.25 s, warn to 1 s |
| `technical.resolution` | exact width and height |
| `technical.frame_rate` | within 0.5 fps |
| `technical.audio` | audio present when required |

A JSON report is stored as a `qa` artifact with the render as its dependency. Final approval then
requires a current artifact whose hash matches `output_hash` **and** recorded QA with no failure, so
the same gate enforces both provenance and quality.

## Verified render


Authored from the kids-rhyme plan: 3 shots, 10 seconds, valid, 3.2 KB, rendered through Chromium at
540×960 with no clipping, correct 9:16 framing, beat label and element chip visible.

Rendered end to end: 100 frames at 10 fps in 81 seconds → 10.0 s H.264 MP4, 540×960, 34 KB, no audio,
hash verified. A frame extracted at t=4 s shows the second scene with correct timing.

Full pass with artwork and narration: 12 embedded layers (pond background + three duck subjects per
shot), 100 frames at 10 fps, live Vertex TTS narration, loudness-normalized mux and a soft caption
track → 10.0 s H.264 + AAC + mov_text, verified. Inspected frames show three separated ducklings
swimming, and the third scene revealing them one at a time.

Governed finish: QA passed all six checks on the final hash, final approval bound to that exact hash,
and the project archived — 12 artifacts, one budget job, **$0.01 spent**, nothing reserved.

## Not implemented

- Caption burn-in as hard subtitles (the track is soft/selectable).
- Physics or path authoring beyond the built-in actions.
- Multi-pass render or partial re-render of a single shot.
