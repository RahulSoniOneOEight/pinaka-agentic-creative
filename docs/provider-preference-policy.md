# Provider preference policy

Which surface executes which capability, in what order, and under what cost rules.

- **Config:** `config/providers/preference-policy.json`
- **Selector:** `pinaka.preference.select_route`
- **CLI:** `select-route --capability <name> --candidates <file>`

## Tiers

| Tier | Meaning |
|---|---|
| `controller` | Built-in governed logic; no external execution |
| `local` | Local tools: FFmpeg, OCR, OpenCV, ComfyUI, Kokoro, faster-whisper |
| `managed_model` | A hosted model reached with a project credential (vision judge) |
| `fal_direct` | fal REST API using the vault fal key |
| `studio` | OpenMontage Studio gateway and its verified provider keys |
| `managed` | Future managed gateway |

## Precedence

| Capability | Order | Rationale |
|---|---|---|
| `analysis`, `deterministic_correction`, `composite` | local | Free, deterministic, already authoritative |
| `render` | local → studio | Local render is reproducible; Studio adds storyboard and library |
| `composition`, `storyboard` | studio → local | Studio owns the plan, shots and renderer workflow |
| `review_packet` | studio | Advisory inspection is a Studio feature |
| `inpaint`, `generative_upscale` | fal_direct → studio → local | fal is headless and per-call; local needs models |
| `image_generation` | fal_direct → studio → local | fal primary; local only when paid is disabled |
| `video_generation` | fal_direct → studio | No local video model |
| `avatar`, `mesh`, `world` | fal_direct → studio | fal primary, Studio fallback |
| `narration`, `transcription` | local → studio | Local is free and already proven |
| `vision_judgement` | managed_model → fal_direct → studio | Best available visual model first |
| `sourcing`, `publication`, `archive` | controller | Never delegated to Studio |

## Paid rules

| Rule | Default | Effect |
|---|---|---|
| `allow_paid` | `false` | Every paid route is rejected until a budget is approved |
| `max_usd_per_call` | 0.50 | A quote above this falls through to the next tier |
| `max_usd_per_project` | 5.00 | Remaining budget also bounds the choice |
| `require_quote` | `true` | A paid route without an estimate is rejected |
| `require_receipt` | `true` | The controller records the provider receipt after the call |
| `fallback_on_unavailable` | `true` | An unavailable route falls through instead of failing |

## Why fal direct before Studio

Studio is the better surface when the deliverable lives in a Studio production: it owns the
storyboard, the renderer, review packets and the library. For an isolated paid operation — one
inpaint, one upscale — Studio adds a production, a membership lease and its own budget on top of the
provider cost. fal direct needs only the key and a per-call receipt.

Studio therefore wins on **workflow**, fal wins on **atomic cost**. The policy expresses that: `studio`
first for composition and storyboard, `fal_direct` first for inpaint and upscale.

## Selecting a route

```powershell
python -m pinaka --home workspace select-route --capability inpaint --candidates routes.json --budget-remaining 1.00
```

```json
{
  "candidates": [
    { "route_id": "fal.flux-fill", "tier": "fal_direct", "paid": true, "estimated_usd": "0.04" },
    { "route_id": "studio.image.cleanup", "tier": "studio", "paid": false }
  ]
}
```

The result names the selected route, the ones rejected and why, for example
`"paid routes are disabled by policy"` or
`"estimate 2.00 exceeds the per-call ceiling 0.50"`.

```powershell
python -m pinaka --home workspace select-route --capability render --show-policy
```

## Current state

| Item | State | Consequence |
|---|---|---|
| `allow_paid` | `true` | Paid routes are permitted within the tiered budget |
| Budget | USD 1.00 primary + 1.00 bonus | Bonus is only used after the first dollar |
| fal adapter | Implemented: queue submit, poll, download, allowlists | Ready |
| fal vault key | Single-token `fal_...` key stored | Format accepted; authentication confirmed on the first charged call |
| Studio MCP tools | Not exposed in the current session | `studio` routes unreachable from here |
| Local ComfyUI | Not running | `local` inpaint and upscale unavailable |

The vault now holds a single-token `fal_...` key; the client accepts both that format and the older
`id:secret` form. fal offers no free authentication probe, so the first charged call confirms the key.
Reserve the spend before that call so the attempt is recorded either way.

## Tiered spend budget

Config: `config/providers/spend-budget.json`

| Field | Value |
|---|---|
| `primary_usd` | 1.00 |
| `bonus_usd` | 1.00 |
| `notify_at_usd` | 1.00 |
| `max_per_call_usd` | 0.50 |

A paid call is charged before it runs:

```powershell
python -m pinaka --home workspace reserve-spend --provider fal --amount-usd 0.04 --purpose "flux fill test"
python -m pinaka --home workspace spend-summary --provider fal
```

Behaviour:

| Situation | Result |
|---|---|
| Total stays at or below 1.00 | Charged to `primary` |
| Total reaches exactly 1.00 | Charged to `primary`, and one notification is emitted |
| Total is already above 1.00 | Charged to `bonus`, no repeat notification |
| Total would exceed 2.00 | Refused with `BudgetExceededError` |
| Single call above 0.50 | Refused by the per-call ceiling |

The notification is printed to stderr as `NOTICE: ...` and recorded as a
`budget.threshold_reached` event, so it survives the session. It fires once by construction: the
condition is `already_spent < 1.00 <= total_after`.

The policy stays valid in that state: every capability resolves to a route that is either available or
explicitly rejected with a reason.

## Changing the policy

Edit the JSON, then verify with `select-route --show-policy`. Raising `allow_paid` is a budget
decision, not a configuration convenience; pair it with an explicit per-call ceiling and a project
ceiling.
