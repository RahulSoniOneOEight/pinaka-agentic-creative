# Pinaka project rules

- The Python controller is the enforcement boundary; agents only propose decisions.
- Never call paid providers outside a controller-approved, transactionally reserved budget.
- Never place secrets in prompts, logs, SQLite records, manifests, fixtures, or Git.
- Preserve originals. Write derivatives to new paths and record hashes and lineage.
- Every state change must be validated, committed atomically, and written to the event log.
- Keep external providers behind small adapters. Tests use deterministic fakes.
- Do not add a UI, queue, vector database, or distributed service until a measured need exists.
- Run `python -m pytest` before reporting an implementation change complete.
