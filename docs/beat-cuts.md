# Beat detection and beat cuts

## Detect

```powershell
python -m pinaka --home workspace detect-beats --audio track.mp3 --output beats.json
```

Decoding is local FFmpeg; analysis is numpy-only (spectral flux, autocorrelation, onset peaks). No
network call, no model download. The result carries `bpm`, `beats`, `duration_seconds` and a
`confidence` ratio.

## Cut on the beat

```powershell
python -m pinaka --home workspace assemble-film <project-id> --beat-grid beats.json --beat-tolerance 0.08
```

`assemble_film` snaps each cumulative cut boundary to the nearest beat within the tolerance and
records `beat_quantization` on the render artifact (adjusted durations, shifts, snapped count).
Segments outside the window keep their planned duration.

**Caveat:** this changes shot durations. Use it for music-led edits; a narrated film's cuts should
follow the voice, and narration is not re-timed.

## Song fit

`plan_song_fit` returns a trim or loop decision with beat-aligned out-points and a fade length. It is
a planner; the mux path does not consume it yet.

## Limits

- The detector is simple by design. Dense mixes, rubato and spoken word need an optional stronger
  tracker (librosa or aubio) behind the same interface.
- Beat extraction is not run inside the assembly command; pass a grid explicitly.
