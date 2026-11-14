# Feature Specification: Fixture-only clinical photo workspace

**Feature ID**: `001-fixture-photo-workspace` | **Created**: 2026-11-06 | **Status**: implementation slice of existing approved plan; not a production release

**Authority**: `docs/superpowers/specs/2026-11-05-clinic-application-design.md` §7.4 and `docs/superpowers/plans/2026-11-05-clinic-04-clinical-read.md` Task 5. This feature elaborates those documents without changing their approved scope. Gaps identified in `docs/assessments/2026-11-06-spec-kit-revalidation.md` remain open.

## User scenarios and acceptance

### US1 — Browse fictional patient photos (P1)
An authorized fixture user opens a patient-addressed gallery, sees only that patient's synthetic records and clear consent states. Denied capability produces a permission state before photo reads. Missing/unknown consent does not enable capture. Loading, empty, denied, invalid and unavailable-file states are distinguishable.

### US2 — Compare fictional photos (P1)
A fixture user selects a before and after image for the same patient, body area and verified-compatible viewpoint, with dates shown. Both side-by-side and slider presentations are available; a keyboard-operable native reveal control and pointer interface change the slider. Wrong-patient, incompatible-area or unknown/incompatible-viewpoint pairs cannot be shown as a valid comparison; unavailable originals are not replaced with fabricated images.

### US3 — Preview annotations without saving (P1)
A fixture user creates, selects, resizes, edits, nudges, removes, undoes and redoes supported shapes locally and can zoom and pan. A mandatory accessible DOM alternative supports pointer-free shape creation, selection, nudging, text editing and deletion alongside the canvas. The versioned annotation document uses normalized coordinates and validated metadata tied to the actual immutable original. A visible notice explains that work is unsaved and Save is disabled. No original is modified and no annotation is uploaded or persisted.

### US4 — Preserve unsaved work during navigation (P1)
While a draft is dirty, SPA links, browser history and local context replacements require a confirmation; cancellation retains the same photo and shapes. Reload/close attempts use the browser's supported unload prompt, with its platform limitations disclosed. Locale and layout changes retain draft state. Image metadata changes or background refetch must not silently replace a dirty draft.

## Functional requirements

- **FR-001**: Gallery and compare are patient-addressed, guarded by `read.photo`, with fixture server-side permission checks independent of the client.
- **FR-002**: Synthetic images use authenticated, allowlisted fixture delivery, no public static paths, arbitrary paths, real upstream fallback or cacheable responses. Fixture checks are not evidence of production patient authorization.
- **FR-003**: Photo ID, patient, body area, byte count, content type, SHA-256 and intrinsic dimensions must match the exact fixture bytes. Unknown, swapped, absent or mismatched metadata fails closed; editor initialization never invents metadata.
- **FR-004**: Compare only same-patient, same-body-area before/after records with known compatible viewpoints; label dates, offer both side-by-side and slider presentations and do not infer clinical equivalence. Until a viewpoint source is verified, unknown or mismatched viewpoints make comparison unavailable, not implicitly compatible.
- **FR-005**: Implement canonical `AnnotationDocV1` as specified by Plan 04 Task 5; validate shapes and normalized coordinates independently of rendering libraries. Render adapters must not leak library node properties into the document.
- **FR-006**: Support arrow, ellipse, rectangle, text and freehand, zoom/pan, shape selection and resizing, undo/redo, and mandatory accessible DOM shape creation/select/nudge/text-edit/delete. Save remains disabled with a reason; no uploads or mutations.
- **FR-007**: Protect dirty drafts for SPA, history, local replacement and supported unload navigation; preserve state across locale and responsive changes. Browser termination and crash recovery are not guaranteed.
- **FR-008**: Thai/English, keyboard access and responsive checks at 390/834/1440 are required. Maintain 44px touch targets where required by Plan 04.
- **FR-009**: Record dependency compatibility, pinned versions, official documentation and decision; use approved fallback only under the existing library policy.
- **FR-010**: Real photo data, patient-level production authorization, consent policy, capture, upload and persisted annotation stay blocked until separate verified contracts and activation tasks are complete.

## Edge cases and constraints

Missing or unknown consent is shown honestly. Missing consent presents specific guidance to obtain recorded consent through the clinic's authorized process, without offering an unverified capture or consent-write action; viewing policy beyond the approved plan is not invented. A photo without validated hash/dimensions cannot initialize the editor. Unknown or inaccessible file IDs fail closed without revealing filesystem paths. Treat unauthorized/malformed responses and stale patient context as errors, never as empty clinical findings. Existing clinical fixture IDs are isolated from front-desk identities; no cross-feature handoff is implied. Offline or force-killed browser recovery is not promised.

## Measurable outcomes

- **SC-001**: Automated tests demonstrate capability denial before read, identity isolation, valid and invalid fixture bytes/metadata, consent states and absence of write requests.
- **SC-002**: Browser tests demonstrate keyboard compare and annotation editing and dirty navigation cancellation/proceed at the stated breakpoints; record exact command, browser and limitations.
- **SC-003**: Schema round-trips canonical documents and normalized coordinates; malformed or unverified originals cannot produce a valid draft.
- **SC-004**: Fixture-only status and unresolved live backend, consent and clinical-UAT gates are explicit in delivery evidence. No claim of P0 completion is made.
