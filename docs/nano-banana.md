# Nano Banana image generation

**Status: historical provider notes.** The Vertex/ADC commands below are retired. The optional
API-key free-tier adapter remains separate; the current paid flow is fal with a controller-funded
item. See `architecture.md`.

**Last verified:** 10 November 2026
**Models:** `gemini-3.1-flash-image`, `gemini-3.1-flash-image-preview`,
`gemini-3.1-flash-lite-image`, `gemini-3-pro-image`, `gemini-nano-banana-2.1`

## Two routes, one controller

| Route | Auth | Model default | Billing | Code |
|---|---|---|---|---|
| **Vertex ADC** (preferred) | Application Default Credentials; no API key | `gemini-3.1-flash-image` | Google Cloud trial credit | `adapters/google_vertex_image.py` |
| **AI Studio API key** | `GEMINI_API_KEY` or key file | `gemini-2.5-flash-image` | Free tier only; paid billing refused | `adapters/google_image.py` |

Both routes go through `CreativeController.run_tool_once`, require a brief approval, write a hashed
artifact, and record usage/cost metadata. Neither route logs the prompt, key, or raw provider
response.

## Vertex ADC route

### Configuration

| Setting | Value |
|---|---|
| Project | `spheric-host-505921-t5` |
| Location | `global` |
| ADC store | `D:\pinaka-credentials\gcloud\application_default_credentials.json` (also `%APPDATA%\gcloud\...` via junction) |
| Tool profile | `config/tools/google-vertex-nano-banana.json` |
| Provider profile | `config/providers/google-vertex-nano-banana.json` |
| Secret note | `D:\pinaka-credentials\secrets\nano-banana.md` |

Override the project/location in another environment with `GOOGLE_CLOUD_PROJECT` and
`GOOGLE_CLOUD_LOCATION`, or with `--project` / `--location`. The defaults are the values above.

The adapter constructs `genai.Client(vertexai=True, project=..., location="global")`. It never reads
an API key. `models.get()` is used before a controller operation is reserved, so missing ADC or an
unavailable model fails before any charge.

### Command

```powershell
.\.venv\Scripts\python -m pinaka --home workspace generate-vertex-image <project-id> `
  --prompt "A ceramic espresso cup on a dark walnut table, soft window light from the left, shallow depth of field" `
  --model gemini-nano-banana-2.1 `
  --aspect-ratio 16:9 --image-size 1K --max-cost-cents 5
```

`--max-cost-cents` is mandatory in spirit: the adapter requires the operation ceiling to be at least
its conservative estimate. Use 5 cents for Nano Banana 2.1 and 8 cents for Gemini 3.1 Flash Image at
1K. The default is 15 cents, enough for 1K Gemini 3 Pro Image.

### Models and prices

Official Vertex pricing, per 1K output image, excluding negligible prompt tokens:

| Model | 1K | 2K | 4K |
|---|---:|---:|---:|
| `gemini-nano-banana-2.1` | $0.034 | $0.050 | $0.113 |
| `gemini-3.1-flash-lite-image` | $0.034 | — | — |
| `gemini-3.1-flash-image` | $0.067 | $0.101 | $0.150 |
| `gemini-3-pro-image` | $0.134 | $0.134 | $0.240 |

Source: <https://cloud.google.com/vertex-ai/generative-ai/pricing> (Nano Banana section).

The media workspace calls `gemini-3.1-flash-image` the “cheapest” model. At 1K, Nano Banana 2.1 is
actually cheaper per image. Use Nano Banana 2.1 when cost is the priority.

### Cost accounting

The receipt records:

- `estimated_cost_minor`;
- `actual_cost_minor`;
- prompt, candidate and total token counts;
- model, project, location and image size;
- `billing: gcp_trial_credit`.

The CLI also writes an `external_spend` artifact under the project. This preserves external trial-credit
spend without pretending it was a cash charge. The project’s `spent` budget is not debited because
the controller currently reserves paid work through budgeted provider jobs, not tool operations. That
settlement path is the remaining accounting gap.

## API-key free-tier route

Use only when a genuine free-tier key exists:

```powershell
.\.venv\Scripts\python -m pinaka --home workspace generate-image <project-id> `
  --prompt "..." --aspect-ratio 9:16 --max-cost-cents 0
```

The key is read from `GEMINI_API_KEY` or
`D:\pinaka-credentials\secrets\gemini-api-key`. The adapter refuses `max_cost_minor != 0`
and paid billing. Confirm in Google AI Studio that the project has billing disabled and a real
free-tier quota; the adapter cannot verify Google’s billing state.

## Verification evidence

One controller-owned Vertex sample:

| Item | Value |
|---|---|
| Model | `gemini-nano-banana-2.1` |
| Image | 1376×768, 16:9, 1K |
| File | `workspace/checks/nano-banana-espresso.png` |
| SHA-256 | `1662de2588e6a84b5ed53169116ac78c8c44661aace79f923701b5bc0007bdea` |
| Tokens | 31 prompt / 1,120 candidate / 1,851 total |
| Estimated / actual | 5 cents / **4 cents trial credit** |
| Artifact | `art_5d8e1002db0e49daadb1f06ae18d9aa1` |
| Spend record | `art_b857edd84b7f454bab05cf0dcd6e7a4a` |

The existing media-workspace sample is
`C:\Users\LENOVO\projects\media\images\nano-banana-recheck.png`.

## Real-project checklist

1. Confirm ADC: `gcloud auth application-default login` if expired, then run the model lookup.
2. Register the tool profile:
   `register-tool-profile --file config/tools/google-vertex-nano-banana.json`.
3. Register the provider profile for routing:
   `register-provider-profile --file config/providers/google-vertex-nano-banana.json`.
4. Confirm the project has an active brief approval.
5. Choose the model and set `--max-cost-cents` to the conservative estimate.
6. Generate one sample, inspect it, and import/approve the exact hash before batch work.
7. Record the `external_spend` artifact in the project’s evidence.

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `ADC is unavailable or expired` | ADC token expired | `gcloud auth application-default login` |
| `Vertex image model is not available` | Model not enabled in project | Check the model ID and Vertex access |
| `Reauthentication needed` from `gcloud billing` | gcloud CLI user credential expired | `gcloud auth login`; ADC image calls can still work |
| `403 SERVICE_DISABLED` | Vertex API disabled | Enable `aiplatform.googleapis.com` |
| `429` | Quota | Wait or lower concurrency |
| 16:9 image is 1376×768, not 1920×1080 | 1K output tier | Use 2K/4K or upscale separately |

## Security

- Never put an API key, ADC token, or refresh token in a prompt, SQLite record, artifact, log or Git.
- The secrets directory is `D:\pinaka-credentials\secrets`; it is outside the repository.
- Rotate any key that appeared in chat.
- Keep the model allowlist exact; do not add wildcard fallbacks.
