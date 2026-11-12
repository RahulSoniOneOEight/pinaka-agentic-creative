# Reference Roles

**Status:** Implemented. Generated motion is **not** part of the standard flow.

## Where references belong

The standard flow is:

```text
text → brief → shots → elements (owned artwork) → composition → render
```

References serve that flow by keeping **element artwork consistent across shots** — the same duck,
the same pond, the same palette. They are recorded per shot and role, carried in the work packet with
an exact hash, and reused as composition layers.

## Generated motion is an exception

Generated **video** comes from Studio's `fal.video` route, one shot at a time, seeded by the shot's
approved still. It needs a funded plan item and a stated intent, and it produces exactly one take per
request. See [`shot-motion.md`](shot-motion.md).

**Veo and Runway are excluded from the flow entirely.** They are not fallbacks, not alternates, and
not available "if a person names them": the controller refuses a non-`fal.video` route and any model
whose name contains `veo`. Only `fal.video` models (including the fal-hosted
`google/gemini-omni-flash/v1.2`) are selectable.

Until generated motion is requested:

- `provider_reference_hints` is empty;
- `generated_motion.requested` is `false`;
- `controller_authority.generated_motion_authorized` is `false`;
- composition layers come from owned artwork only.

A request that sets the flag without a reason is rejected.

## Roles

| Role | Meaning |
|---|---|
| `character` | who appears |
| `location` | where it happens |
| `style` | how it looks |
| `product` | exact item |
| `first_frame` | where a shot starts |
| `last_frame` | where a shot ends |
| `voice` | narration identity |

Each reference is either an artifact or a creative element — exactly one, never both. An element
reference resolves to the element's own artwork.

## CLI

```powershell
pinaka bind-reference <project-id> --file reference.json
pinaka list-references <project-id> [--shot-id <shot>]
```

```json
{
  "shot_id": "shot_…",
  "role": "character",
  "artifact_id": "art_…",
  "notes": "approved duckling from the storyboard"
}
```

One reference per shot and role; binding the same role again replaces it.

## In the work packet

Each packet carries:

- `references` — role, shot, and the resolved artifact path and hash;
- `reference_summary` — count, roles and shots;
- `generated_motion` — whether it was explicitly requested, and why;
- `provider_reference_hints` — only populated when generated motion was requested.

Reference artifacts are also added to the packet inputs, so their hashes are part of the packet hash.

## Provider mapping (exception path only)

Only Studio's `fal.video` route has a mapping; `pinaka.references` rejects any other provider.

| Role | `fal.video` field |
|---|---|
| character / location / style / product | `imageReferences[]` |
| first_frame | `firstFrame` |
| last_frame | `lastFrame` |

Field names follow the route's documented request shape and **must be re-verified against the live
API before the first paid call**. The mapping lives in `pinaka.references` so an adapter can consume
it without guessing.

## Not implemented

- Live Studio `fal.video` generation. The packet, import and render path is built and tested with
  fakes, but no paid clip has been generated yet.
- Veo and Runway. Deliberately excluded, not pending: no mapping, no rate card, no route.
- Automatic reference selection from the storyboard.
- Reference-derived variants (A/B) per shot.
