---
description: Start the governed Pinaka pilot from a validated contract
---

Load the `pinaka-project` skill. Use `$ARGUMENTS` as a pilot-contract path, defaulting to
`config/pilots/pinaka-virtual-cfo-10s.json` when omitted. Validate it with `pilot-validate`, then create
the project with `pilot-new`. Report the project ID, contract artifact hash and persisted next action.
Do not approve the brief or activate a provider.
