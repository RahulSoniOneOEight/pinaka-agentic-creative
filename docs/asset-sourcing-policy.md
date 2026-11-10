# Governed asset-sourcing policy

**Review date:** 9 November 2026  
**Scope:** Pexels, Openverse, Wikimedia Commons, Internet Archive, Freesound, Unsplash, Mixkit, Videezy, and owned media
**Mode:** metadata search is implemented for Openverse, Wikimedia Commons and Internet Archive; media
download/import remains disabled until a selected item passes the rights gate and an explicit
operator import

This document is an engineering policy, not legal advice. Provider terms and individual-item
licences can change. Every selected asset must retain its own evidence; this matrix is not a
substitute for reviewing the item and its current licence.

## Important terminology

“Free stock” does **not** mean open source, public domain, attribution-free, or unrestricted.

- **Openverse** is a search aggregator for Creative Commons and public-domain media. The exact
  licence belongs to each work and Openverse does not verify its accuracy.
- **Pexels, Unsplash, Mixkit, and Videezy** are stock platforms with platform-specific licences and
  terms. Their website or API code may be public in places, but that does not make every media item
  open source.
- A commercially usable asset can still carry attribution, endorsement, personality, trademark,
  privacy, editorial, or redistribution restrictions.

## Provider decision matrix

| Provider | Media in this flow | Acquisition | Integration decision | Selection gate |
|---|---|---|---|---|
| **Pexels** | Photos, video | Official API or manual import | Metadata search implemented for photos and videos; key present and validated; no downloads | Keep item page, creator, licence/terms evidence, Pexels link obligation, and third-party-rights review |
| **Openverse** | Images, audio | Official API or manual import | First API connection candidate; no video API | Verify the licence on the originating site; reject incompatible CC terms; preserve exact attribution |
| **Wikimedia Commons** | Images, audio, video | Official API or manual import | Metadata search implemented against the MediaWiki API; no downloads | Verify the licence, authorship and restrictions on the file description page; reject non-free/fair-use files; preserve attribution and share-alike |
| **Internet Archive** | Images, audio, video | Official API or manual import | Metadata search implemented against `advancedsearch.php`; no downloads | Treat the search index as discovery only; verify the item page, selected file and uploader rights; reject unclear licences |
| **Freesound** | Audio, SFX | Official API or manual import | Metadata search implemented; existing API key required; no downloads | Verify the sound page licence; preserve attribution and share-alike; previews are audition-only |
| **Unsplash** | Photos | Official API or manual import | Metadata search implemented; access key present and validated; no downloads | API previews hotlink returned URLs; an API result stays blocked until the operator names it with `--selected-asset-id`; preserve photographer and Unsplash attribution with UTM links |
| **Mixkit** | Video, music, SFX, templates | **Manual import only** | No documented public API was found; never scrape or script downloads | Capture the per-item licence. Stock Video Free can be commercial; Restricted cannot be used for commercial/business-social/YouTube or monetized output |
| **Videezy** | Video, templates | **Manual import only** | No documented public API was found; terms prohibit automated/non-human access and scraping | Capture the exact Standard, Community, Pro, CC, or Editorial licence and required attribution |

### Pexels

- The official API covers photos and videos. Default keys are documented at 200 requests/hour and
  20,000/month; rate-limit increases and new key issuance are currently paused.
- The base licence permits commercial use and modification and does not generally require credit.
  The API guidance separately requires a prominent link to Pexels; contributor credit should be
  retained and is required for unlimited-call eligibility.
- Do not sell unaltered copies, imply endorsement, use identifiable people offensively, redistribute
  content as a competing stock service, scrape/bulk-copy the library, or build an AI-training
  dataset from it.

Implementation notes:

- `pinaka.pexels.PexelsClient` performs metadata search only. It reads `PEXELS_API_KEY`,
  `PEXELS_API_KEY_FILE`, or the vault file `D:\pinaka-credentials\secrets\pexels-api-key`.
- Photos use `https://api.pexels.com/v1/search`; videos use
  `https://api.pexels.com/videos/search`. Authentication is the raw key in the `Authorization`
  header, not a bearer token.
- The Pexels License permits commercial use and modification and does not require attribution, so
  `attribution_required=false`. The API guidelines independently require a prominent Pexels link and
  retention of contributor credit, which are recorded as obligations and as
  `provider_credit_planned`/`creator_credit_planned`.
- Pexels returns no per-item licence field. The adapter applies the Pexels License as policy,
  records `license_code=pexels`, and leaves `origin_license_verified=false` until a person checks the
  item page and archives it as evidence.
- Videos resolve to the largest MP4 `video_file`. If no MP4 is present, the candidate records a
  missing-asset uncertainty instead of inventing a URL.
- Search is metadata-only. It never downloads a file, so a governed import remains a separate step.

### Openverse

- The API currently exposes images and audio. Openverse's user interface may link to external video
  sources, but that is not an Openverse stock-video API.
- Results can carry CC BY, BY-SA, BY-NC, BY-ND combinations, CC0, or public-domain marks. A
  commercial production must reject `NC`; a modified production must reject `ND`; all attribution
  and share-alike obligations must be retained.
- Openverse explicitly says it does not verify licence metadata. The originating page must be
  checked and saved as per-item evidence before import.
- Do not scrape or bypass rate limits. An API integration must say it was made using Openverse and
  is not endorsed or certified by Openverse.

### Wikimedia Commons

- Commons is a community-maintained repository. Its licence metadata can be wrong, incomplete, or
  changed after upload; the file description page is the authoritative evidence.
- CC BY, CC BY-SA, CC0 and public-domain marks can be commercially usable. Non-free, fair-use,
  unclear, and "copyrighted" files are not eligible. Preserve attribution and share-alike.
- Personality, trademark, museum, and access restrictions can apply even when the copyright licence
  is free. Record the `Restrictions` field and route it to human review.
- The adapter searches metadata only. It never downloads or imports a file; selection triggers a
  separate evidence and import step.

### Internet Archive

- The advanced-search index returns item-level metadata, not a licence grant. Licences vary by item
  and by file inside an item.
- The adapter records `licenseurl` when present, but many items return only the Archive's general
  terms page. That is not a licence and must be treated as unverified.
- Verify the item page, the uploader's rights, the selected file, and any access restrictions before
  import. Reject unclear licences and takedown-affected items.
- The adapter searches metadata only. File selection requires a separate item-metadata check and an
  explicit governed import.

### Freesound

- Freesound requires an existing API key. The adapter reads `FREESOUND_API_KEY` or
  `D:\pinaka-credentials\secrets\freesound-api-key`; the key is now present and was validated with a
  live metadata search. Never put the key in a URL, prompt, log or artifact.
- Each sound carries its own Creative Commons licence. Commercial productions must reject `NC`;
  modifications must reject `ND`; attribution and share-alike must be preserved.
- Search results carry an audition preview, not the original file. The adapter records no direct
  asset URL; the original requires a governed import after the licence is verified.
- Do not bulk-download or redistribute the library. Rate limits and the API terms apply.

### Unsplash

- The official API is photo-only for this flow.
- API results must use the returned hotlinked URLs for previews and displays of the original image.
  When a human selects a photo for use, call the returned `photo.links.download_location` endpoint.
- API displays must credit the photographer and Unsplash with the required UTM referral links.
- Keep access and secret keys out of prompts, logs, SQLite, fixtures, and Git. The integration must
  be a non-automated, high-quality, authentic experience—not a stock-site clone or bulk harvester.

Implementation notes:

- `pinaka.unsplash.UnsplashClient` performs metadata search only. It reads
  `UNSPLASH_ACCESS_KEY`, `UNSPLASH_ACCESS_KEY_FILE`, or the vault file
  `D:\pinaka-credentials\secrets\unsplash-access-key`; the app ID and secret key stay in the vault and
  are not used for search.
- Unsplash returns no per-item licence field. The adapter applies the Unsplash License as policy,
  records `license_code=unsplash`, and marks `origin_license_verified=false` until a person checks the
  photo page and archives it as evidence.
- API results are **blocked** until the operator selects one:
  `--selected-asset-id <photo-id>`. This is deliberate; an automated search result is not a human
  selection. A selected photo still requires the download event to be recorded and the licence
  evidence to be filed before it can become eligible.
- Unsplash+ (`premium`/`plus`) content is not covered by the free licence, so the adapter sets
  `commercial_use_allowed=false` and the candidate is blocked for commercial or monetized use.
- The current access key is on the demo tier: 50 requests/hour. Plan searches accordingly; do not
  poll or batch-harvest.
- The adapter records the hotlinked `urls.regular` as the preview reference and never stores a preview
  copy as the master. A governed import must use the file obtained through the download endpoint.

### Mixkit

- Licences differ by item type. Never infer a music/SFX/template licence from the stock-video rules.
- **Stock Video Free** permits commercial and non-commercial projects and does not require
  attribution, though credit is appreciated.
- **Stock Video Restricted** is for personal/non-commercial uses and excludes commercial projects,
  advertising, company social media, YouTube videos, and video-on-demand uses.
- User terms prohibit scripts/bots that mass-download or scrape the library and prohibit building a
  similar or competing service.

### Videezy

- **Standard** and **Community** files generally require attribution for free use; a paid licence may
  remove attribution for qualifying files.
- **Pro** permits commercial use without attribution after purchase. The original or modified file
  still may not be redistributed as stock.
- **Creative Commons** files follow the exact CC licence; `NC` is not commercial and `ND` is not
  compatible with planned modification.
- **Editorial Use Only** is not eligible for advertising or other commercial/promotional use.
- Terms prohibit automated/non-human access, data mining, robots, scrapers, offline readers, and
  unauthorized scripts. This provider therefore remains manual-import only.

## Production sourcing cascade

```text
1. Owned approved media
      ↓ unavailable
2. Official API search (Openverse → Unsplash → Pexels with existing key)
      ↓ no eligible candidate
3. Approved manual search/import (including Mixkit and Videezy)
      ↓ no eligible candidate
4. Deterministic crop/grade/composite of eligible assets
      ↓ still unresolved
5. Governed still generation
      ↓ only when motion is essential
6. Governed video generation
```

Rights filtering happens **before** aesthetic ranking:

```text
query → normalized candidates → rights gate
                              ├── blocked: discard
                              ├── needs_review: human/legal review
                              └── eligible: rank for fit, crop, quality, cost
                                               ↓
                                         sourcing approval
                                               ↓
                                  content-addressed original import
```

Visual quality, relevance, or schedule pressure can never override a blocked or unresolved licence.

## Required per-asset manifest

Every externally sourced original must record:

### Source and creator

- provider and provider asset ID;
- media type and acquisition method (`official_api` or `manual_import`);
- source item page and direct asset URL, when available;
- creator name and creator profile URL;
- hash of the API response, when acquired through an API.

### Rights evidence

- exact licence name/code and licence URL;
- provider terms URL and the date reviewed;
- explicit commercial-use and modification findings;
- editorial-only and attribution requirements;
- ready-to-use attribution text;
- saved licence-evidence reference and SHA-256 hash;
- origin-page verification for Openverse;
- personality/trademark review when recognizable people or brands appear;
- unresolved rights questions and downstream obligations.

### Provider compliance

- whether selection was human-initiated;
- Unsplash preview hotlink and download-event evidence;
- planned provider/creator credit;
- planned Openverse non-endorsement disclosure.

### Import integrity

- immutable original SHA-256 and byte size;
- normalized source-manifest SHA-256;
- project/use context and eligibility result;
- import event and downstream lineage.

The implementation stores this manifest in the immutable original artifact's `operation` metadata.
It does not store API credentials.

## Evidence, download and quota

| Step | Command | Behaviour |
|---|---|---|
| Capture evidence | `capture-evidence <project> --candidate <json>` | Fetches the item page and licence page, stores both as `reference` artifacts, records their hashes, sets `origin_license_verified=true`, and clears the "evidence pending" uncertainty |
| Governed download | `download-asset <project> --candidate <json>` | Fetches the direct asset URL, optionally fires the provider download event first, checks the content type, then registers an immutable original |
| Quota | automatic on `source-search` | Rolling window guard: Pexels 200/hour, Unsplash 50/hour. Exceeding it raises before any request is sent |

Evidence pages are capped at 4 MB and must be text documents. An asset is imported only when the
post-evidence evaluation reaches `eligible`; otherwise the import is refused with the remaining
reasons.

## Treatment decisions

Preservation is not a use decision. Every source asset is kept, and the question "as-is or changed?"
is answered per slot by `evaluate_treatment`.

| Verdict | Meaning | Rights rule |
|---|---|---|
| `use_as_is` | The source satisfies the slot unmodified | Commercial rights and any attribution obligation |
| `correct` | Crop, upscale, retouch or remove text | `modifications_allowed` must be `true`; not editorial-only; attribution still deliverable |
| `composite` | Built from several sources | Each parent checked separately |
| `regenerate` | A new AI asset replaces the source for this slot | Source retained but unbound |
| `replace` | Another source is needed | Source retained but unbound |
| `reject` | Unusable for this slot | Nothing bound |

A correction verdict does not modify the original: it instructs the operator to write a new
derivative with the source as its parent.

Record a decision with:

```powershell
python -m pinaka --home workspace finalize-asset <project-id> --file decision.json --decided-by reviewer@example.test
```

```json
{
  "slot_id": "slot_...",
  "artifact_id": "art_...",
  "criteria": {
    "framing_ok": false,
    "resolution_sufficient": true,
    "contains_conflicting_text": false
  },
  "use_context": { "use_class": "commercial", "monetized": true }
}
```

Coverage is reported by `slot-coverage <project-id>` and enforced by preflight rule `R-SLOTS-001`:
unresolved slots warn during production and fail at final candidate, final approval and archive. A
project with no declared slots passes trivially, which is why the archived pilot stays 12/12.

### Primary, backup and A/B assets

Every slot carries a primary asset and a backup, plus an optional A/B pair:

| Role | Required | Purpose |
|---|---|---|
| `primary` | Yes | The asset used in the master |
| `backup` | Yes | Approved alternative, promoted only when a person asks |
| `variant_a` | Optional | Experiment arm A |
| `variant_b` | Optional | Experiment arm B |

```powershell
python -m pinaka --home workspace assign-slot-asset <project-id> --file slot-asset.json --decided-by reviewer@example.test
python -m pinaka --home workspace promote-backup <project-id> <slot-id> --decided-by reviewer@example.test --reason "Person preferred the alternate"
```

```json
{
  "slot_id": "slot_...",
  "artifact_id": "art_...",
  "role": "backup",
  "criteria": { "framing_ok": true }
}
```

Rules:

- every assignment runs the same treatment evaluation as the primary path; a `replace` or `reject`
  verdict cannot be assigned;
- the backup is a different artifact from the primary and carries its own decision and hash;
- promoting a backup swaps the two roles in one transaction and writes an event with the reason;
- A/B variants must be two different artifacts; a single arm is reported as an incomplete pair;
- coverage is complete only when a slot has a primary and a backup, and any A/B pair is whole.

### Project use context

The licence context used for sourcing belongs to the project, not to the caller:

```powershell
python -m pinaka --home workspace set-use-context <project-id> --file context.json
```

```json
{
  "use_class": "commercial",
  "monetized": true,
  "advertising": false,
  "modification_planned": true,
  "attribution_can_be_delivered": true
}
```

When no context is recorded, the controller derives a conservative default: commercial, monetized
when the project budget is above zero, modification planned, attribution deliverable. A request may
repeat the stored context or strengthen it; a request that weakens it — for example declaring
`noncommercial` for a monetized project — is refused with a conflict.

### Automated criteria

`analyze-asset <project> <artifact>` measures the asset with local tools and writes a QA report:

| Measurement | Tool | Drives |
|---|---|---|
| Width, height, duration | ffprobe | `resolution_sufficient`, `motion_suitable` |
| Aspect ratio vs required | arithmetic | `framing_ok` |
| Text on the image | Tesseract | `contains_conflicting_text` |
| Face count | OpenCV Haar cascade | `contains_recognizable_person_or_brand` |
| Sharpness (Laplacian variance) | OpenCV | `has_visible_artifacts` |

The analyzer proposes criteria; it does not decide. Tone, brand fit and composition quality remain
human judgements, and a proposal may be overridden with a recorded reason.

### Correction and composite

| Command | Requirement | Output |
|---|---|---|
| `correct-asset` | A recorded `correct` decision for that slot and artifact | Derivative PNG with the source as parent, the filter graph and a command hash |
| `compose-assets` | Two registered artifacts | Derivative PNG with the background as parent and the overlay as a dependency |

Corrections are deterministic FFmpeg operations: `crop`, `scale` and `pad`. They are not AI edits;
an inpaint or generative upscale route remains a provider operation and would need its own adapter.
After producing a derivative, finalize it as a normal asset decision so its approval is recorded
against the new hash.

### AI edit, upscale and visual judgement

| Command | Route | Gate |
|---|---|---|
| `ai-edit-asset` | Local ComfyUI img2img or inpaint | A recorded `correct` decision, loopback endpoint, allowlisted checkpoint |
| `upscale-asset` | Local ComfyUI + ESRGAN-class model | A recorded `correct` decision, allowlisted upscale model |
| `judge-asset` | OpenAI-compatible vision endpoint | A credentialed endpoint; judgement stored as a QA artifact |

AI edits record prompt, negative prompt, checkpoint, seed, steps, denoise and the workflow hash. The
output is a derivative with the source as parent; the source is never altered.

`judge-asset` scores tone, brand, composition, legibility and safety from 0 to 1, lists findings with
`info`/`warning`/`blocking` severity, and returns a confidence and verdict. The stored report binds
the image hash, prompt hash and response hash.

### Autonomy policy

A `ReviewPolicy` decides how often a person must look:

| Outcome | Condition |
|---|---|
| `reject` | Any `blocking` finding |
| `human_review` | Any warning, any score below its threshold, confidence below the minimum, a `revise` verdict, or failed technical checks |
| `auto_accept` | No findings, every score above threshold, confidence at or above the minimum, and automatic acceptance enabled |

Defaults: confidence 0.8; tone, brand, composition and legibility 0.6; safety 0.7. A stricter or
looser policy is a JSON file passed with `--policy`.

### Current availability

| Component | State |
|---|---|
| ComfyUI server on `127.0.0.1:8188` | Not running |
| Inpaint checkpoint | Absent; only SD1.5 base is installed |
| Upscale model | Absent |
| Vision credential | Absent from the vault |

All four routes fail closed with an explicit error rather than silently degrading to a different
model. Installing the checkpoint and upscale model, starting ComfyUI, and adding a vision key enable
them without further code changes.

## Eligibility outcomes

| Outcome | Meaning | Controller action |
|---|---|---|
| `eligible` | Rights, evidence, acquisition method, and immediate provider obligations are complete | Permit immutable original import |
| `needs_review` | Permission or evidence is incomplete/uncertain | Block import pending human resolution |
| `blocked` | The proposed use conflicts with the licence, provider terms, or sourcing policy | Reject import; quality cannot override |

Examples of blocking conditions:

- commercial/monetized use with noncommercial or editorial-only rights;
- modifying a no-derivatives work;
- attribution is mandatory but cannot be delivered;
- API acquisition from a manual-only provider;
- standalone redistribution or implied endorsement;
- automated Unsplash selection;
- known uncleared third-party rights.

Examples requiring review:

- missing licence snapshot/reference/hash;
- unknown commercial or modification permission;
- Openverse metadata not checked at the originating site;
- unresolved personality, trademark, privacy, or property-release concern;
- missing Unsplash download event or API attribution plan;
- unrecorded Pexels API link requirement.

## Final-output obligations

Eligibility at import does not end compliance. Before final approval, QA must verify that:

- every selected asset is still linked to its current immutable original and source manifest;
- required credits appear in the appropriate output, description, or credits package;
- CC share-alike and other recorded obligations are satisfied;
- no editorial-only asset has drifted into commercial use;
- on-screen context does not imply endorsement or depict people offensively;
- the final archive includes an asset-and-licence manifest.

## Automation boundaries

- Use only documented official APIs and their published rate limits.
- Never scrape provider websites, automate manual-download pages, rotate identities to evade limits,
  bulk-harvest catalogues, mirror libraries, or create media/training datasets.
- Mixkit and Videezy stay manual until an official API or written authorization is documented.
- Pexels manual imports remain governed; its network API stays disabled unless an existing key is
  confirmed.
- Search adapters return metadata and previews; the controller alone decides whether selection and
  import are permitted.

## Official sources reviewed

- Pexels: [API](https://www.pexels.com/api/), [API documentation](https://www.pexels.com/api/documentation/), [licence](https://www.pexels.com/license/), [terms](https://www.pexels.com/terms-of-service/)
- Openverse: [API](https://api.openverse.org/v1/), [terms](https://docs.openverse.org/terms_of_service.html), [about/licence warning](https://openverse.org/about/)
- Unsplash: [API documentation](https://unsplash.com/documentation), [API guidelines](https://help.unsplash.com/en/articles/2511245-unsplash-api-guidelines), [licence](https://unsplash.com/license)
- Wikimedia Commons: [API](https://commons.wikimedia.org/w/api.php), [licensing](https://commons.wikimedia.org/wiki/Commons:Licensing), [terms](https://foundation.wikimedia.org/wiki/Policy:Terms_of_Use)
- Internet Archive: [advanced search](https://archive.org/advancedsearch.php), [terms](https://archive.org/about/terms.php), [developer docs](https://archive.org/developers/)
- Freesound: [API docs](https://freesound.org/docs/api/), [terms](https://freesound.org/help/tos_web/), [FAQ](https://freesound.org/help/faq/)
- Mixkit: [licences](https://mixkit.co/license/), [terms](https://mixkit.co/terms/)
- Videezy: [terms](https://www.videezy.com/terms), [licence types](https://support.videezy.com/en_us/categories/license-types-Bk1PKxowF), [commercial-use guidance](https://support.videezy.com/en_us/how-can-i-use-a-videezy-resource-for-commercial-purposes-HkAwYgowt)
