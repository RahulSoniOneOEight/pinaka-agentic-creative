# Update-oriented modular monolith

Pinaka is one Python process and one governed SQLite store. It is **not** split into services.
The controller remains the enforcement boundary, with feature-local implementations behind a
compatible facade. No queue, UI, vector database or plugin framework was added.

```text
CLI / operator proposal
  -> CreativeController (compatible facade)
     -> use_cases/<feature>.py
        -> policy/ (explicit reusable guards)
        -> operations/ (route, cost-model, receipt, one-take declarations)
        -> adapters/ (local tools / fal / Studio transport)
        -> Repository (compatible facade)
           -> store/<aggregate>.py
              -> Database.transaction -> state + event in one commit

contracts/<aggregate>.py -> strict requests and records
models.py               -> legacy import compatibility
```

## Core ownership and consistency (retained from the foundation)

- SQLite is the single mutable owner of projects, approvals, reservations, jobs and audit events.
  JSON holds immutable snapshots/manifests, not a competing editable state store.
- One credential boundary (`policy/secrets.py`) refuses credential-shaped field names *and* values
  in anything the controller persists; the error names the location, never the value.
- ArtifactStore owns project files; SQLite records their hashes, dependency graph and lineage.
  Versioned fact sets own source-bound values used by a production.
- Every database mutation uses `BEGIN IMMEDIATE` and appends its event in the same transaction.
  Budget exposure is `spent + reserved`; pending/unknown submissions retain reservations.
- Dependency changes invalidate affected approvals/artifacts and roll advanced project state back;
  unrelated originals remain current. Relocating source did not change this graph or the schema.
- Owned media is preferred. External candidates carry creator, licence, attribution, terms date,
  evidence hash and uncertainty. Rights eligibility precedes visual ranking and sourced import;
  blocked/needs-review candidates cannot enter through that path.
- Agents propose; only the Python controller commits. Provider adapters execute already-authorized
  operations and do not approve spend. No scraping/bulk harvesting or automatic channel posting.
- Provider sourcing distinctions (official API versus governed manual import) remain in
  `asset-sourcing-policy.md`; this refactor does not expand a provider's rights or API access.

## Ownership

| Change | Primary home | Test |
|---|---|---|
| Motion planning, reservation, import, assembly | `use_cases/motion.py`, `motion.py` | `test_shot_motion.py` |
| Funded still edits | `use_cases/images.py`, `adapters/fal_image.py` | `test_provider_catalog.py` |
| Script proposals | `use_cases/planning.py`, `scripts.py` | `test_modular_architecture.py` |
| Narration | `use_cases/narration.py` | `test_composition_media.py` |
| Music/SFX registration and mixing | `use_cases/audio.py`, `adapters/mux.py` | `test_audio_layers.py` |
| Studio packet policy | `use_cases/studio_packets.py` | `test_workable_brief_and_studio.py` |
| Accepted Studio job lifecycle | `use_cases/studio_execution.py`, `studio_gateway.py` | `test_studio_gateway.py` |
| QA and publication | `use_cases/qa.py`, `publication.py` | render/publication tests |
| SQL for one aggregate | `store/<aggregate>.py` | feature integration tests |
| Request shape | `contracts/<aggregate>.py` | contract/feature tests |

## Editing discipline

1. Start in the feature file, not the facade. Keep one owner per method; the architecture test
   rejects duplicate method names in facade bases.
2. Add policy to `policy/` only when shared. Explicit calls are preferred over hidden decorator
   side effects for budget reservations and transactions.
3. Register an operation's route and funding policy in `operations/catalog.py`. Reuse the existing
   price estimator instead of copying price constants. Catalog entries describe policy, **not**
   live credential readiness, provider access or balances.
4. Keep adapters small. Paid image calls must have a funded item; no optional `reserve=False`
   escape hatch. A submitted-but-uncertain job remains for reconciliation, never an automatic retry.
5. Use `Database.transaction()` and `Repository._event()` on the same connection. The relocation
   preserves that existing unit-of-work boundary rather than introducing event sourcing.
6. Run selected tests during editing; run the full serial and/or isolated-worker gate before release.

## Provider consolidation

- Hosted stills/edits: fal (`generate-asset`, `enhance-still`), or the existing Studio work-packet
  path using `fal.image`. Direct fal uses its separately configured credential; it never reads
  Studio's saved keys. Studio owns credentials used on the Studio path.
- Generated motion: Studio `fal.video` only, per shot, first-frame preferred. No Veo/Runway.
- Narration: local Kokoro via `synthesize-narration`; optional `--script-file` makes an unapproved
  local preview. ASR remains local faster-whisper.
- `draft-script --script-file`: records agent/operator-supplied copy as a proposal. An injected
  `ScriptAdapter` can supply copy programmatically; there is no implicit hosted model call.
- Vertex entry points and ADC-specific adapters are retired. Historic receipts and media are
  untouched. The optional free-tier Gemini adapter is separate, explicitly selected, and still
  available through the renamed `google-image` extra; it is not a Vertex fallback.
- Music/SFX: sourced open-licensed or owned audio; no paid generation route in this catalog.

## Static checks

`python -m mypy` is a release gate over the modules that are already clean: `policy/`,
`operations/`, `contracts/`, `adapters/`, `fal.py`, `captions.py`, `metadata.py`,
`finishing.py`, `beats.py`, `assembly.py`, `studio_gateway.py`, `artifacts.py`,
`database.py`, `errors.py`, `utils.py`, `enums.py`, `budget.py`. The feature mixins are the
remaining surface; add a module to `[tool.mypy].files` only when it passes, so coverage grows
monotonically. `scripts/check.ps1` and CI run the same command.

## Compatibility and limits

The public `CreativeController`, `Repository` and `pinaka.models` import paths remain. Feature
classes are implementation details using shared controller context, not independent services.
This is source modularization, not complete dependency inversion: cross-feature workflows still
delegate through the facade. Large workflows can be decomposed later when a specific change needs it.

The one-time `scripts/refactor_layout.py` codemod copied methods/decorators and checked exact
method text before formatting. It refuses to run on the relocated layout. New features should edit
the owned modules directly, not rerun the migration.
