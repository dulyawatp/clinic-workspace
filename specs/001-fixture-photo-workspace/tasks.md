# Tasks: Fixture-only clinical photo workspace

**Input**: `spec.md`, `plan.md`, existing approved Plan 04 Task 5. Checkboxes record implementation status, not independent acceptance.

## Foundation

- [x] T001 [US1] Resolve manifest-to-photo DTO identity, URL, byte-count, hash and dimensions consistency and MSW error/header parity in `proxy/src/read/registry.ts`, `proxy/src/read/photo-assets.ts`, `app/src/test/msw/handlers.ts`; independently reviewed and verified: proxy 39/39, app 23/23, workspace typecheck/lint passed. Fixture authorization remains unverified.
- [ ] T002 [US4] Verify router migration, sanitized error boundary, StrictMode lifecycle and data-router test helpers in `app/src/app/router.tsx` and `app/src/test/render.tsx`; revalidate integration.
- [x] T003 [US3] Recheck compatible pinned photo dependencies; write `docs/decisions/photo-libs.md` and `scripts/verify-photo-deps.mjs` with tests as required by Plan 04.

## Gallery and compare

- [x] T004 [US1] Add canonical addressed routes/nav entries and capability-gated patient-scoped gallery in `app/src/features/photos/`; test denial before reads, consent and specific missing-consent guidance, identity mismatch and missing files.
- [x] T005 [US2] Add same-patient/body-area/verified-compatible-viewpoint before/after comparison with distinct side-by-side and slider presentations, accessible native reveal range and no invented equivalence; unknown/mismatched viewpoint remains unavailable. Test both presentations, incompatible viewpoint, accessible selection and failed images.

## Annotation preview

- [ ] T006 [US3] Implement strict `app/src/domain/annotation.ts` schema and coordinate conversions; test malformed metadata, normalized round trips and library independence.
- [ ] T007 [US3] Implement local-only draft state, canvas adapter, zoom/pan, shape selection/resize, mandatory accessible DOM shape creation/select/nudge/text-edit/delete, undo/redo and disabled save; assert originals immutable and zero mutation requests.
- [ ] T008 [US4] Wire SPA/back/forward blocker, dirty-only unload and local context-replacement confirmation; preserve draft on cancel, locale change and background refresh; test both proceed and cancel.

## Verification and handoff

- [ ] T009 [US1] [US2] [US3] [US4] Run focused unit/integration checks, workspace typecheck/lint/builds and browser checks at 390/834/1440 with TH/EN; test local fixture proxy separately from intercepted tests.
- [ ] T010 [US1] [US2] [US3] [US4] Independently review photo privacy, source identity and navigation safety; fix Important findings, rerun stale checks and report exact evidence and remaining live-backend gates.
