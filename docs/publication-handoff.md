# Publication Handoff

**Status:** Implemented (authorization recorded, package built). Publishing never happens here.

## Why a separate step

Production approval and publication authority are different decisions. A final-approved video is
ready to review, not ready to publish. So publication is authorized explicitly, by a named person,
against one exact hash.

## Flow

```text
final-approved or archived project
  → human records publication authorization (exact hash + channels)
  → publication-check    (platform rules, rights, captions, disclosure)
  → publication-package  (manual-upload bundle)
  → a person uploads
```

Nothing in this path posts to a platform.

## Recording authorization

```powershell
pinaka authorize-publication <project-id> --file publication-authorization.json
```

```json
{
  "channels": ["instagram_reels", "youtube_shorts"],
  "authorized_by": "producer",
  "reference": "client-email-2026-11-12",
  "expires_at": "2026-12-31T00:00:00Z"
}
```

Rules:

- the project must be **final-approved** or **archived**;
- an active final approval with an exact output hash is required;
- the artifact behind that hash must still be current;
- the hash is stored, so later edits do not silently inherit authorization;
- recording a new authorization revokes the previous one;
- expiry is optional and enforced on read.

## Checking and packaging

```powershell
pinaka publication-check <project-id> --file publication-request.json
pinaka publication-package <project-id> --file publication-request.json --output-dir ./delivery
```

`publication-request.json` holds the request; if it contains no `authorization` block, the stored
authorization is used for that channel. An explicit block still overrides it.

The package contains the media, the caption file when supplied, and a manifest with hashes. Its
status is always `ready_for_manual_upload` and `publishing_performed` is always `false`.

## Guarantees

| Guarantee | How |
|---|---|
| No silent publication | No tool posts to a platform |
| Exact-hash binding | Authorization stores the artifact hash |
| Named authority | `authorized_by` is required |
| Channel scope | A channel not listed is treated as unauthorized |
| Revocable | Re-authorizing revokes the previous record |
| Auditable | `publication.authorized` event on every record |

## Not implemented

- Automated upload to any platform.
- Per-channel credential handling.
- Scheduling or approval of scheduled posts.
