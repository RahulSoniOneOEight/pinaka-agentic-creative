# Update register

| Item | Status | Evidence |
|---|---|---|
| Assembly, take planning, script proposal storage | Closed | Existing regressions; script default is now supplied-copy/local rather than Vertex |
| Veo/Runway exclusion | Closed | Contract, route and operation-catalog refusal tests |
| U1 provider consolidation | Closed (code) | Vertex/ADC paths removed; fal funded image edit and local speech regressions |
| U2 / M1–M9 open-licensed music and sound cues | Closed (code/local runtime) | Both mux paths, rights, credit coverage and kids publication wiring tested |
| U3 shared policy | Closed | Central active-project, funded-item, one-take and audio-rights guards |
| U4 operation catalog | Closed | `pinaka operations`; policy only, not credential discovery |
| U5 controller split | Closed | `use_cases/`; compatible facade and ownership tests |
| U6 repository split | Closed | `store/`; original transaction/event boundary preserved |
| U7 contract split | Closed | `contracts/`; legacy imports preserved |
| U8 test lanes | Closed | Declared/pinned xdist, slow integration marker, scripts tested outside repository |
| P1 live fal image | **Blocked, not closed** | Studio refuses `credits: 0` at production creation; direct fal is unauthenticated (401 identical to no-auth, see below) |
| P2 live Studio image-to-video/import/assembly | **Blocked, not closed** | Same admission refusal; no accepted paid job exists |
| B2 paid music generation | Closed / excluded | User chose open-licensed/owned sources only |

The user explicitly authorized **USD 1 maximum total** for P1/P2, with zero OpenMontage credits,
one still, one five-second H3 clip, no retry and no publication. That authorization is not the
blocker: Studio's zero-credit budget validator is. No defaults or alternative credentials were used
to bypass it. New real spend is **USD 0**.

Detailed contract, baseline, request ID, exact refusal, test evidence, rollback and remaining
limitations: [implementation/remaining-items.md](implementation/remaining-items.md).
