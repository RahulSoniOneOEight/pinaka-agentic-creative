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
| **Pexels** | Photos, video | Official API or manual import | API only if an existing key is available; new key issuance is currently paused | Keep item page, creator, licence/terms evidence, Pexels link obligation, and third-party-rights review |
| **Openverse** | Images, audio | Official API or manual import | First API connection candidate; no video API | Verify the licence on the originating site; reject incompatible CC terms; preserve exact attribution |
| **Wikimedia Commons** | Images, audio, video | Official API or manual import | Metadata search implemented against the MediaWiki API; no downloads | Verify the licence, authorship and restrictions on the file description page; reject non-free/fair-use files; preserve attribution and share-alike |
| **Internet Archive** | Images, audio, video | Official API or manual import | Metadata search implemented against `advancedsearch.php`; no downloads | Treat the search index as discovery only; verify the item page, selected file and uploader rights; reject unclear licences |
| **Freesound** | Audio, SFX | Official API or manual import | Metadata search implemented; existing API key required; no downloads | Verify the sound page licence; preserve attribution and share-alike; previews are audition-only |
| **Unsplash** | Photos | Official API or manual import | API connection only after its tracking/attribution behavior is implemented | API previews hotlink returned URLs; human selection triggers `download_location`; preserve photographer and Unsplash attribution with UTM links |
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
