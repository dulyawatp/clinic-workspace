# Whole-app UI parity design

## Intent and why

Bring the React app to feature parity with the OpenDesign "Clinic Application
Design" prototype: all 37 designed surfaces, their components, interactions,
role navigation and responsive rules, in a single implementation wave. The
prototype and the gap table are the authority
(`docs/superpowers/specs/2026-11-15-ui-parity-gap-audit.md`; design source in
the user's OpenDesign project files, including the v3 enhancement spec).
Marley stays the backend of record; nothing in this wave claims unverified
backend capability.

## Goals

- Every designed surface exists at its designed route with its designed
  components, states and interactions, matching the prototype's Thai/English
  copy and fixture presentation rules.
- Role-gated navigation and route guards follow the design's per-surface role
  matrix (presentation level; no backend authorization change).
- Components backed by verified native contracts keep their native wiring
  (patient directory/profile/create/edit; appointment list/detail with
  reschedule/check-in; photo save; live stock list).
- Cross-cutting parity: per-role route guards, complete nav manifest, locale
  persistence, work-item dismiss, honest integration-status surface.
- Annotation engine for photo compare (draw, undo/redo, versioned layers) as
  a client-side, non-destructive feature matching the design.
- Preclinic design-system tokens and component styling match the prototype.
- Unbacked areas (pharmacy deep pages, cashier, payments, management,
  reports, admin, clinical write loop, labs, nurse flows, appointment
  cancel/no-show, checkout follow-ups) render the designed components over
  the prototype's clearly marked Demo/Sample fixtures, with truthful
  integration states instead of implying a Marley contract.
- Fixture coverage: components backed by prototype fixtures render over
  them; for designed components the prototype never populated, the wave
  authors minimal, clearly marked Demo/Sample fixtures following the
  prototype's own data conventions (seeded naming, frozen test clock) —
  recorded decision 2026-11-15. No unavailable-state fallback is required
  solely because the prototype lacked rows; integration status remains
  honest for surfaces with no Marley contract.

## Non-goals and anti-goals

- No new backend contracts, endpoints, migrations, tables, permissions or
  site changes in this wave; no claims of production readiness.
- No removal or regression of verified patient/appointment SIT behavior.
- Anti-goal: inventing backend capability anywhere; anti-goal: showing
  sample data as if it were a real integration; anti-goal: deleting safety
  affordances not in the design.

## Constraints

- Keep the React/TypeScript single app; do not embed or fork the prototype's
  HTML/JS runtime — port components, tokens, copy and fixtures.
- Authored fixtures must be supported by Frappe and Marley Healthcare: their
  field names, DocType/workflow semantics and vocabulary model the installed
  backend contract (design handoff `clinic-backend-api-contract-spec.md`,
  installed DocTypes), so future native wiring is a data swap.
- Preserve the native transport and error-envelope rules; new surfaces that
  later gain contracts must be able to switch from fixture to native without
  redesign.
- Route reconciliation: designed routes are canonical; the React-invented
  `/clinic/finance` is folded into the designed cashier/management surfaces
  and the LivePhotoWorkspace is reached from the designed patient photo
  surfaces, with redirects for deep links.
- Cancel/no-show: the approved appointment-write design excluded
  cancellation; the UI may present the designed control only in an explicit
  "not available in this contract" state, never as a working write path.
- All patient/appointment write gating, one-shot acknowledgement and lock
  semantics stay exactly as implemented and reviewed.

## Approaches considered

1. Staged waves per role area (safer incrementally) — user chose single wave.
2. Single parity wave (chosen): implement all surfaces in one pass, one
   consolidated review, one deploy/test pass afterward.
3. Embedding the prototype runtime — rejected: separate stack, no native
   wiring, forks the product.

User decisions recorded: single wave; Demo/Sample fixtures (clearly labeled)
for unbacked data areas, unavailable states where the prototype has no
backing fixture; design parity for components/interactions.

## What is left to the builder

- File structure and component decomposition following existing feature
  folders; fixture modules mirroring the prototype's sample data and copy.
- Exact route/guard wiring keyed to the audit's surface table.
- Test strategy per existing repo conventions plus visual/responsive checks
  at 390/834/1440 for new surfaces.
