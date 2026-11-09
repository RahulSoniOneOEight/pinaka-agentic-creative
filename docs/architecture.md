# Foundation architecture

```text
OpenCode / CLI / future UI
          |
          v
validated proposal contracts
          |
          v
CreativeController  <---- human approvals
  |       |       |
  |       |       +---- provider adapters (mock only in Phase 1)
  |       +------------ sourcing registry + rights eligibility gate
  |                         |-- official API candidates
  |                         |-- governed manual imports
  |                         +-- no scraping / bulk harvesting
  |       +------------ ArtifactStore (originals + rights metadata + lineage)
  +-------------------- Repository -> SQLite + event stream + budget ledger
```

## Ownership

- **SQLite** owns mutable execution state, approvals, reservations, jobs and audit events.
- **Versioned fact sets** own source-bound values used by a production.
- **ArtifactStore** owns project files; SQLite records their hashes and dependencies.
- **CreativeController** owns transition, approval, budget and secret-handling policy.
- **Adapters** translate an already-authorized operation to one provider. They do not approve spend.
- **Provider profiles** define which media and acquisition methods are permitted. Mixkit and Videezy
  are manual-only; Pexels API access requires an existing key.
- **The rights evaluator** rejects incompatible uses and holds uncertain candidates for review before
  they can enter the artifact store.
- **Agents** will return validated proposals. They do not write state or call paid services directly.

## Consistency model

Every database mutation runs in a `BEGIN IMMEDIATE` transaction and records an event in that same
transaction. Budget exposure is `spent + reserved`; pending and submission-unknown jobs retain their
reservation. The provider job ID is persisted as soon as submission succeeds.

SQLite is the single mutable owner. JSON is used for immutable input/output snapshots and manifests,
not as a competing editable state store.

## Invalidation

Approvals and artifacts declare dependencies such as a fact-set version or prior approval. Replacing
a fact set traverses this graph, invalidates dependent approvals, marks dependent artifacts stale,
and rolls an advanced project back to `facts_locked`. Unrelated originals and artifacts remain
current.

## Asset sourcing

Owned media is preferred. External candidates are normalized into source, creator, licence,
attribution, terms-date, evidence-hash, provider-compliance, and rights-uncertainty fields. The
controller evaluates those fields against the intended use before importing a content-addressed
original. Rights eligibility precedes visual ranking; `blocked` and `needs_review` candidates cannot
be imported through the sourced-original path.

See `asset-sourcing-policy.md` for the provider matrix and complete flow.

## Deliberately absent

No FastAPI, UI, Redis, external queue, vector database, parallel agent processes, production
publisher, or real paid adapter is included. Those require measured need and their own acceptance
gate.
