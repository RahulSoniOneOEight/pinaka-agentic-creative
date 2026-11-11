# Studio Gateway

**Status:** Implemented (transport-backed). No live production has been executed.

## What it does

The gateway turns an already-authorized work packet into one Studio production, and brings the result
back through the controller import boundary.

```text
ready check → authorized operation → reserved budget job
  → open production  (explicit cap, never Studio's default)
  → submit packet    (once)
  → poll             (resume, never resubmit)
  → collect outputs  → controller import → receipt → settle
```

It never spends, approves, or publishes on its own. Every step is recorded, and re-running the same
command resumes rather than restarts.

## Transports

The controller cannot call MCP directly, so the live call sits behind a transport.

| Transport | Use |
|---|---|
| `deterministic` | offline end-to-end test of the gateway |
| `spool` | file bridge: a host agent or operator performs each Studio call |

### Spool protocol

For a production keyed by the packet hash prefix:

```text
<spool>/<key>.open.request.json      → <key>.open.response.json      {production_id, status}
<spool>/<key>.submit.request.json    → <key>.submit.response.json    {job_id, status}
<spool>/<key>.poll-<job>.request.json → <key>.poll-<job>.response.json {status}
<spool>/<key>.collect-<job>.request.json → <key>.collect-<job>.response.json
```

Requests are written once. A missing response returns `status: pending` with the step and the file to
produce; nothing is charged and the budget reservation stays held.

Collect response:

```json
{
  "outputs": [
    {
      "source_path": "C:/.../storyboard.json",
      "relative_path": "artifacts/openmontage/results/storyboard.json",
      "kind": "derivative",
      "role": "storyboard_manifest",
      "content_hash": "<sha256>"
    }
  ],
  "actual_cost_minor": 25
}
```

Every output must match its declared hash; the controller copies it into the project store and records
lineage.

## Budget

- The production is opened with `max_cost_minor` and `currency` taken from the **reserved budget job**.
- Studio's own default allowance is never used.
- Cost above the reservation is refused by import and becomes a variance request (`kind: studio_run`).
  Approving it and re-running settles at the reported cost; rejecting it cancels at zero.
- A zero-cost run has no budget job and therefore cannot open a production.

## Recovery rules

| Situation | Behaviour |
|---|---|
| Same command re-run | Opens nothing, submits nothing, polls or collects |
| Studio still working | `status: pending`, step `poll` |
| Host has not answered | `status: pending`, step named, request file written |
| Transport reports failed/cancelled | Settles at zero, production marked failed/cancelled |
| Settlement refused | Artifacts registered by that call are discarded; nothing charged |

## CLI

```powershell
pinaka studio-run <project-id> <operation-id> --transport spool --spool ./studio-spool
pinaka studio-production <operation-id>
```

## What is not implemented

- No live MCP transport ships in the repository; the spool bridge is the integration seam.
- The gateway performs one collect pass; long-running renders rely on repeated `studio-run` calls.
- Publication is never requested from Studio.
