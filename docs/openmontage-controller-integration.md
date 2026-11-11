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
  -> exact-hash zero-spend work packet
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

## Zero-spend enforcement

Every `StudioScope` is validated with:

- `allow_paid_generation: false`;
- `max_credits: 0`;
- `max_usd: 0`; and
- `allow_publication: false`.

The resulting tool operation repeats those constraints in its limits. Existing receipt validation
also rejects any positive external cost. A paid Studio adapter must not be added to this path; paid
provider work requires the controller's separately budgeted provider-job flow.

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

# Authorize work without invoking Studio.
pinaka prepare-studio-work-packet <project-id> --file studio-request.json
pinaka studio-work-packet <operation-id>

# After an external operator returns files, validate hashes, copy derivatives into
# the project artifact store, record lineage, and settle a zero-cost receipt.
pinaka import-studio-result --file studio-result.json
```

Examples are under `config/examples/`. Replace placeholder IDs and hashes with records returned by
the controller. The source artifact must belong to the project and its hash must match exactly.

## Work-packet contents

The canonical JSON artifact contains:

- project intent and technical profile;
- the current workable brief and all decision provenance;
- the current scene plan when one exists;
- selected shots, current elements and bindings, governed slots, treatment decisions, primary/backup
  assignments, and audio beds;
- every input artifact path and exact hash;
- allowed phases and output declaration; and
- explicit controller authority, zero-spend, no-publication, and exact-hash approval statements.

Storyboard, composition, render, and review scopes require a current scene plan. Idempotency is
enforced by the existing `(project, tool profile, idempotency key)` operation constraint and by the
packet artifact's content hash.

## Result return

Each returned file must:

1. match its declared SHA-256 hash;
2. use a role allowed by the work packet when roles were declared;
3. be written to a new path under `artifacts/`;
4. become a project-owned artifact with dependencies on the work packet and operation; and
5. appear in one terminal zero-cost execution receipt and the linked specialist handoff.

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
