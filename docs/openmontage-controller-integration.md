# Governed OpenMontage Integration

**Status:** Foundation implemented; live Studio execution intentionally disabled

## Authority boundary

`CreativeController` remains the system of record. OpenMontage is an execution, storyboard, and
composition surface. A Studio review or delivery never grants Pinaka approval, publication rights,
provider authority, or budget authority.

```text
preserved source artifact
  -> versioned workable brief + decision provenance
  -> reusable creative elements + shot bindings
  -> exact-hash governed work packet
  -> existing tool operation + specialist handoff
  -> Studio or deterministic fixture
  -> project-owned artifacts + terminal execution receipt
  -> controller QA, exact-hash approval, archive, publication gate
```

The integration does not call Studio directly. It establishes the transactional boundary needed by
a future live adapter without accepting OpenMontage's default budget or bypassing controller
records.

## Persisted records

Schema v9 adds:

- `workable_briefs`: immutable versions, source artifact/hash, package hash, and one current version;
- `brief_decisions`: supplied, derived, defaulted, assumed, approved, or blocked values;
- `creative_elements`: versioned characters, props, locations, worlds, palettes, voices, and styles;
- `shot_element_bindings`: current role-based bindings while preserving replacement history; and
- links from `specialist_handoffs` to the authorized operation, work-packet artifact, and exact result
  packet hash.

Changing a current brief or creative element invalidates dependent approvals and marks dependent
artifacts stale through the existing dependency graph. Protected source artifacts remain unchanged.

## Decision and blocker policy

An `assumed` decision must be reversible. A `blocked` decision must name the smallest affected work
unit in `blocked_work`. Values can be phase names such as `narration`, `storyboard`, or `render`, a
shot identifier, `all`, or a qualified value such as `storyboard:shot-01`.

Packet preparation rejects only a scope affected by a blocker. For example, a missing voice identity
with `blocked_work: ["narration"]` does not prevent a storyboard-only packet.

## Per-run spending policy

Each Studio run reserves an explicit cap in the **project currency** before authorization. Overall
creative funding is planned separately against a one-unit target — see
[`creative-budget-planning.md`](creative-budget-planning.md).

- **At or below the automatic limit** — the controller authorizes automatically, provided the project
  has enough unreserved budget.
- **Above the automatic limit** — a human must first approve that exact work request. The approval is
  bound to the request hash and can be consumed once.
- `max_credits` must remain `0`; Studio runs use a money reservation, not credits.
- `allow_publication` must remain `false`.
- A run must reserve in the project currency. A mismatched `max_currency` is refused rather than
  silently converted.

| Currency | Automatic per-run limit |
|---|---|
| USD | 1.00 |
| INR | 85.00 |


The reservation is a real controller budget hold:

```text
project.max_budget
  - project.spent
  - project.reserved
  >= this run's cap
```

The reservation, tool operation, and specialist handoff are created in one transaction. Settlement
releases the hold and charges the reported actual cost:

```text
import result (actual_cost)
  -> actual_cost <= reserved cap
  -> release reservation
  -> charge actual cost
```

A reported cost above the reserved cap is refused and requires manual reconciliation. An unknown
outcome keeps the reservation held and moves the budget job to `submission_unknown` instead of
charging it.

## CLI workflow

All commands use `--home` in the normal Pinaka CLI position.

```powershell
# Record and inspect the planning package.
pinaka record-workable-brief <project-id> --file brief.json
pinaka workable-brief <project-id>

# Record reusable elements and bind a current version to a shot role.
pinaka record-creative-element <project-id> --file element.json
pinaka list-creative-elements <project-id>
pinaka bind-creative-element <project-id> --file binding.json

# Authorize work without invoking Studio. A run above USD 1.00 needs a
# human spend approval first; the resulting ID is passed to the packet.
pinaka prepare-studio-work-packet <project-id> --file studio-request.json
pinaka approve-studio-spend <project-id> --file spend-approval.json
pinaka prepare-studio-work-packet <project-id> --file studio-request.json \
  --spend-approval <approval-id>
pinaka studio-work-packet <operation-id>

# After an external operator returns files, validate hashes, copy derivatives into
# the project artifact store, record lineage, and settle the run receipt.
pinaka import-studio-result --file studio-result.json
```

Examples are under `config/examples/`. Replace placeholder IDs and hashes with records returned by
the controller. The source artifact must belong to the project and its hash must match exactly.

## Live production

[`studio-gateway.md`](studio-gateway.md) drives an authorized packet through one Studio production:
explicit budget, single submission, resumable polling, hash-verified collection and controller
settlement.

## Scope boundaries

The routing, coverage, asset-cost, variance and approval gates apply to **planned** work — budget
plan items, Studio work packets and final approval. Legacy zero-cost paths (`generate-image` free
tier, local ComfyUI, the demo, older specialist operations) bypass them by design; they cannot spend
money, but they also do not produce per-asset cost rows.

## Session readiness
Studio readiness must be verified and recorded at the start of every session:

```powershell
pinaka studio-status
pinaka studio-check --file studio-readiness.json
```

`prepare-studio-work-packet` fails closed when the check is missing, stale (older than 12 hours), or
non-ready. See [`studio-session-readiness.md`](studio-session-readiness.md).

## Work-packet contents
The canonical JSON artifact contains:

- project intent and technical profile;
- the current workable brief and all decision provenance;
- the current scene plan when one exists;
- selected shots, current elements and bindings, governed slots, treatment decisions, primary/backup
  assignments, and audio beds;
- every input artifact path and exact hash;
- the project budget snapshot and this run's cap;
- allowed phases and output declaration; and
- explicit controller authority, spending limit, no-publication, and exact-hash approval statements.

Storyboard, composition, render, and review scopes require a current scene plan. Paid render scopes
also require an approved storyboard, and every slot in a render scope needs a primary and a backup
asset. Idempotency is enforced by the existing `(project, tool profile, idempotency key)` operation
constraint and by the packet artifact's content hash.

## Local-first generation routing

Paid generation through a planned budget item is refused unless the target slot explicitly permits a
generated source:

```text
slot.approved_sources must include one of: generated, paid_generation, ai_generated, ai, synthesis
```

A slot that already has a current primary asset is also refused, so existing owned or stock material
is used before paying to generate a replacement.

## Raw text planning

`pinaka plan-from-text` converts one informal request into a workable brief, shots, and a scene plan.
See [`raw-text-planner.md`](raw-text-planner.md).


## Result return

Each returned file must:

1. match its declared SHA-256 hash;
2. use a role allowed by the work packet when roles were declared;
3. be written to a new path under `artifacts/`;
4. become a project-owned artifact with dependencies on the work packet and operation; and
5. appear in one terminal execution receipt and the linked specialist handoff, settled against the
   run's budget reservation.

Repeated imports return the original receipt only when the complete result packet hash is identical.
An uncertain or different return is rejected rather than silently replacing governed output.

## Deterministic verification

`DeterministicStudioAdapter` exercises the full authorization, output, lineage, receipt, retry, and
invalidation path without network access, provider credentials, Studio state, or spending. It is a
test fixture, not a live OpenMontage client.

## Deferred live step

A future live pilot must be a separately authorized bundle. It must load Studio readiness and
orchestration guidance, explicitly set a controller-approved budget, use the saved packet identity,
collect an accepted job rather than resubmitting it, and return artifacts through this boundary.
Final approval, archive, and publication remain controller-owned.
