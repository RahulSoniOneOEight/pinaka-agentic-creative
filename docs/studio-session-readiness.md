# Studio Session Readiness

**Status:** Implemented gate

## Why

OpenMontage can be signed out, disconnected, missing a provider key, or updated underneath the
project. A work packet authorized against a Studio that is not actually up would fail late, after a
budget reservation. So Studio readiness is checked **at the start of every session** and recorded.

## Session ritual

```text
1. Agent loads Studio guidance (studio:bootstrap, studio:orchestration)
2. Agent reads account status, connections and app version
3. Agent records the probe:   pinaka studio-check --file studio-readiness.json
4. Work proceeds only when:   pinaka studio-status  →  ready: true
```

`prepare-studio-work-packet` refuses to authorize anything while readiness is missing, stale, or not
healthy.

## What the probe records

| Field | Meaning |
|---|---|
| `connected` | Studio reports a live connection |
| `account_state` | e.g. `signed-in` |
| `full_access` | Full access granted for productive work |
| `providers` | Connected provider names (never credentials) |
| `studio_version` | Installed Studio version |
| `production_folder` | Where Studio keeps productions |
| `status` | `working`, `configured`, `degraded`, `unavailable` |
| `checked_at` | When the probe was observed |

A report claiming `working` or `configured` must also record `connected: true` and
`full_access: true`; otherwise it is rejected as internally inconsistent.

## Freshness

```text
ready = status in {working, configured}  AND  age <= 12 hours
```

An older check is treated as unknown. That is deliberate: a Studio connection verified yesterday says
nothing about today, and provider keys can be revoked or balances exhausted between sessions.

## Fail-closed behavior

```text
no check recorded        → "Studio readiness has never been checked in this home"
check older than 12h     → "The last Studio check is stale; run pinaka studio-check again"
non-ready status         → "Studio reported a non-ready status"
```

In all three cases `prepare-studio-work-packet` raises `ApprovalRequiredError` and no budget is
reserved.

## CLI

```powershell
pinaka studio-status                      # is Studio verified right now?
pinaka studio-check --file studio-readiness.json   # record this session's probe
```

`studio-status` prints whether Studio is ready, the age of the check, the TTL, the last recorded
report, and the reason when it is not ready.

## Current observed state

Recorded on 11 November 2026 during a live check:

- account signed in, connection connected, Full access granted;
- providers connected: Google, OpenRouter, fal, Recraft;
- fal key verified with 10.00 USD available;
- Studio version 0.1.6, update idle;
- several speech and music vendors (ElevenLabs, Azure, OpenAI, Cartesia, xAI, Runware) had no key
  saved, while Google and OpenRouter routes were ready for the same operations.

That snapshot is evidence of the check, not a standing guarantee — run the probe again next session.

## What this gate does not do

- It does not start a production, spend money, or change Studio.
- It does not verify a provider balance beyond what Studio reports.
- It does not replace Studio's own setup checks; it records their outcome for the controller.
