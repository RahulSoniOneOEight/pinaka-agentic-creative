---
description: Persist the approved deliverable and segment plan
---

Load the `pinaka-project` skill. Treat `$1` as the project ID and `$2` as an optional contract path,
defaulting to `config/pilots/pinaka-virtual-cfo-10s.json`. Read `status`, then run `pilot-plan`.
Report the deliverable, ordered segments and next human gate. Do not record approval on the user's behalf.
