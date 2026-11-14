# Live photo save Implementation Plan

> **For agentic workers:** Use subagent-driven development; keep UI/design work with the designer and backend work with the implementation specialist.

**Goal:** Enable a clinician to manually save a synthetic lab photo from the existing workspace and verify the result in a real browser.

**Architecture:** Retain fixture mode as the default. Add an explicitly enabled lab-only live proxy that keeps each clinician's Frappe session server-side and forwards validated requests to the existing protected save/status API. Add an accessible manual upload control to the React photo workspace; only the server's `saved` outcome is success.

**Tech Stack:** React, TypeScript, Fastify, Frappe, Vitest and browser automation.

**Spec:** `docs/superpowers/specs/2026-11-11-live-photo-save-design.md`

## Global Constraints

- No image bytes in database rows; private filesystem and Frappe File authorization remain authoritative.
- Fixture mode stays unchanged and live mode is explicitly enabled only for the lab.
- A live request uses the originating clinician's native session, not a shared service account.
- Thai is default; English, accessibility, explicit manual saves and server-confirmed status are required.
- Current image-create API is synthetic-lab-only; do not imply general patient upload is supported.
- No replay or deletion of existing operations, queue clearing, credential disclosure, staging, commits, or pushes.

## Review Focus

- Expired native session: fail closed without exposing credentials.
- Missing, reused or cross-origin CSRF: reject before upstream writes.
- Wrong patient or unauthorized file: never expose metadata or private bytes.
- Unknown, pending, or timeout: never show saved or retry an uncertain write automatically.
- Oversized, invalid, or unsupported image: reject before forwarding and never log payload bytes.

## Task 1: Authenticated lab proxy

**Files:** `proxy/src/config.ts`, `proxy/src/server.ts`, `proxy/src/routes/`, `proxy/src/session/`, `proxy/src/read/` and focused tests.

- [ ] Add failing tests for live mode's explicit opt-in, per-clinician native session/CSRF, write and status forwarding, safe error mapping, private reads, request limits and all review-focus failures.
- [ ] Implement the smallest live adapter and routes consistent with the existing session and CSRF boundaries. Retain fixture behavior; use the backend's POST save/status contracts and never forward or return upstream session secrets.
- [ ] Run `pnpm --filter @clinic/proxy test` and the relevant typecheck; record exact results. Do not execute native publication during this task.

## Task 2: Manual save workspace

**Files:** `app/src/features/photos/`, `app/src/transport/`, `app/src/i18n/catalog/{th,en}.json`, focused component and transport tests.

- [ ] Add failing tests for explicit selection and save, capability denial, file validation, Thai/English copy, and pending/unknown/saved states.
- [ ] Designer implements an accessible responsive upload and status flow using the existing workspace style and Task 1's agreed API. No implicit upload or success before server confirmation.
- [ ] Run focused app tests and i18n parity tests; report exact results.

## Task 3: Reconcile and verify

- [ ] Review both lanes for authorization, private-file handling, session lifetime, idempotency and fixture-mode regressions; reconcile the actual request/response contracts before integration.
- [ ] Run `pnpm typecheck`, `pnpm lint`, relevant app/proxy tests and the existing Frappe unit suite; record command exits and failures.
- [ ] Deploy to the selected lab with synthetic data, enable only the controls needed for the test, and verify in a real browser: login, Thai/English, manual photo selection and save, server-confirmed status, and authorized private access. Restore temporary settings afterward; do not replay uncertain operations.
- [ ] Record what was directly verified and any remaining limitation; no commit or push.
