# Capability closures — beat cuts and avatar

Closed on 2026-11-13 at commit `97bcaf9`. This record states exactly what is closed,
what evidence supports it, and what is deliberately **not** claimed.

## C7 — beat detection and beat cuts: closed

**Scope closed**

- Local beat detection: FFmpeg decode + numpy spectral-flux/autocorrelation tempo and
  onset peaks. No network, no model download.
- `detect-beats --audio <file> --output <json>` writes `bpm`, `beats`, `duration_seconds`,
  `confidence`.
- `assemble-film --beat-grid <json> --beat-tolerance <s>` snaps cumulative cut boundaries
  to the nearest beat inside the tolerance and records `beat_quantization` on the render
  artifact (adjusted durations, shifts, snapped count).

**Evidence**

- `tests/test_beats.py`: estimator on a synthetic 120 BPM click track, WAV decode through
  FFmpeg, snapping/no-snap cases, monotonicity, song-fit trim/loop.
- `tests/test_assembly_and_planning.py::test_assemble_film_snaps_cuts_to_a_beat_grid`:
  the controller path records the quantization on the artifact.
- CLI exercised on a real WAV: `bpm 117.45`, beats at 0.488, 0.975, 1.486, 1.974, 2.485,
  2.972, 3.483 (a 512-sample hop explains the sub-frame offset).
- Full gate at closure: 627 tests pass; ruff, format and mypy clean.

**Not claimed**

- `plan_song_fit` is a planner only; the mux path does not consume its trim/loop/fade
  decision yet.
- Beat quantization changes shot durations. It is for music-led edits; narration is not
  re-timed, so do not use it on a narrated film without re-recording.
- The detector is simple. Dense mixes, rubato and spoken word may need librosa/aubio behind
  the same `estimate_beats` interface.

## C8 — avatar / lip-sync: closed at code level

**Scope closed**

- Contracts taken from fal's published queue OpenAPI, not inferred:
  - `fal-ai/bytedance/omnihuman/v1.5`: `image_url` + `audio_url`, `resolution`
    (720p ≤60 s, 1080p ≤30 s), `turbo_mode`, optional `prompt`.
  - `fal-ai/sync-lipsync/v3`: `video_url` + `audio_url`, `sync_mode`
    (`cut_off|loop|bounce|silence|remap`).
- `avatar.generate` in the operations catalog (route `fal.avatar`, funded item, one take,
  USD only).
- `FalAvatarAdapter` with an allowlist, hashed output, data-URI inputs under 12 MB, and
  hosted-URL support.
- `avatar-generate` CLI. Likeness consent is a **hard gate**: no `--consent` means no call.
- `run_adapter_once` accepts the avatar operation without weakening the fal image checks.

**Evidence**

- `tests/test_avatar.py`: both payload schemas, input validation, adapter submit/download,
  route refusal for the wrong route, empty-consent refusal.
- `pinaka operations` lists `avatar.generate`; the adapter is the only submitter.
- Full gate at closure: 627 tests pass; ruff, format and mypy clean.

**Not claimed**

- **No live avatar call has been made.** The end-to-end provider path is unproven; the
  receipt records `cost_source: planned_estimate` because the endpoint returns no price.
- No face/likeness verification and no sync-confidence metric.
- Synthetic-media disclosure at publication is still a human act.
- Studio's avatar routes remain a separate path behind Studio admission.

## Closing a live acceptance later

The only remaining step for C8 is one funded take: a portrait with rights, ~5 s of
narration, a consent reference, a USD plan item for `fal/avatar.generate`, and an explicit
cap. That belongs in the P-series register, not in this closure.
