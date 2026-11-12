# Avatar and lip-sync

**Status:** implemented against published queue schemas. **No live call has been made.**

## Routes

| Model | Input | Output | Limits |
|---|---|---|---|
| `fal-ai/bytedance/omnihuman/v1.5` | portrait image + audio | talking video | 720p ≤ 60 s audio; 1080p ≤ 30 s |
| `fal-ai/sync-lipsync/v3` | source video + audio | lip-synced video | sync modes `cut_off`, `loop`, `bounce`, `silence`, `remap` |

Schemas: `https://fal.ai/api/openapi/queue/openapi.json?endpoint_id=<model>`.

## Consent is a gate, not a field

Synthetic likeness needs a recorded human authorization. `avatar-generate` refuses without
`--consent <reference>`; the reference is stored in the job parameters and returned with the result.
It is not a substitute for a signed release, and it does not cover a minor or a third party.

## Command

```powershell
python -m pinaka --home workspace avatar-generate <project-id> `
  --source <portrait-artifact-id> --audio <narration-artifact-id> `
  --plan <plan-id> --item <avatar-item> --model fal-ai/bytedance/omnihuman/v1.5 `
  --resolution 720p --consent "release-2026-01"
```

The plan item must be `provider: fal`, `operation: avatar.generate`, USD, and reserved through the
normal budget path. The call is one take; no retry is issued on an unknown submission.

## Cost

The endpoint does not return a price. The reservation is charged as the actual cost and the receipt
records `cost_source: planned_estimate`. Set the plan item from fal's published per-second price; do
not guess it in code.

## Live test checklist

1. A portrait you have the right to animate, and 5 seconds of narration.
2. A USD plan item for `fal/avatar.generate` inside the standing cap.
3. `--consent` naming the release.
4. One call; inspect face, mouth sync, duration and audio drift; record the charge.
5. Publication still needs the synthetic-media disclosure; do not infer it.

## Limits

- Inputs are inlined as data URIs under 12 MB; larger media must be hosted and passed as a URL.
- No automatic face/likeness verification and no sync-confidence metric.
- Studio's avatar routes are a separate path and remain subject to Studio admission.
