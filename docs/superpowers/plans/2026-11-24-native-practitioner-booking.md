# Native practitioner booking implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans. Steps use checkbox syntax for tracking.

**Goal:** Reception can find and book one doctor's available slot from the React form using Frappe’s existing production path.

**Architecture:** Extend the existing native booking transport and Reception form. Use the pinned Marley Desk source as the contract for link filtering, availability response interpretation and appointment fields; keep `Patient Appointment` as the only write.

**Tech stack:** React, TypeScript, Vitest/MSW, Frappe/Marley.

**Spec:** [`2026-11-24-native-practitioner-booking-design.md`](../specs/2026-11-24-native-practitioner-booking-design.md).

## Global constraints

No new production flag, extra access restriction, date cutoff, or independent scheduling policy. Preserve existing department booking and native authorization. Work from the latest `origin/sit-integration` in isolation. Do not deploy or write to a live site.

The user authorizes writes and prerequisite records only on isolated `marley-sit.local`. Use realistic but entirely fictional patient and staff records, ordinary clinic hours and appointment durations, and linked schedules and appointments that exercise genuinely open and occupied slots. Prefer existing suitable synthetic masters; create only missing test data. Do not use real patient data or change production or the separate lab.

## Review focus

- Desk availability is not a flat list: cover ordinary slots, occupied slots and Desk’s overlap/capacity behavior.
- Preserve non-default native slot metadata and native save errors; do not infer appointment identifiers on failure.
- Drop stale availability when practitioner, date or appointment context changes; distinguish no slots from a failed lookup.
- Do not render or retain other patients’ appointment details in UI state.
- Keep the existing department payload and production capability unchanged.

## Task 1: Native booking transport

- [ ] Read pinned Marley `patient_appointment.js` and `.py` at `dda107b26e3a0fe1dbd65c7257371235de054a04`. Pin the practitioner and appointment-type link filters, `get_availability_data` request, Desk slot metadata and final save payload in tests.
- [ ] Add failing Vitest/MSW cases in `app/src/transport/native/appointment-booking.test.ts`: availability sends doctor/date and native appointment context; displayed choices match Desk for occupied and capacity-sensitive slots; selecting a slot sends a native Practitioner appointment; native error does not become success. Keep the Department contract unchanged.
- [ ] Extend `app/src/transport/native/appointment-booking.ts` with `listPractitionerSlots(input, signal?)` and `createPractitionerAppointment(input)`, sharing existing response/error handling as appropriate. Reuse native authentication and the ordinary `Patient Appointment` insert.
- [ ] Run `pnpm --filter @clinic/app test src/transport/native/appointment-booking.test.ts` and verify all new and existing cases pass.

## Task 2: Reception form

- [ ] Add failing form tests alongside `app/src/features/reception/AppointmentBooking.tsx`: choose doctor and date, see/select a returned time, submit with that doctor; changing the context clears stale times; native errors remain visible; department-only booking still works.
- [ ] Extend `AppointmentBooking.tsx` and the existing English/Thai copy. Reuse the current production booking capability; do not modify backend gating. Keep the booking workflow on the existing page.
- [ ] Run the focused form tests, `pnpm typecheck`, and `pnpm --filter @clinic/app build:native`. Report exact results and any environment blockers.
- [ ] Before deployment, separately verify real Reception permissions, Desk/React slot parity, conflicting bookings and assigned-doctor handoff on approved staging. Repository tests alone do not establish installed-site readiness.
