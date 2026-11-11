# Creative Budget Planning

**Status:** Implemented foundation

## Purpose

Every creative is planned against a small dollar target before any paid work happens. The default
target is **USD 1.00**. Planning happens before generation, so cost is a design input rather than a
surprise discovered at settlement.

```text
creative idea
  → budget plan (line items, USD)
  → planned total vs USD 1.00 target
      → at or below  → authorized automatically
      → above        → human approval required
  → per-item reservation at the planned price
  → provider execution
  → actual vs planned variance
      → within tolerance → auto-accepted, reported
      → beyond tolerance → further approval required
  → summary for the person
```

## Rules

1. **Plan first.** A paid call must belong to a planned line item. The job estimate must equal the
   planned estimate, and the provider and operation must match.
2. **Target, not hard wall.** The target decides whether a human must approve the plan. It does not
   block a plan that a person has approved.
3. **Automatic below target.** A plan at or below the target is authorized immediately, provided the
   project has the unreserved budget.
4. **Approval binds the plan.** Approval is stored against the exact plan hash and total. Editing the
   plan creates a new version, which needs its own approval.
5. **Slight variance is expected.** Allowed variance per item is
   `max(estimate × tolerance_bps, tolerance_floor)`. Defaults: **50%** with a floor of 0.10 in the
   plan currency.
6. **Material overrun stops.** An actual cost beyond tolerance is refused at settlement and requires
   further approval rather than being charged silently.
7. **Project budget still governs.** A tolerated overrun cannot push `spent` above the project
   maximum.
8. **Currency follows the project.** A plan, its items, and its approvals use the project budget
   currency. Supported currencies are USD and INR. Mixed currencies inside one plan are rejected.

## Currency targets

| Currency | Default creative target | Default per-run cap |
|---|---|---|
| USD | 1.00 | 1.00 |
| INR | 85.00 | 85.00 |

The INR figures are policy equivalents of the one-dollar policy. They are fixed constants, not a
live exchange rate, and can be overridden per plan or per run.

## Persisted records


Schema v11 adds:

- `budget_plans` — versioned plan, target, tolerance, status, and content hash;
- `budget_plan_items` — provider, operation, estimate, priority, optional shot/slot, linked job,
  actual cost, and variance; and
- `budget_plan_approvals` — human approval bound to one plan and its hash.

## Item lifecycle

```text
planned → reserved → settled
planned → skipped
```

A settled item records the job, the actual cost, and the variance. The variance event is written to
the event log, so overrun history survives project changes.

## Summary

`budget-summary` reports:

- plan status and target;
- planned total and tolerance allowance;
- reserved, settled, and outstanding required items;
- settled estimate, settled actual, and variance;
- whether the variance is within tolerance;
- project budget, reserved, spent, and available; and
- whether approval or further approval is required.

## CLI

```powershell
pinaka plan-budget <project-id> --file creative-budget-plan.json
pinaka budget-plan <project-id>
pinaka budget-summary <project-id>
pinaka approve-budget-plan <project-id> --file budget-approval.json
```

## Relationship to the Studio run cap

The USD 1.00 Studio run cap and the creative budget plan are separate checks:

| Check | Controls |
|---|---|
| Creative budget plan | whether the overall creative is funded at all |
| Studio run cap | the maximum one Studio work packet may reserve |

A run can only proceed if the plan authorizes the work and the run cap allows that packet.

## Deferred

Live execution, automatic estimate acquisition, and marketplace price lookup are not implemented.
Estimates are supplied by the planner and recorded as `supplied` or `derived` values so the basis
remains auditable.
