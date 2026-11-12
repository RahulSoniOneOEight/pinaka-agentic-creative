# Reference Roles

**Status:** Implemented (binding, packet, provider mapping). No live video generation executed.

## Why

Text prompts cannot hold identity. A reference makes the same character, place or look survive
across shots, and it is the input that reference-driven models actually use.

```text
element or approved image
  → bound to a shot with a role
  → carried in the work packet with an exact hash
  → mapped to the provider's own field names
```

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
- `provider_reference_hints` — the same references mapped per provider.

Reference artifacts are also added to the packet inputs, so their hashes are part of the packet hash.

## Provider mapping

| Role | Veo field | Runway field |
|---|---|---|
| character / location / style / product | `reference_images[]` | `reference_images[]` |
| first_frame | `image` | `promptImage` |
| last_frame | `last_frame` | `lastFrame` |

Field names follow each provider's documented request shape and **must be re-verified against the
live API before the first paid call**. The mapping lives in `pinaka.references` so an adapter can
consume it without guessing.

## Not implemented

- Live Veo or Runway generation. Both are paid per second and remain behind the budget plan.
- Automatic reference selection from the storyboard.
- Reference-derived variants (A/B) per shot.
