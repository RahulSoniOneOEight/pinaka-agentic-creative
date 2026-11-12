# Captions and ASR

Two caption sources exist, and they are not interchangeable.

## Plan-timed (default)

`build_srt(shots)` derives one cue stream from the shot plan. It is deterministic and needs no
model, but its timing is only as accurate as the plan. It now applies the locale rules the
localisation layer already owns:

- `line_chars` and `lines` wrap and split each cue;
- `cps` is **measured**, not "fixed" by splitting — the same words still occupy the same seconds —
  and violations are reported by `legibility_violations`;
- `build_srt(..., strict=True)` raises on the first violation instead of shipping it.

`export-captions` records `legible` and the violation list in the artifact operation and returns a
`legibility` block. Publication can gate on it.

## Speech-timed (ASR)

```powershell
python -m pinaka --home workspace transcribe-audio --audio narration.wav --output transcript.json `
  --srt captions.srt --locale en
```

`faster-whisper` is asked for `word_timestamps`; `build_srt_from_words` groups words into cues by
pause (`--max-gap`, default 0.6 s), line limits and a six-second cap, then wraps them with the same
locale profile. The transcript JSON now carries per-word timings.

## Drift gate

```powershell
python -m pinaka --home workspace caption-drift --plan plan.srt --spoken speech.srt
```

`caption_drift` matches each plan cue to the spoken cue carrying at least 80% of its tokens and
checks the start offset against a tolerance (default 0.75 s). It returns `passed`, the maximum
offset and every mismatch with a reason (`text` or `timing`).

## Limits

- Word timings require the optional `asr` extra and a model download.
- Drift is a report; it does not re-time the plan automatically.
- The spoken SRT is a separate artifact; the controller does not yet choose it automatically when
  both sources exist.
