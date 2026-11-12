# Audio layers: open-source music, SFX and sound elements

**Status:** Implemented at zero cost. Music and sound come only from open-source or owned
sources; there is no paid music generation in the flow.

## Scope

| Layer | Role values | Placement |
|---|---|---|
| Narration | — | the whole film, loudness-normalised |
| Music bed | `primary` / `backup` on `audio_beds` | the whole film, ducked under the voice |
| Sound effect | `sfx` on `audio_cues` | `start_ms` + `duration_ms` |
| Ambience | `ambience` on `audio_cues` | `start_ms` + `duration_ms`, may `loop` |
| Stinger | `stinger` on `audio_cues` | `start_ms` + `duration_ms` |

Narration is required; everything else is optional. A film with no music records that fact and
publishes cleanly — silence is a valid, auditable choice.

## Licence policy (enforced, not advisory)

Only licences that allow **commercial use** *and* **modification** are accepted, because ducking,
trimming and mixing are derivatives. `pinaka.licensing` already maps the CC variants, so the rule is
mechanical:

| Licence | Accepted | Note |
|---|---|---|
| CC0 / Public Domain | ✅ | no obligation |
| CC-BY | ✅ | attribution required |
| CC-BY-SA | ⚠️ | allowed by the flags, but share-alike propagates into a master — avoid |
| CC-BY-ND | ❌ | mixing is a derivative |
| CC-BY-NC / NC-SA / NC-ND | ❌ | commercial use blocked |

An artifact without a recorded `AssetRightsRecord` cannot become a bed or a cue. Freesound previews
are audition-only; the original must come through the governed import or Openverse path.

## Commands

```powershell
pinaka assign-audio-bed <project> --file bed.json --decided-by <email>
pinaka assign-audio-cue <project> --file cue.json
pinaka audio-coverage <project>
pinaka audio-credits <project>
pinaka mux-voiced <project>
pinaka preflight <project>
pinaka delivery-packet ... --credits <audio-credits.json>
```

```json
{
  "cue_key": "sfx-shot-03-whoosh",
  "artifact_id": "art_…",
  "role": "sfx",
  "start_ms": 2400,
  "duration_ms": 400,
  "gain_db": -18,
  "loop": false,
  "shot_id": "shot_…",
  "decided_by": "reviewer@example.test"
}
```

Re-assigning the same `cue_key` replaces the cue, so cue placement is idempotent. A film carries at
most **12 cues**, and no cue may run past the end of the film.

## Mixing

`FFmpegMuxAdapter` builds one deterministic graph:

```text
[voice] loudnorm=-16 LUFS / -1.5 dBTP, adelay=200, apad, atrim
[bed]   volume=-20 dB, atrim, 0.4 s fade in, 0.6 s fade out
[bed][voice] sidechaincompress threshold=0.03 ratio=8 attack=20 release=300
[cue_i] volume=<gain>, atrim=<duration>, adelay=<start_ms>
[voice][ducked][cue_0..n] amix normalize=0
```

The bed ducks under the voice; cues are gain-staged and never ducked. Every hash, gain and offset is
part of `command_hash`, so the same inputs always produce the same master.

## Gates

| Gate | Rule | Fails when |
|---|---|---|
| QA (`audio.layers`) | every bed and cue carries a verified hash | an unhashed layer reaches the mix |
| QA (`audio.level`) | configured bed gain −30…−12 dB, cue gain −40…−6 dB | gain settings exceed policy (not a measured stem-to-voice ratio) |
| QA (`audio.density`) | cues at least 2000 ms apart | sounds are stacked |
| QA (`audio.credits`) | attribution present when the licence requires it | a CC-BY track ships uncredited |
| Preflight (`R-AUDIO-001`) | licences clean and complete | NC/ND, missing attribution, backup without primary |
| Publication (`P-MUSIC-001`) | `music_licence` plus verified, current audio-credits artifact | disclosure or hashed credit coverage is missing |
| Kids (`kids.audio_cues`) | ≤8 cues, ≤−12 dB, ≥2000 ms apart | a kids profile is exceeded |

Both `mux-voiced` and `mux-narration` include registered layers. Preflight rechecks the artifact's
rights record, not the treatment-decision summary. The YouTube Kids publication profile runs the cue
limits and measures the actual final mix against the kids loudness/true-peak policy. Nominal gains
do not certify perceived loudness or an independently measured voice-to-bed ratio.

## Delivery

`audio-credits` writes a hashed artifact listing every bed and cue with its licence, licence URL,
attribution and content hash. `delivery-packet --credits` copies it into the packet as
`credits.json`, so a CC-BY obligation travels with the files instead of living in a chat message.

## Not implemented

- Paid music generation (Lyria and similar). Deliberately excluded: the flow is open-source only.
- Beat-sync to cuts, stem separation, per-genre ducking curves.
- Automatic cue selection from the shot plan.
