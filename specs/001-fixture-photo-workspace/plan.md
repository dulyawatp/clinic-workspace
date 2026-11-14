# Implementation Plan: Fixture-only clinical photo workspace

**Feature**: `001-fixture-photo-workspace` | **Spec**: `spec.md` | **Date**: 2026-11-06

## Summary and existing authority

Deliver the approved Plan 04 Task 5 as a fixture-only slice, using the existing React/TypeScript app, proxy read layer and capability model. Preserve `docs/superpowers/specs/2026-11-05-clinic-application-design.md` and `docs/superpowers/plans/2026-11-05-clinic-04-clinical-read.md`; this plan adds execution detail rather than replacing them. Backend/photo authorization and all photo writes remain blocked.

## Technical context

- `app/`: React 18, React Router data-router foundation, TanStack Query, Vitest, Playwright; `proxy/`: Fastify, Zod; `contracts/fixtures/`: synthetic inputs.
- Approved rendering libraries and verification policy are Plan 04 Task 5. Compatibility research and official sources are in `.superpowers/sdd/2026-11-05-clinic-04-clinical-read/task-5-report.md`; recheck exact pins and add an ADR and dependency guard before use.
- Synthetic PNG fixture transport and data-router migration were implemented in preliminary lanes, but are **not accepted** until their outstanding reviews and final integration checks pass. Their presence does not establish actual private storage, patient-scoped production authorization or consent policy.
- Local-only in-memory annotation state; no backend upload or persistence.

## Constitution check

- Clinical safety: synthetic data only; no unverified write or live access. **Gate before any delivery.**
- Privacy: server-side fixture checks and exact allowlisted synthetic byte delivery; no claim of production authorization. **Verify in integration.**
- Accuracy: document digest/dimensions derived from exact fixture originals; fail closed on mismatch. **Open transport review finding.**
- Accessibility: localized keyboard and touch interactions, viewport checks; explicitly record any missing manual AT review. **Pending.**
- Evidence: distinguish fixture/mock, real local proxy and live-site checks; no unqualified production pass. **Pending final verification.**

## Structure and design

1. Harden and test fixture-byte manifest, DTO binding and MSW parity; do not serve untrusted paths.
2. Reconcile data-router lifecycle and safe error behavior, then integrate `useBlocker` and dirty-only unload guard with draft/context replacement. No history patching.
3. Add gallery and side-by-side/slider compare with patient filters, server-returned identity validation, consent states with missing-consent guidance, read capability gate and noninteractive unavailable states. Viewpoint is absent from the current source contract: comparison stays unavailable for unknown/incompatible viewpoint until a verified fixture field is supplied; never infer compatibility.
4. Add strict library-independent annotation schema, local draft, mandatory accessible shape-list controls, canvas adapter, zoom/pan and shape resize, and explicit disabled save. Bind drafts to photo ID and validated original metadata.
5. Test source errors, transitions, permissions, metadata mismatch, no writes, keyboard operation, locales and 390/834/1440. Independently review security and verify browser and local proxy lanes.

## Risks and open gates

- Router blocker protects SPA navigation but browser unload prompts are platform-controlled; force termination cannot be reliably intercepted.
- Synthetic fixture identity and byte delivery do not prove patient assignment, tenant isolation or consent enforcement in a live site.
- Clinical fixture patient IDs differ from front-desk fixtures; cross-feature navigation remains an explicit separate integration task.
- Browser visual review and formal clinical UAT remain pending; no manufactured timing or performance targets.
