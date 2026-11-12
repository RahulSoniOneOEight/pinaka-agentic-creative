# Gemini on Vertex with ADC

**Status:** Implemented; free model verification passed. No paid call executed yet.

## Why ADC

Gemini work runs against a Google Cloud project using Application Default Credentials. No API key
is stored in prompts, config, SQLite, logs or Git.

| Item | Value |
|---|---|
| Auth | Application Default Credentials |
| Project | `spheric-host-505921-t5` (fallback; override with `GOOGLE_CLOUD_PROJECT`) |
| Region | `global` (override with `GOOGLE_CLOUD_LOCATION`) |
| SDK | `google-genai` 1.75.0 |

## Verified availability

`pinaka vertex-check` performs a free `models.get` per model — no tokens, no charge:

```json
{
  "ready": true,
  "project": "spheric-host-505921-t5",
  "location": "global",
  "auth": "application_default_credentials",
  "models": [
    {"name": "google-vertex:gemini-3.1-flash-image", "status": "working"},
    {"name": "google-vertex:gemini-2.5-flash-preview-tts", "status": "working"},
    {"name": "google-vertex:gemini-2.5-flash", "status": "working"}
  ]
}
```

## Provider profile

```powershell
pinaka register-vertex-profile            # registers disabled
pinaka register-vertex-profile --project X --location us-central1
```

The profile records `credential_ref: ADC`, project, location, and `auth: adc`. It is registered
**disabled**; enabling still requires an explicit approval and is refused by the current
zero-spend provider policy until that gate is changed.

## Cost estimates

| Step | Basis | Where |
|---|---|---|
| Image | tokens in/out per model | `adapters/google_vertex_image.py` |
| Narration | text tokens in, audio tokens out | `vertex.estimate_tts_cost_minor` |
| Transcription | per second of audio | `vertex.estimate_transcription_cost_minor` |

The TTS and transcription rates are **policy constants** for planning. They must be confirmed
against the live Vertex price sheet before enabling a paid run; the receipt records the provider's
reported usage where available.

## Still enhancement

Sourced stills can be corrected or enhanced through the Vertex image adapter instead of ComfyUI:

```text
sourced artifact (never a protected original)
  → image.edit request with the source as an input image
  → Vertex gemini-3.1-flash-image
  → derivative artifact with parent + dependency lineage
  → receipt with estimated and actual cost
```

```powershell
pinaka enhance-still-vertex <project-id> --file vertex-still-enhance.json
# add --plan <plan-id> --item <key> to reserve the estimated cost first
```

It requires an active sourcing approval, refuses originals and hash-mismatched sources, and records
the estimate as the operation ceiling.

With `--plan` and `--item` the same call reserves the planned cost against the project budget before
the provider request, settles the real cost afterwards, and writes a per-asset cost row against the
plan item's slot. A failed request cancels the reservation at zero. This puts Vertex image spending
under the same $1 creative rule as narration and fal.

## fal cost model

fal work is priced per model. `pinaka.pricing` mirrors the November 2026 model pages:

| Class | Cheapest useful choice | Approx per unit |
|---|---|---|
| Draft stills | `fal-ai/flux/schnell` | ~$0.003 per 1024² |
| Keeper stills | `fal-ai/flux-2/flash` | ~$0.005 per 1024² |
| Cheapest motion | `fal-ai/ltx-2-19b/distilled/image-to-video` | ~$0.018 per second at 720p |

`fal_budget_preview(images=…, clips=…)` gives a batch estimate. Images are almost free at small
scale; **video is what consumes a budget**. A $1 creative comfortably covers roughly 20 images plus
8 five-second clips at the cheapest rates — verify before batching.

Caveats:

- Prices differ between fal's aggregator page and individual model pages; trust the model page and
  the playground estimate.
- Promotions expire (H3 Max Turbo promo ended 2026-11-15).
- Purchased credits last 365 days; free credits can expire in a week.

## Not implemented

- No paid Vertex or fal call has been executed by this bundle.
- TTS and transcription adapters are covered by injected-client tests only; their live response
  shapes must be confirmed on the first authorized call.
- Forced alignment remains unavailable (ElevenLabs only, no key).
