# Next gate: controlled connection spikes

Do not enable a production provider until the pilot contract is completed and reviewed.

Run one bounded operation for each connection. Stock sourcing is split into provider-specific gates
because their API and licence obligations differ.

### Stock sourcing gates

1. **Openverse:** one image search through the official API, then verify the exact item licence on
   the originating page. Preserve API metadata, attribution, origin evidence and hashes. Do not
   download until the rights evaluator returns `eligible`.
2. **Unsplash:** one human-initiated photo search/selection using hotlinked preview URLs, the returned
   `download_location` event, photographer/Unsplash UTM attribution, and confidential credentials.
3. **Pexels:** run only if an existing API key is available. Verify the prominent Pexels link,
   creator metadata, default rate limits and no-competing-service restrictions.
4. **Mixkit:** manually import one Stock Video Free item and one Restricted fixture; prove the Free
   item can pass and the Restricted item is blocked for a commercial/monetized context.
5. **Videezy:** manually import one attributed Standard/Community item fixture and one Editorial
   fixture; prove the attribution obligation survives and Editorial is blocked commercially.

No Mixkit or Videezy scraper/API adapter is authorized. See `asset-sourcing-policy.md`.

### Managed model and service gates

Availability of a provider does not authorize arbitrary models, endpoints, inputs, or spend. Run each
connection independently through the controller:

1. **OpenAI GPT:** invoke one exact approved model with a typed planning, research, script, or QA
   contract. Prove redaction, structured-output validation, token/cost reservation and settlement,
   rate-limit behavior, and secret-free persistence. Do not automate the consumer ChatGPT website.
2. **DeepSeek:** invoke one exact hosted or approved self-hosted model with the same contract and
   evidence. Record the endpoint, model version, service terms, and model licence separately.
3. **fal still:** run one allowlisted still-image endpoint with eligible references, a hard cost cap,
   idempotency, polling, restart/reconciliation, output hash, and rights evidence.
4. **fal video:** only after the still path and an approved animatic pass, run one allowlisted video
   endpoint for one exact unresolved shot. Bound references, dimensions, seconds, attempts, and cost.
5. **Hosted voice or another service:** when the pilot selects one, add one exact provider/model
   profile and repeat the privacy, rights, budget, idempotency, recovery, and QA gates. Never use a
   wildcard OpenAI-compatible endpoint or shared unrestricted credential.

OpenCode may use GPT or DeepSeek to assist the operator, but any model call that becomes production
state, handles protected input, or consumes project budget must run as a controller-authorized job.

### Remaining connection gates

1. FFmpeg: cut, overlay, audio and export using explicit argument arrays.
2. KinoCut: prove the equivalent operation and document the FFmpeg fallback.
3. Penpot: one scoped edit/export plus reconstruction comparison and font check.
4. Target Windows laptop: font rendering, PNG alpha, render throughput and memory measurements.

Each spike must supply:

- exact tool/provider version or endpoint;
- exact model and approved role for model-backed services;
- capability status (`configured`, `working`, `degraded`, or `unavailable`);
- request, response and artifact hashes without secrets;
- measured elapsed time and cost;
- failure/restart evidence;
- approved fallback when the required operation is unavailable;
- data classification, privacy/retention disposition, usage settlement, and licence evidence for
  managed services.

Stock spikes additionally require the normalized source/right manifest, per-item licence evidence,
terms review date, creator/attribution fields, provider-specific compliance evidence and a recorded
eligibility outcome.

Video generation remains disabled until the still/animatic approval gate works end to end. After that
gate, one exact fal video endpoint may be enabled within Production Phase 1; it does not authorize any
other provider, model, endpoint, or media type.
