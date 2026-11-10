---
description: Record an explicit human approval through the controller
---

Load the `pinaka-project` skill. Parse `$ARGUMENTS` for project ID, approval stage, human approver and scope.
Show the exact subject and dependencies before running `approve`. Never infer consent from prior chat. Final
approval must include the exact output SHA-256 hash.
