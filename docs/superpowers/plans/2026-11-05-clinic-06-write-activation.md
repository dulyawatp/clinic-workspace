# Clinic Platform — 06 Gated Write Activation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Enable writes one operation at a time — patient registration/editing, booking lifecycle, check-in, visit drafts, medication ordering, visit completion, and the photo chain (consent → private upload → encounter link → versioned annotation) — each behind its own flag, durable operation log, reconciliation path and signed contract evidence. Anything without that evidence stays disabled.

**Architecture:** The proxy owns all writes. A minimal durable **operation log** (SQLite, one table) gives write-ahead records, duplicate suppression and reconciliation for each enabled operation. Each `WA-*` task adds one adapter + one route + one flag. Upstream call shapes come from a checked-in **contract manifest**; a missing or unverified entry makes the route return `UNSUPPORTED` and the UI stays `read_only`. No generic outbox framework, no speculative abstraction beyond the single log table and one adapter interface.

**Tech Stack:** Fastify 5, `better-sqlite3`, Zod, Vitest (`fastify.inject`), child-process restart harness, React Query mutations (only for enabled operations), Playwright.

**Spec:** `docs/superpowers/specs/2026-11-05-clinic-application-design.md`

**Parallelism:** Tasks 1–4 depend on Plan 01 only and may run in parallel with Plans 03–05. Task 5 depends on Plan 03. Tasks 6–7 depend on Plan 03. Tasks 8–10 depend on Plan 04. Task 11 depends on all enabled tasks. Tasks 14–17 depend on Plan 04 Task 5; Task 15 depends on Task 14, Task 16 on Task 15, Task 17 on Task 15. Task 13 depends on Plans 02–05 for test references and can be built last. Each activation task is independent of the others except Task 10's documented cascade condition.

## Global Constraints

**Routing contract:** no new pages; write activation reuses existing `ROUTES` entries and must not hardcode a path that bypasses them.
- Default state for every write: `read_only`. Enabling requires a flag **and** a manifest entry with `evidence: "verified"`.
- No direct `PUT status`, no `docstatus` set by client, no ledger/balance edit, no client-computed financial value.
- Every enabled mutation carries a `requestKey`; the proxy writes `pending` to durable storage **before** the upstream call.
- Reconciliation must survive proxy restart and browser restart. If it cannot be proven, the operation stays disabled.
- No PAN/CVV anywhere. Payment work is out of scope for this plan.
- Contract manifest entries are per operation and must cite the signed evidence file; do not invent endpoint names in code.
- Tests use fake upstreams only. No test may point at `10.0.100.10:8080`.

## Review Focus

1. **Double click on an enabled submit** — exactly one upstream call and one document. Test: `completes once for a duplicate request key`.
2. **Upstream commits, then the proxy dies before responding** — after restart, reconciliation returns `committed`, not a retry. Test: `reconciles committed after proxy restart`.
3. **Browser reload during an in-flight mutation** — re-query returns the stored state; no duplicate. Test: `does not repost after browser reload`.
4. **Permission revoked between page load and submit** — server rejects; UI shows forbidden; no retry loop. Test: `rejects a revoked permission without retrying`.
5. **Manifest entry marked unverified while a flag is on** — route refuses and the capability degrades to read-only. Test: `degraded route returns UNSUPPORTED when manifest changes to unverified` (Task 2).
6. **Photo retried after a partial chain** — consent exists, upload committed, link failed, or annotation save timed out: the same original must be reused and no duplicate file created. Test: `keeps the patient association when encounter linking fails` (Task 16) and `returns the same photo for a retried upload of the same bytes` (Task 15).

---

### Task 1: Durable operation log

**Files:**
- Create: `proxy/src/operations/store.ts`, `proxy/src/operations/store.test.ts`
- Create: `proxy/src/operations/schema.sql`

**Interfaces:**
- Produces: `OperationStore` with:
  - `begin(input: {userId, operation, requestKey, payloadHash}): {state: "new" | "pending" | "committed" | "failed" | "unknown", result?: unknown}`
  - `commit(requestKey, result): void`
  - `fail(requestKey, errorCode): void`
  - `markUnknown(requestKey): void`
  - `get(requestKey): OperationRecord | undefined`
  - `begin` is atomic; a duplicate `requestKey` returns the existing record instead of inserting.

- [ ] **Step 1: Write failing tests**

```ts
it("is atomic and returns the existing record for a duplicate key", () => {
  const first = store.begin({ userId: "u1", operation: "patient.create", requestKey: "k1", payloadHash: "h" });
  const second = store.begin({ userId: "u1", operation: "patient.create", requestKey: "k1", payloadHash: "h" });
  expect(first.state).toBe("new");
  expect(second.state).toBe("pending");
});
it("rejects reuse of a key with a different payload", () => {
  store.begin({ userId: "u1", operation: "patient.create", requestKey: "k1", payloadHash: "h1" });
  expect(() => store.begin({ userId: "u1", operation: "patient.create", requestKey: "k1", payloadHash: "h2" }))
    .toThrowError(/CONFLICT_DUPLICATE/);
});
it("persists committed results across reopen", () => {
  store.begin({ userId: "u1", operation: "patient.create", requestKey: "k1", payloadHash: "h" });
  store.commit("k1", { name: "PAT-0001" });
  expect(openStore(file).get("k1")?.state).toBe("committed");
});
it("stores no patient payload, only a hash", () => {
  store.begin({ userId: "u1", operation: "patient.create", requestKey: "k1", payloadHash: "sha256:abc" });
  expect(JSON.stringify(store.get("k1"))).not.toMatch(/สมชาย|081/);
});
```

- [ ] **Step 2: Run to verify failure**

Run: `pnpm --filter @clinic/proxy test src/operations/store.test.ts`
Expected: FAIL.

- [ ] **Step 3: Implement store**

Table: `operations(request_key TEXT PRIMARY KEY, user_id TEXT, operation TEXT, payload_hash TEXT, state TEXT, result_json TEXT, error_code TEXT, created_at INTEGER, updated_at INTEGER)`. Use `BEGIN IMMEDIATE` for `begin`. `result_json` holds only identifiers/status, never PHI.

- [ ] **Step 4: Run to verify pass**

Run: `pnpm --filter @clinic/proxy test src/operations/store.test.ts`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add proxy/src/operations
git commit -m "feat: add durable operation log with duplicate suppression"
```

---

### Task 2: Contract manifest and activation flags

**Files:**
- Create: `proxy/src/contracts/manifest.ts`, `contracts/backend-manifest.json`, `proxy/src/contracts/manifest.test.ts`
- Create: `proxy/src/contracts/activation.ts`

**Interfaces:**
- Produces: `type ManifestEntry = {operation: string, evidence: "verified" | "unverified" | "missing", evidenceFile?: string, method: string, path: string, requestSchema: string, cascade?: "none" | "orders"}`; `readManifest(): ManifestEntry[]`; `isActivatable(operation: string): boolean` (requires `evidence === "verified"`, non-empty fields and an existing evidence file); `ACTIVE_FLAGS: Set<string>` loaded from `CLINIC_ACTIVE_WRITES`.
- Produces: `OPERATION_CAPABILITY: Record<string, string>` mapping each operation to its capability name (e.g. `patient.create → write.patient.create`).
- Produces test/activation controls: `activate(operation)`, `deactivate(operation)`, `capabilityFor(capability: string): "enabled" | "read_only" | "blocked_unverified" | "denied" | "error_upstream"`, and a manifest test double exposing `set(operation, evidence)` and `setCascade(operation, cascade)`.
- Default `contracts/backend-manifest.json` contains every operation with `evidence: "missing"`.

- [ ] **Step 1: Write failing tests**

```ts
it("refuses to activate a missing contract", () => {
  expect(isActivatable("patient.create")).toBe(false);
});
it("refuses a verified entry with no evidence file", () => {
  vi.mocked(readManifest).mockReturnValue([{ operation: "patient.create", evidence: "verified", method: "POST", path: "/x", requestSchema: "s", evidenceFile: "" }]);
  expect(isActivatable("patient.create")).toBe(false);
});
it("activates only when flag and verified evidence agree", () => {
  activate("patient.create");
  expect(capabilityFor("write.patient.create")).toBe("enabled");
  deactivate("patient.create");
  expect(capabilityFor("write.patient.create")).toBe("read_only");
});
it("degraded route returns UNSUPPORTED when manifest changes to unverified", async () => {
  activate("patient.create");
  manifest.set("patient.create", "unverified");
  const res = await postPatient({});
  expect(res.statusCode).toBe(501);
});
```

- [ ] **Step 2: Run to verify failure**

Run: `pnpm --filter @clinic/proxy test src/contracts`
Expected: FAIL.

- [ ] **Step 3: Implement manifest and flags**

`isActivatable` also rejects entries whose `path` or `method` look like generic resource passthrough (`/api/resource/*` unrestricted) unless the manifest explicitly declares the fixed path and schema. No endpoint names appear in source code, only in the manifest.

- [ ] **Step 4: Run to verify pass**

Run: `pnpm --filter @clinic/proxy test src/contracts`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add proxy/src/contracts contracts/backend-manifest.json
git commit -m "feat: add contract manifest gate for write activation"
```

---

### Task 3: Write route middleware and reconciliation endpoint

**Files:**
- Create: `proxy/src/operations/middleware.ts`, `proxy/src/routes/write.ts`, `proxy/src/operations/middleware.test.ts`

**Interfaces:**
- Produces: `withIdempotency(operation, handler)` where handler is `(input, ctx) => Promise<{result, status?}>`; route `POST /api/write/:operation`; route `GET /api/write/:operation/status?requestKey=...` returning `{state, result?}` only for the requesting user.
- Consumes from Plan 01 Task 9: `CapabilityState` and proxy `buildCapabilityMap`. This plan does not redefine either. `setCapability(capability, state)` mutates the same map `buildCapabilityMap` returns.
- Produces test controls: `setCapability(capability: string, state: CapabilityState)`, `resetCapabilities()`; helpers in `proxy/test/helpers/api.ts` used by Tasks 5–10: `post(operation: string, body: unknown, opts?: {key?: string, user?: string, omitCsrf?: boolean, csrf?: string}): Promise<LightResponse>` and `status(operation: string, key: string, opts?: {user?: string}): Promise<LightResponse>`, where `LightResponse = {statusCode: number, json(): unknown}`. By default `post` logs in as `opts.user`, fetches `GET /api/auth/csrf`, and sends the returned token in Plan 01's `CSRF_HEADER`; `omitCsrf` suppresses only the header. The upstream fake is exported from the same helper module as `fake` with `calls: {operation, body}[]`, `respondFor(operation, status)` and `bodiesFor(operation)`; it is reachable only through the operation handler, so `fake.calls` stays empty on any rejection. `validBody` is the helper module's minimal valid request per operation.
- Every write route applies the Plan 01 protection chain before `withIdempotency`: `assertTrustedOrigin` → JSON content type → `CSRF_HEADER` verified against the session via Plan 01's `CsrfStore` (`purpose: "session"`) → session/permission. Missing or invalid CSRF is `403` and must not reach the operation log.
- Status codes: duplicate committed → `200` with first result; pending → `409 CONFLICT_DUPLICATE` with retry-after; unverified → `501 UNSUPPORTED`; forbidden/CSRF failure → `403`.

- [ ] **Step 1: Write failing tests**

```ts
it("returns the first result for a duplicate key", async () => {
  const a = await post("patient.create", validBody, { key: "k1" });
  const b = await post("patient.create", validBody, { key: "k1" });
  expect(b.statusCode).toBe(200);
  expect(b.json()).toEqual(a.json());
  expect(fake.calls).toHaveLength(1);
});
it("returns 409 while the first attempt is pending", async () => {
  holdUpstream();
  const a = post("patient.create", validBody, { key: "k1" });
  const b = await post("patient.create", validBody, { key: "k1" });
  expect(b.statusCode).toBe(409);
  releaseUpstream();
  await a;
});
it("does not expose another user's operation status", async () => {
  await post("patient.create", validBody, { key: "k1", user: "u1" });
  const res = await status("patient.create", "k1", { user: "u2" });
  expect(res.statusCode).toBe(404);
});
it("rejects a request with no request key", async () => {
  expect((await post("patient.create", {})).statusCode).toBe(400);
});
it("rejects a write without the proxy CSRF header", async () => {
  const res = await post("patient.create", validBody, { omitCsrf: true });
  expect(res.statusCode).toBe(403);
  expect(fake.calls).toHaveLength(0);
});
it("rejects a revoked permission without retrying", async () => {
  setCapability("write.patient.create", "denied");
  const res = await post("patient.create", {});
  expect(res.statusCode).toBe(403);
  expect(fake.calls).toHaveLength(0);
});
```

- [ ] **Step 2: Run to verify failure**

Run: `pnpm --filter @clinic/proxy test src/operations/middleware.test.ts`
Expected: FAIL.

- [ ] **Step 3: Implement middleware**

Flow: validate capability + manifest → validate body via the operation's Zod schema → `begin` → upstream call → `commit`/`fail`. On thrown timeout: `markUnknown`, return `202 {state:"unknown", requestKey}`. Reconciliation reads only the log; it never calls upstream while `pending`.

- [ ] **Step 4: Run to verify pass**

Run: `pnpm --filter @clinic/proxy test src/operations/middleware.test.ts`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add proxy/src/operations/middleware.ts proxy/src/routes/write.ts
git commit -m "feat: add idempotent write middleware and reconciliation route"
```

---

### Task 4: Restart and timeout harness

**Files:**
- Create: `proxy/test/restart.test.ts`, `proxy/test/helpers/fakeUpstream.ts`, `proxy/test/helpers/spawnProxy.ts`

**Interfaces:**
- Produces: test helper `withProxy(fn: (baseUrl) => Promise<void>)`; `restartProxy()`; external helpers `postExternal(baseUrl, operation, body, opts?)` and `getStatusExternal(baseUrl, operation, key)`; fake upstream `fake` with `calls: Array<{operation, path}>`, `lastBody`, `bodiesFor(operation)`, `respondWith(status, body)`, `respondFor(operation, status)`, `commitThenHang()`, `hangBeforeCommit()`, `failAfterCommit()`, `release()`; database file shared across restarts.

- [ ] **Step 1: Write failing tests**

```ts
it("reconciles committed after proxy restart", async () => {
  await withProxy(async (url) => {
    fake.commitThenHang();
    const first = await postExternal(url, "patient.create", { key: "k1" });      // 202 unknown
    await restartProxy();
    const status = await getStatusExternal(url, "patient.create", "k1");
    expect(status.state).toBe("committed");
    expect(status.result).toMatchObject({ name: expect.stringMatching(/^PAT-/) });
    expect(fake.calls).toHaveLength(1);
  });
});
it("does not repost after browser reload", async () => {
  await withProxy(async (url) => {
    await postExternal(url, "patient.create", { key: "k2" });
    const again = await getStatusExternal(url, "patient.create", "k2");
    expect(again.state).toBe("committed");
    expect(fake.calls).toHaveLength(1);
  });
});
it("shows unknown, not failed, when outcome is indeterminate", async () => {
  await withProxy(async (url) => {
    fake.hangBeforeCommit();
    const res = await postExternal(url, "patient.create", { key: "k3" });
    expect(res.statusCode).toBe(202);
    expect(res.json().state).toBe("unknown");
  });
});
```

- [ ] **Step 2: Run to verify failure**

Run: `pnpm --filter @clinic/proxy test test/restart.test.ts`
Expected: FAIL.

- [ ] **Step 3: Implement harness**

Spawn `node dist/server.js` on an ephemeral port with a temp database path; poll `/api/health` before each request. Fake upstream is injected via `CLINIC_UPSTREAM=fake` with an in-process control channel on a second port.

- [ ] **Step 4: Run to verify pass**

Run: `pnpm --filter @clinic/proxy test test/restart.test.ts`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add proxy/test
git commit -m "test: prove restart and timeout reconciliation"
```

---

### Task 5: WA-PAT-01 — patient registration and editing

**Files:**
- Create: `proxy/src/operations/patient.ts`, `proxy/src/operations/patient.schema.ts`, `proxy/src/operations/patient.test.ts`
- Create: `app/src/features/patients/RegisterPatientForm.tsx`, `RegisterPatientForm.test.tsx`
- Modify: `contracts/backend-manifest.json`, `app/src/features/patients/PatientDirectoryPage.tsx`

**Interfaces:**
- Consumes: Task 1 `OperationStore`, Task 2 `isActivatable`, Task 3 `withIdempotency`.
- Produces: `registerPatient(input: RegisterPatientInput): Promise<{name: string}>`; `updatePatient(input: UpdatePatientInput): Promise<{name: string}>`; Zod schema includes `firstName`, `lastName?`, `sex`, `dob?`, `mobile?`, `allergies: "known"|"none_reported"|"unknown"`; UI mutation hook `useRegisterPatient()`.

- [ ] **Step 1: Write failing tests**

```ts
it("does not send layout or unknown fields upstream", async () => {
  activate("patient.create");
  await post("patient.create", validPatient({ physical_examination: "x" }));
  expect(fake.lastBody).not.toHaveProperty("physical_examination");
});
it("keeps known/none/unknown allergy states distinct", async () => {
  activate("patient.create");
  for (const a of ["known", "none_reported", "unknown"]) {
    await post("patient.create", validPatient({ allergies: a }));
    expect(fake.lastBody.allergies).toBe(a);
  }
});
it("stores the created reference and returns it to the UI", async () => {
  activate("patient.create");
  const res = await post("patient.create", validPatient());
  expect(res.json()).toEqual({ name: "PAT-0001" });
});
it("stays read-only when the manifest is missing", async () => {
  const res = await post("patient.create", validPatient());
  expect(res.statusCode).toBe(501);
});
```

- [ ] **Step 2: Run to verify failure**

Run: `pnpm --filter @clinic/proxy test src/operations/patient.test.ts`
Expected: FAIL.

- [ ] **Step 3: Implement adapter, schema and form**

Form fields per spec F01; on success show Patient ID and next actions (all gated). Add the manifest entry as `unverified` (not `verified`) — the task ships the code, not the permission.

- [ ] **Step 4: Run to verify pass**

Run: `pnpm --filter @clinic/proxy test src/operations/patient.test.ts && pnpm --filter @clinic/app test src/features/patients`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add proxy/src/operations/patient* app/src/features/patients/RegisterPatientForm* contracts/backend-manifest.json
git commit -m "feat: add gated patient registration and edit"
```

---

### Task 6: WA-APT-01 — booking lifecycle

**Files:**
- Create: `proxy/src/operations/appointment.ts`, `appointment.schema.ts`, `appointment.test.ts`
- Create: `app/src/features/appointments/BookAppointmentForm.tsx`, `RescheduleDialog.tsx`, `appointment-actions.test.tsx`
- Modify: `contracts/backend-manifest.json`

**Interfaces:**
- Produces: `bookAppointment(input): Promise<{name, status}>`; `rescheduleAppointment({name, date, time, reason})`; `cancelAppointment({name, reason})`; hooks `useBookAppointment()`, `useReschedule()`, `useCancel()`; capabilities `write.appointment.book`.

- [ ] **Step 1: Write failing tests**

```ts
it("sends patient, practitioner, date, time and company only", async () => {
  activate("appointment.book");
  await post("appointment.book", validBooking());
  expect(Object.keys(fake.lastBody).sort()).toEqual(["appointment_date","appointment_time","company","patient","practitioner"]);
});
it("treats a server conflict as a displayed conflict, not a success", async () => {
  activate("appointment.book");
  fake.respondWith(409, { code: "CONFLICT_STALE" });
  const res = await post("appointment.book", validBooking());
  expect(res.statusCode).toBe(409);
  expect(res.json().code).toBe("CONFLICT_STALE");
});
it("requires a reason for cancellation", async () => {
  activate("appointment.cancel");
  const res = await post("appointment.cancel", { name: "APT-1" });
  expect(res.statusCode).toBe(400);
});
it("prevents a duplicate booking for the same request key", async () => {
  activate("appointment.book");
  await post("appointment.book", validBooking(), { key: "k1" });
  await post("appointment.book", validBooking(), { key: "k1" });
  expect(fake.calls).toHaveLength(1);
});
it("disables the form when the capability is read_only", async () => {
  renderWithCaps({}, <BookAppointmentForm />);
  expect(screen.getByRole("button", { name: /ยืนยัน|confirm/i })).toBeDisabled();
});
```

- [ ] **Step 2: Run to verify failure**

Run: `pnpm --filter @clinic/proxy test src/operations/appointment.test.ts`
Expected: FAIL.

- [ ] **Step 3: Implement booking, reschedule, cancel**

Server availability is authoritative; the client sends the selected slot and renders conflict responses. Cancel/reschedule require `reason`. Manifest entries ship `unverified`.

- [ ] **Step 4: Run to verify pass**

Run: `pnpm --filter @clinic/proxy test src/operations/appointment.test.ts && pnpm --filter @clinic/app test src/features/appointments`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add proxy/src/operations/appointment* app/src/features/appointments contracts/backend-manifest.json
git commit -m "feat: add gated appointment booking lifecycle"
```

---

### Task 7: WA-APT-02 — check-in (no direct status write)

**Files:**
- Create: `proxy/src/operations/checkin.ts`, `checkin.test.ts`
- Modify: `app/src/features/reception/QueuePage.tsx`, `contracts/backend-manifest.json`

**Interfaces:**
- Produces: `checkInAppointment({appointmentName, requestKey}): Promise<{appointment, checkedInAt}>`; capability `write.appointment.checkin`; UI hook `useCheckIn()`.
- Hard rule: the adapter must call a **named method** from the manifest. A manifest entry whose `method` is `PUT` against the appointment document is rejected at load time.

- [ ] **Step 1: Write failing tests**

```ts
it("rejects a manifest that writes status directly", () => {
  expect(() => loadManifestWith({ operation: "appointment.checkin", method: "PUT", path: "/api/resource/Patient Appointment/X" }))
    .toThrowError(/direct status write/);
});
it("performs exactly one check-in for a duplicate click", async () => {
  activate("appointment.checkin");
  await post("appointment.checkin", { appointmentName: "APT-1" }, { key: "k1" });
  await post("appointment.checkin", { appointmentName: "APT-1" }, { key: "k1" });
  expect(fake.calls).toHaveLength(1);
});
it("returns the server timestamp, not the browser clock", async () => {
  activate("appointment.checkin");
  fake.respondWith(200, { checkedInAt: "2026-11-05T03:00:00Z" });
  const res = await post("appointment.checkin", { appointmentName: "APT-1" });
  expect(res.json().checkedInAt).toBe("2026-11-05T03:00:00Z");
});
it("keeps the action disabled without a verified method", async () => {
  expect((await post("appointment.checkin", { appointmentName: "APT-1" })).statusCode).toBe(501);
});
```

- [ ] **Step 2: Run to verify failure**

Run: `pnpm --filter @clinic/proxy test src/operations/checkin.test.ts`
Expected: FAIL.

- [ ] **Step 3: Implement check-in adapter and UI wiring**

Manifest ships with `evidence: "missing"` and no method. The route returns `501` until a verified entry exists. UI shows the button disabled with the reason from Plan 03.

- [ ] **Step 4: Run to verify pass**

Run: `pnpm --filter @clinic/proxy test src/operations/checkin.test.ts`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add proxy/src/operations/checkin* app/src/features/reception/QueuePage.tsx contracts/backend-manifest.json
git commit -m "feat: add check-in behind verified-method gate"
```

---

### Task 8: WA-VST-01a — visit section drafts

**Files:**
- Create: `proxy/src/operations/encounter.ts`, `encounter.schema.ts`, `encounter.test.ts`
- Create: `app/src/features/clinical/DraftSection.tsx`, `useSaveSection.ts`, `draft-section.test.tsx`
- Modify: `contracts/backend-manifest.json`

**Interfaces:**
- Produces: `saveVisitSection({encounterId, section: "history"|"exam"|"note", payload}): Promise<{savedAt, sectionState}>`; capability `write.encounter.draft`; hook `useSaveSection(section)`.
- Per-section persistence: response reports only that section; the UI reports per-section state.

- [ ] **Step 1: Write failing tests**

```ts
it("reports the failing section without claiming global success", async () => {
  activate("encounter.draft");
  fake.respondFor("note", 500);
  const res = await post("encounter.draft", { encounterId: "ENC-1", section: "note", payload: { text: "x" } });
  expect(res.statusCode).toBe(502);
  const ui = renderDraftHarness();
  expect(await ui.findByText(/บันทึกไม่สำเร็จ|save failed/i)).toBeVisible();
  expect(ui.queryByText(/บันทึกแล้วทั้งหมด|all saved/i)).toBeNull();
});
it("keeps a committed section on retry of another section", async () => {
  activate("encounter.draft");
  await post("encounter.draft", { encounterId: "ENC-1", section: "history", payload: { text: "h" } }, { key: "kh" });
  fake.respondFor("exam", 500);
  await post("encounter.draft", { encounterId: "ENC-1", section: "exam", payload: { text: "e" } }, { key: "ke" });
  expect(fake.bodiesFor("history")).toHaveLength(1);
});
it("does not submit orders on draft save", async () => {
  activate("encounter.draft");
  await post("encounter.draft", { encounterId: "ENC-1", section: "note", payload: { text: "x" } });
  expect(fake.calls.some((c) => /order|procedure|lab/i.test(c.operation))).toBe(false);
});
it("preserves text across locale switch", async () => {
  const ui = renderDraftHarness();
  await ui.user.type(ui.screen.getByLabelText(/note/i), "ข้อความ");
  await ui.user.click(ui.screen.getByRole("button", { name: "EN" }));
  expect(ui.screen.getByLabelText(/note/i)).toHaveValue("ข้อความ");
});
```

- [ ] **Step 2: Run to verify failure**

Run: `pnpm --filter @clinic/proxy test src/operations/encounter.test.ts`
Expected: FAIL.

- [ ] **Step 3: Implement draft save**

One request per section; request key per section attempt; manifest entries per section or one entry with a declared section parameter. No cascade behaviour.

- [ ] **Step 4: Run to verify pass**

Run: `pnpm --filter @clinic/proxy test src/operations/encounter.test.ts && pnpm --filter @clinic/app test src/features/clinical`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add proxy/src/operations/encounter* app/src/features/clinical contracts/backend-manifest.json
git commit -m "feat: add per-section visit draft persistence"
```

---

### Task 9: WA-MED-01 — medication ordering

**Files:**
- Create: `proxy/src/operations/medication.ts`, `medication.schema.ts`, `medication.test.ts`
- Create: `app/src/features/clinical/MedicationSection.tsx`, `MedicationRow.tsx`, `medication.test.tsx`
- Modify: `contracts/backend-manifest.json`

**Interfaces:**
- Produces: `saveMedicationOrders({encounterId, lines: MedicationLine[]}): Promise<{orders: MedicationLine[], state}>`; capability `write.medication.order`; `MedicationLine = {medicationId, dose, unit, frequency?, durationDays?, instructions?}`.

- [ ] **Step 1: Write failing tests**

```ts
it("does not mark an order as dispensed", async () => {
  activate("medication.order");
  const res = await post("medication.order", { encounterId: "ENC-1", lines: [line()] });
  expect(JSON.stringify(res.json())).not.toMatch(/dispensed|จ่ายยาแล้ว/i);
});
it("keeps order identity stable for reconciliation", async () => {
  activate("medication.order");
  await post("medication.order", { encounterId: "ENC-1", lines: [line()] }, { key: "k1" });
  await post("medication.order", { encounterId: "ENC-1", lines: [line()] }, { key: "k1" });
  expect(fake.calls).toHaveLength(1);
});
it("rejects a non-positive dose before any upstream call", async () => {
  activate("medication.order");
  const res = await post("medication.order", { encounterId: "ENC-1", lines: [line({ dose: 0 })] });
  expect(res.statusCode).toBe(400);
  expect(fake.calls).toHaveLength(0);
});
it("does not auto-submit orders from a draft save", async () => {
  activate("encounter.draft");
  await post("encounter.draft", { encounterId: "ENC-1", section: "note", payload: { text: "x" } });
  expect(fake.calls.some((c) => c.operation === "medication.order")).toBe(false);
});
it("disables ordering independently of visit completion", async () => {
  renderWithCaps({ "write.encounter.complete": "enabled" }, <MedicationSection />);
  expect(screen.getByRole("button", { name: /เพิ่มยา|add medication/i })).toBeDisabled();
});
```

- [ ] **Step 2: Run to verify failure**

Run: `pnpm --filter @clinic/proxy test src/operations/medication.test.ts`
Expected: FAIL.

- [ ] **Step 3: Implement ordering**

Saves the order set for the encounter; response state is `ordered` only. No dispense/consume/invoice side effects.

- [ ] **Step 4: Run to verify pass**

Run: `pnpm --filter @clinic/proxy test src/operations/medication.test.ts && pnpm --filter @clinic/app test src/features/clinical`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add proxy/src/operations/medication* app/src/features/clinical contracts/backend-manifest.json
git commit -m "feat: add gated medication ordering"
```

---

### Task 10: WA-VST-01b — visit completion and sign-off

**Files:**
- Create: `proxy/src/operations/complete-visit.ts`, `complete-visit.test.ts`
- Create: `app/src/features/clinical/ReviewComplete.tsx`, `review-complete.test.tsx`
- Modify: `contracts/backend-manifest.json`

**Interfaces:**
- Produces: `completeVisit({encounterId, noteVersion, requestKey}): Promise<{encounterId, signedAt, cascade: "none"|"orders"}>`; capability `write.encounter.complete`.
- Dependency rule: if the verified contract declares a submit cascade that creates orders, the operation requires `write.medication.order` to be enabled and returns `409 PRECONDITION` otherwise. If the contract declares no cascade, no dependency.

- [ ] **Step 1: Write failing tests**

```ts
it("refuses completion when the contract declares an order cascade and ordering is disabled", async () => {
  activate("encounter.complete"); // ordering disabled
  manifest.setCascade("encounter.complete", "orders");
  const res = await post("encounter.complete", { encounterId: "ENC-1", noteVersion: 3 });
  expect(res.statusCode).toBe(409);
  expect(res.json().code).toBe("PRECONDITION");
});
it("completes once for a duplicate request key", async () => {
  activate("encounter.complete");
  await post("encounter.complete", { encounterId: "ENC-1", noteVersion: 3 }, { key: "k1" });
  await post("encounter.complete", { encounterId: "ENC-1", noteVersion: 3 }, { key: "k1" });
  expect(fake.calls).toHaveLength(1);
});
it("treats a stale note version as a conflict, not a success", async () => {
  activate("encounter.complete");
  fake.respondWith(409, { code: "CONFLICT_STALE" });
  const res = await post("encounter.complete", { encounterId: "ENC-1", noteVersion: 2 });
  expect(res.json().code).toBe("CONFLICT_STALE");
});
it("shows exactly what will be submitted before confirmation", async () => {
  renderWithCaps({ "write.encounter.complete": "enabled" }, <ReviewComplete />);
  await user.click(await screen.findByRole("button", { name: /ตรวจสอบ|review/i }));
  expect(await screen.findByTestId("submit-summary")).toHaveTextContent(/Clinical Note/);
  expect(screen.getByRole("button", { name: /ยืนยัน|confirm/i })).toBeInTheDocument();
});
it("does not present a draft as signed after saving", async () => {
  activate("encounter.draft");
  await post("encounter.draft", { encounterId: "ENC-1", section: "note", payload: { text: "x" } });
  expect(fake.calls.some((c) => c.operation === "encounter.complete")).toBe(false);
});
```

- [ ] **Step 2: Run to verify failure**

Run: `pnpm --filter @clinic/proxy test src/operations/complete-visit.test.ts`
Expected: FAIL.

- [ ] **Step 3: Implement completion**

Requires a reviewed summary, an exact note version, and a signed method from the manifest. Response records `signedAt` from the server only. Never re-submit on timeout; use reconciliation.

- [ ] **Step 4: Run to verify pass**

Run: `pnpm --filter @clinic/proxy test src/operations/complete-visit.test.ts && pnpm --filter @clinic/app test src/features/clinical`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add proxy/src/operations/complete-visit* app/src/features/clinical contracts/backend-manifest.json
git commit -m "feat: add gated visit completion with cascade precondition"
```

---

### Task 11: Activation evidence gate and release check

**Files:**
- Create: `scripts/verify-activations.mjs`, `scripts/verify-activations.test.mjs`
- Create: `docs/backend/ACTIVATION_EVIDENCE.md` (template, not evidence)
- Modify: `contracts/backend-manifest.json` validation only

**Interfaces:**
- Produces: `node scripts/verify-activations.mjs` exits non-zero when a flag is on without `evidence: "verified"`, a missing evidence file, a missing restart test reference, or a capability enabled for an operation with no route.
- Output: per-operation table `operation | flag | manifest | evidence file | route | result`.

- [ ] **Step 1: Write failing tests**

```js
test("fails when a flag is active without verified evidence", async () => {
  const r = run({ flags: ["patient.create"], manifest: { "patient.create": { evidence: "unverified" } } });
  expect(r.status).toBe(1);
  expect(r.stdout).toContain("patient.create");
});
test("fails when the evidence file is absent", async () => {
  const r = run({ flags: ["appointment.book"], manifest: { "appointment.book": { evidence: "verified", evidenceFile: "docs/backend/missing.md" } } });
  expect(r.status).toBe(1);
});
test("passes only when every active operation is verified and routed", async () => {
  const r = run({ flags: [], manifest: allMissing() });
  expect(r.status).toBe(0);
});
```

- [ ] **Step 2: Run to verify failure**

Run: `node --test scripts/verify-activations.test.mjs`
Expected: FAIL.

- [ ] **Step 3: Implement verification script**

Read the manifest, env flags and route registry. Cross-check restart test names in `proxy/test/restart.test.ts` by operation marker comment. Print an explicit `NO ACTIVE WRITES` line when the flag set is empty (the expected default).

- [ ] **Step 4: Run to verify pass**

Run: `node --test scripts/verify-activations.test.mjs && node scripts/verify-activations.mjs`
Expected: PASS; script prints `NO ACTIVE WRITES`.

- [ ] **Step 5: Commit**

```bash
git add scripts/verify-activations.mjs scripts/verify-activations.test.mjs docs/backend/ACTIVATION_EVIDENCE.md
git commit -m "chore: add write activation evidence gate"
```

---

### Task 12: End-to-end activation rehearsal (fixture only)

**Files:**
- Create: `app/e2e/activation.spec.ts`, `proxy/test/activation.test.ts`

**Interfaces:**
- Produces: proof that one operation can be enabled without enabling neighbours, and that partial failure does not block other screens.

- [ ] **Step 1: Write failing tests**

```ts
test("enabling patient create leaves booking disabled", async ({ page }) => {
  await startProxy({ active: ["patient.create"] });
  await loginAs(page, "REC");
  await page.goto("/clinic/patients/new");
  await expect(page.getByRole("button", { name: /บันทึก|save/i })).toBeEnabled();
  await page.goto("/clinic/appointments/new");
  await expect(page.getByRole("button", { name: /ยืนยัน|confirm/i })).toBeDisabled();
});
test("a blocked clinical task does not break the reception read screen", async ({ page }) => {
  await startProxy({ active: ["patient.create"] });
  await loginAs(page, "REC"); await page.goto("/clinic/reception");
  await expect(page.getByTestId("kpi-todayAppointments")).toBeVisible();
});
test("disabling a flag returns the control to read-only", async ({ page }) => {
  await startProxy({ active: [] });
  await loginAs(page, "REC"); await page.goto("/clinic/patients/new");
  await expect(page.getByRole("button", { name: /บันทึก|save/i })).toBeDisabled();
});
```

- [ ] **Step 2: Run to verify failure**

Run: `pnpm --filter @clinic/app e2e e2e/activation.spec.ts`
Expected: FAIL.

- [ ] **Step 3: Implement fixture activation harness**

`startProxy({active})` sets `CLINIC_ACTIVE_WRITES` and the fake upstream. No real backend calls.

- [ ] **Step 4: Run to verify pass**

Run: `pnpm -w test && pnpm --filter @clinic/app e2e`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/e2e/activation.spec.ts proxy/test/activation.test.ts
git commit -m "test: prove independent write activation"
```

---

### Task 13: Acceptance evidence harness (matrix ownership)

**Files:**
- Create: `scripts/acceptance.mjs`, `scripts/acceptance.test.mjs`
- Create: `docs/acceptance/AT_MATRIX.md`

**Interfaces:**
- Produces: `node scripts/acceptance.mjs --write-report` emits `docs/acceptance/AT_MATRIX.md` with one row per AT-01…25 and V3-AT-01…16: `id | priority | owning plan/task | test command | last result | evidence path | status`.
- Default status for every row is `not_run`. A row becomes `pass` only when the referenced test exits 0 **and** an evidence artefact path exists. `declined` requires a linked scope-change note.

- [ ] **Step 1: Write failing tests**

```js
test("defaults every row to not_run", async () => {
  const report = await buildReport({ run: false });
  expect(report.rows.every((r) => r.status === "not_run")).toBe(true);
});
test("refuses pass without an evidence artefact", async () => {
  const report = await buildReport({ rows: [{ id: "AT-01", testExit: 0, evidencePath: "" }] });
  expect(report.rows[0].status).toBe("not_run");
});
test("lists every P0 and P1 acceptance id", async () => {
  const ids = (await buildReport({ run: false })).rows.map((r) => r.id);
  for (let i = 1; i <= 25; i++) expect(ids).toContain(`AT-${String(i).padStart(2, "0")}`);
  for (let i = 1; i <= 16; i++) expect(ids).toContain(`V3-AT-${String(i).padStart(2, "0")}`);
});
```

- [ ] **Step 2: Run to verify failure**

Run: `node --test scripts/acceptance.test.mjs`
Expected: FAIL.

- [ ] **Step 3: Implement harness and matrix file**

The matrix maps each row to the plan/task whose tests cover it (from the self-review tables), or `orchestrator` for rows requiring clinic hardware/staff review. Do not execute tests by default; `--run` is opt-in and never points at a live site.

- [ ] **Step 4: Run to verify pass**

Run: `node --test scripts/acceptance.test.mjs && node scripts/acceptance.mjs --write-report`
Expected: PASS; report exists with all rows `not_run`.

- [ ] **Step 5: Commit**

```bash
git add scripts/acceptance.mjs scripts/acceptance.test.mjs docs/acceptance/AT_MATRIX.md
git commit -m "test: add acceptance evidence harness with not_run default"
```

---

### Task 14: WA-PHO-01 — consent capture

**Files:**
- Create: `proxy/src/operations/photo-consent.ts`, `photo-consent.schema.ts`, `photo-consent.test.ts`
- Create: `app/src/features/photos/ConsentCapture.tsx`, `consent-capture.test.tsx`
- Modify: `contracts/backend-manifest.json`

**Interfaces:**
- Produces: `recordPhotoConsent({patientId, scope: "before_after" | "profile" | "clinical", policyVersion, signerName, method: "paper" | "signature" | "verbal_witnessed"}): Promise<{consentId, recordedAt, scope}>`; capability `write.photo.consent`; manifest operation `photo.consent`.
- Consent is a precondition for upload (Task 15), not a substitute for it. Revocation is not modelled; it needs its own approved lifecycle.

- [ ] **Step 1: Write failing tests**

```ts
it("requires a policy version and an explicit scope", async () => {
  activate("photo.consent");
  expect((await post("photo.consent", validConsent({ policyVersion: "" }))).statusCode).toBe(400);
  expect((await post("photo.consent", validConsent({ scope: undefined }))).statusCode).toBe(400);
});
it("records one consent for a duplicate request key", async () => {
  activate("photo.consent");
  await post("photo.consent", validConsent(), { key: "c1" });
  await post("photo.consent", validConsent(), { key: "c1" });
  expect(fake.calls).toHaveLength(1);
});
it("rejects consent for an unknown patient before any upstream call", async () => {
  activate("photo.consent");
  fake.respondWith(404, { code: "NOT_FOUND" });
  expect((await post("photo.consent", validConsent({ patientId: "NOPE" }))).statusCode).toBe(404);
});
it("stays disabled until the manifest is verified", async () => {
  expect((await post("photo.consent", validConsent())).statusCode).toBe(501);
});
it("keeps submit disabled until a scope is chosen", async () => {
  renderWithCaps({ "write.photo.consent": "enabled" }, <ConsentCapture />);
  expect(screen.getByRole("button", { name: /บันทึกความยินยอม|record consent/i })).toBeDisabled();
  await user.selectOptions(screen.getByLabelText(/ขอบเขต|scope/i), "before_after");
  expect(screen.getByRole("button", { name: /บันทึกความยินยอม|record consent/i })).toBeEnabled();
});
```

- [ ] **Step 2: Run to verify failure**

Run: `pnpm --filter @clinic/proxy test src/operations/photo-consent.test.ts`
Expected: FAIL.

- [ ] **Step 3: Implement consent capture**

Store scope, policy version, signer, method, actor and server timestamp. The UI requires scope selection before enabling submit; submit stays behind `WriteGate capability="write.photo.consent"`. Manifest ships `unverified`.

- [ ] **Step 4: Run to verify pass**

Run: `pnpm --filter @clinic/proxy test src/operations/photo-consent.test.ts && pnpm --filter @clinic/app test src/features/photos`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add proxy/src/operations/photo-consent* app/src/features/photos contracts/backend-manifest.json
git commit -m "feat: add gated photo consent capture"
```

---

### Task 15: WA-PHO-02 — private photo upload

**Files:**
- Create: `proxy/src/operations/photo-upload.ts`, `photo-upload.schema.ts`, `photo-upload.test.ts`
- Create: `app/src/features/photos/CapturePhotoForm.tsx`, `capture-photo.test.tsx`
- Modify: `contracts/backend-manifest.json`

**Interfaces:**
- Produces: `uploadPhoto({patientId, consentId, file, category, bodyArea, capturedAt}): Promise<{photoId, fileId, sha256, bytes, private: true}>`; capability `write.photo.upload`; operation `photo.upload`; multipart proxy route.
- Hard rules: consent must already be `recorded` for this patient and scope; `is_private` is always set by the proxy; bytes are stored unchanged (no re-encode, no resize); retry with the same request key returns the same `photoId`; dedupe on `(patientId, sha256)` returns the existing photo rather than a second copy.

- [ ] **Step 1: Write failing tests**

```ts
it("refuses an upload with no recorded consent", async () => {
  activate("photo.upload");
  fake.consentState = "missing";
  const res = await postFile("photo.upload", pngFile, { patientId: "PAT-1", consentId: "C-1" });
  expect(res.statusCode).toBe(409);
  expect(res.json().code).toBe("PRECONDITION");
  expect(fake.calls).toHaveLength(0);
});
it("stores the original bytes unchanged and marks the file private", async () => {
  activate("photo.upload");
  const res = await postFile("photo.upload", pngFile, { patientId: "PAT-1", consentId: "C-1" });
  expect(res.json().private).toBe(true);
  expect(res.json().sha256).toBe(sha256(pngFile));
  expect(res.json().bytes).toBe(pngFile.byteLength);
  expect(fake.lastFileBytes).toEqual(pngFile);
});
it("returns the same photo for a retried upload of the same bytes", async () => {
  activate("photo.upload");
  const a = await postFile("photo.upload", pngFile, { patientId: "PAT-1", consentId: "C-1" }, { key: "u1" });
  const b = await postFile("photo.upload", pngFile, { patientId: "PAT-1", consentId: "C-1" }, { key: "u1" });
  expect(b.json().photoId).toBe(a.json().photoId);
  expect(fake.storageWrites).toHaveLength(1);
});
it("rejects a disallowed content type before storing", async () => {
  activate("photo.upload");
  const res = await postFile("photo.upload", textFile, { patientId: "PAT-1", consentId: "C-1" });
  expect(res.statusCode).toBe(415);
  expect(fake.storageWrites).toHaveLength(0);
});
it("never returns a public URL", async () => {
  activate("photo.upload");
  const res = await postFile("photo.upload", pngFile, { patientId: "PAT-1", consentId: "C-1" });
  expect(JSON.stringify(res.json())).not.toMatch(/https?:\/\//);
});
it("falls back to file upload when the camera is unavailable", async () => {
  renderWithCaps({ "write.photo.upload": "enabled" }, <CapturePhotoForm />);
  await user.click(screen.getByRole("button", { name: /ถ่ายภาพ|capture/i }));
  expect(screen.getByText(/กล้องไม่พร้อมใช้งาน|camera unavailable/i)).toBeVisible();
  expect(screen.getByLabelText(/อัปโหลด|upload/i)).toBeVisible();
});
```

- [ ] **Step 2: Run to verify failure**

Run: `pnpm --filter @clinic/proxy test src/operations/photo-upload.test.ts`
Expected: FAIL.

- [ ] **Step 3: Implement upload**

Proxy streams to private storage, computes SHA-256, writes the operation log before naming the file. UI capture uses `capture="environment"`; on the HTTP evaluation host the camera is unavailable, so the form surfaces the upload fallback with an explanation rather than a silent failure. Submit gated by `write.photo.upload`; manifest `unverified`.

- [ ] **Step 4: Run to verify pass**

Run: `pnpm --filter @clinic/proxy test src/operations/photo-upload.test.ts && pnpm --filter @clinic/app test src/features/photos`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add proxy/src/operations/photo-upload* app/src/features/photos contracts/backend-manifest.json
git commit -m "feat: add gated private photo upload preserving originals"
```

---

### Task 16: WA-PHO-03 — link the same original to the encounter

**Files:**
- Create: `proxy/src/operations/photo-link.ts`, `photo-link.test.ts`
- Modify: `app/src/features/photos/PhotoGalleryPage.tsx`, `contracts/backend-manifest.json`

**Interfaces:**
- Produces: `linkPhotoToEncounter({photoId, encounterId}): Promise<{photoId, fileId, encounterId, linkedAt}>`; capability `write.photo.link`; operation `photo.link`.
- Hard rules: linking never creates a new file and never re-uploads bytes; repeated linking is idempotent; if encounter linking fails, the patient-level association remains and the failure is retryable.

- [ ] **Step 1: Write failing tests**

```ts
it("links the existing original without a second storage write", async () => {
  activate("photo.link");
  const res = await post("photo.link", { photoId: "PH-1", encounterId: "ENC-1" });
  expect(res.json().fileId).toBe("FILE-1");
  expect(fake.storageWrites).toHaveLength(0);
});
it("is idempotent for repeated linking", async () => {
  activate("photo.link");
  await post("photo.link", { photoId: "PH-1", encounterId: "ENC-1" }, { key: "l1" });
  await post("photo.link", { photoId: "PH-1", encounterId: "ENC-1" }, { key: "l1" });
  expect(fake.calls).toHaveLength(1);
});
it("keeps the patient association when encounter linking fails", async () => {
  activate("photo.link");
  fake.respondWith(404, { code: "NOT_FOUND" });
  const res = await post("photo.link", { photoId: "PH-1", encounterId: "NOPE" });
  expect(res.statusCode).toBe(404);
  const photo = await readPhoto("PH-1");
  expect(photo.patientId).toBe("PAT-1");
  expect(photo.encounterId).toBeNull();
});
it("refuses to link a photo the user cannot read", async () => {
  activate("photo.link");
  setCapability("read.photo", "denied");
  expect((await post("photo.link", { photoId: "PH-9", encounterId: "ENC-1" })).statusCode).toBe(403);
});
```

- [ ] **Step 2: Run to verify failure**

Run: `pnpm --filter @clinic/proxy test src/operations/photo-link.test.ts`
Expected: FAIL.

- [ ] **Step 3: Implement linking**

Link record stores `photoId`, `fileId`, `encounterId`, actor and timestamp; the original file id is immutable. UI shows a gated "Link to visit" action with a retry state. Manifest `unverified`.

- [ ] **Step 4: Run to verify pass**

Run: `pnpm --filter @clinic/proxy test src/operations/photo-link.test.ts`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add proxy/src/operations/photo-link* app/src/features/photos/PhotoGalleryPage.tsx contracts/backend-manifest.json
git commit -m "feat: add gated encounter linking without re-upload"
```

---

### Task 17: WA-PHO-04 — versioned annotations

**Approved amendment:** Implement against `docs/superpowers/specs/2026-11-06-frappe-private-photo-drafts-design.md` and its gated plan. Prefer native Frappe draft DocType ownership/permissions and separate private annotation files. All clinicians may read saved drafts; only the author may edit. The interfaces below are planned application contracts, not verified installed Frappe endpoints. Draft creation and initial-version behavior must be verified and specified before implementation or activation; never accept client-supplied ownership.

**Files:**
- Create: `proxy/src/operations/photo-annotation.ts`, `photo-annotation.schema.ts`, `photo-annotation.test.ts`
- Modify: `app/src/features/photos/AnnotationEditor.tsx`, `useAnnotationDraft.ts`, `contracts/backend-manifest.json`
- Test: `app/src/features/photos/annotation-save.test.tsx`, `app/e2e/photos.spec.ts`

**Interfaces:**
- Consumes: `AnnotationDocV1` / `AnnotationShapeV1` from `app/src/domain/annotation.ts` (Plan 04 Task 5). This task adds **no new rendering library** and does not extend the JSON with library fields.
- Produces: `saveAnnotationVersion({draftId, baseVersion, doc: AnnotationDocV1}): Promise<{draftId, photoId, version, savedAt, originalSha256}>`; capability `write.photo.annotate`; operation `photo.annotation.save`. Resolve photo and author from the draft record server-side, authorize the current clinician and require author identity for saving. A capability boolean alone cannot authorize a draft mutation.
- Produces: proxy-side Zod validation reusing the same schema; each version stores canonical `AnnotationDocV1` in a separate Frappe private JSON file, referenced by draft/version metadata containing `version`, `savedAt`, `savedBy` and the annotation File reference. Draft identity, original photo/file references and immutable author identity are maintained separately. Konva/react-compare-slider never appear in persisted JSON.
- Produces test helpers used below: `annotationDoc(shapes, overrides?)`, `arrow()`, `addArrow(ui)`, `renderPersistedEditor()`, `originalHash(photoId)` in `app/src/features/photos/test-helpers.ts`.
- Hard rules: original file bytes and `fileId` never change; each save appends an immutable version within one draft, not a photo-wide version chain; `baseVersion` mismatch is `409 CONFLICT_STALE`; another clinician's mutation is denied with 403; the editor clears its dirty state only after a committed response; language switch preserves unsaved shapes; the editor is usable at 390 px with 44 px tools and the DOM shape list remains the accessible alternative.

- [ ] **Step 1: Write failing tests**

First add ownership fixtures: `DRAFT-1` belongs to the acting clinician and references `PH-1` at version 1. Add failing tests proving another clinician can read it but cannot append a version, replace/relink its annotation attachment or delete the annotation File through any API; denial leaves bytes and metadata unchanged. Prove two authors' drafts for the same photo have independent version/conflict chains. Deny anonymous/non-clinician reads, including after clinician-role removal even when the user owns a File. Include these cases in the scoped proxy test command in Step 2 and repeat them against installed-site HTTP routes before activation. These tests must precede implementation.

```ts
it("appends a new version and leaves the original untouched", async () => {
  activate("photo.annotation.save");
  const before = await originalHash("PH-1");
  const res = await post("photo.annotation.save", { draftId: "DRAFT-1", baseVersion: 1, doc: annotationDoc([arrow()]) });
  expect(res.json().version).toBe(2);
  expect(res.json().originalSha256).toBe(before);
  expect(await originalHash("PH-1")).toBe(before);
  expect(res.json().draftId).toBe("DRAFT-1");
});
it("persists only the canonical schema, never library attributes", async () => {
  activate("photo.annotation.save");
  await post("photo.annotation.save", { draftId: "DRAFT-1", baseVersion: 1, doc: annotationDoc([arrow()]) });
  const stored = fake.lastAnnotationDoc;
  expect(AnnotationDocV1Schema.parse(stored).schemaVersion).toBe(1);
  expect(JSON.stringify(stored)).not.toMatch(/konva|attrs|transform/i);
});
it("rejects a document whose original hash does not match the photo", async () => {
  activate("photo.annotation.save");
  const res = await post("photo.annotation.save", { draftId: "DRAFT-1", baseVersion: 1, doc: annotationDoc([arrow()], { originalSha256: "b".repeat(64) }) });
  expect(res.statusCode).toBe(409);
  expect(res.json().code).toBe("CONFLICT_STALE");
});
it("rejects a stale base version instead of overwriting", async () => {
  activate("photo.annotation.save");
  fake.respondWith(409, { code: "CONFLICT_STALE" });
  const res = await post("photo.annotation.save", { draftId: "DRAFT-1", baseVersion: 1, doc: annotationDoc([arrow()]) });
  expect(res.json().code).toBe("CONFLICT_STALE");
});
it("saves one version for a duplicate request key", async () => {
  activate("photo.annotation.save");
  await post("photo.annotation.save", { draftId: "DRAFT-1", baseVersion: 1, doc: annotationDoc([]) }, { key: "a1" });
  await post("photo.annotation.save", { draftId: "DRAFT-1", baseVersion: 1, doc: annotationDoc([]) }, { key: "a1" });
  expect(fake.calls).toHaveLength(1);
});
it("clears the dirty state only after a committed save", async () => {
  activate("photo.annotation.save");
  const ui = renderPersistedEditor();
  addArrow(ui);
  fake.hangBeforeCommit();
  await ui.user.click(ui.screen.getByRole("button", { name: /บันทึกภาพวาด|save annotation/i }));
  expect(ui.screen.getByText(/ยังไม่บันทึก|unsaved/i)).toBeVisible();
  fake.release();
  expect(await ui.screen.findByText(/บันทึกแล้ว|saved/i)).toBeVisible();
});
it("keeps unsaved shapes across a locale switch at 390", async () => {
  setViewport(390);
  const ui = renderPersistedEditor();
  addArrow(ui);
  await ui.user.click(ui.screen.getByRole("button", { name: "EN" }));
  expect(ui.screen.getByTestId("annotation-canvas")).toHaveAttribute("data-shape-count", "1");
  expect(ui.screen.getByText(/unsaved/i)).toBeVisible();
});
```

- [ ] **Step 2: Run to verify failure**

Run: `pnpm --filter @clinic/proxy test src/operations/photo-annotation.test.ts`
Expected: FAIL.

- [ ] **Step 3: Implement annotation versions**

Store `AnnotationDocV1` in separate private JSON files linked to draft-scoped immutable versions and original photo/file references; never flatten onto the original or persist renderer state. Validate with the shared schema on both sides. Switch the editor to `mode="persisted"` only when the capability is enabled; before that it stays preview-only. Keep the DOM shape list enabled in both modes. Manifest `unverified`. Add the 390 px e2e save/conflict case, including a keyboard-only edit through the shape list.

Assert that the verified adapter writes exactly one private annotation version and leaves original bytes unchanged; file upload alone is not a committed draft save. Run the Step 1 ownership and route tests against the installed disposable site before activation.

- [ ] **Step 4: Run to verify pass**

Run: `pnpm --filter @clinic/proxy test src/operations/photo-annotation.test.ts && pnpm --filter @clinic/app test src/features/photos && pnpm --filter @clinic/app e2e e2e/photos.spec.ts`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add proxy/src/operations/photo-annotation* app/src/features/photos app/e2e/photos.spec.ts contracts/backend-manifest.json
git commit -m "feat: add gated versioned photo annotations"
```

---

## A7+ activation backlog (not planned here)

These operations have a design contract but no activation task in this plan. Each needs its own plan/task, its own manifest entry, its own durable keys and restart evidence. They stay `read_only` until then.

| Operation group | Surfaces | Contract gate | Extra condition |
|---|---|---|---|
| Procedure order/complete, stock consumption | DOC-02, DOC-03, PHA-06 | `PROCEDURE-REQUEST/COMPLETE` | Exactly-once stock effect; batch selection |
| Lab order/send/receive/review | DOC-04, DOC-02, NUR-02 | `NATIVE-LAB`, `EXTERNAL-LAB` | Reviewer authorisation; `EXT` provider entity |
| Stock receive/transfer/adjust | PHA-05, PHA-06 | `STOCK-RECEIVE`, `STOCK-TRANSFER/RECONCILE` | Separate reconciliation privilege |
| Purchase order draft/submit | PHA-07 | `PURCHASE-ORDER` | Never auto-submit |
| Invoice submit/void | CAS-02 | `SALES-INVOICE` | Customer link; duplicate-charge check |
| Payment and refund | CAS-03 | `PAYMENT-RECEIVE` | Mode only; refund is a separate lifecycle |
| Admin users/masters/settings | ADM-02…04 | `AUTH/ROLES`, master contracts | No privilege escalation |

## Self-Review

| Spec area | Covered by |
|---|---|
| §3.4 durable log, write-ahead, reconciliation, restart proof | Tasks 1, 3, 4 |
| §6.1 capability states, default deny | Task 2 |
| §6.2 per-write evidence | Tasks 2, 11 |
| §6.5 WA-PAT-01 | Task 5 |
| §6.5 WA-APT-01 | Task 6 |
| §6.5 WA-APT-02, no direct status write | Task 7 |
| §6.5 WA-VST-01a | Task 8 |
| §6.5 WA-MED-01 | Task 9 |
| §6.5 WA-VST-01b + cascade precondition | Task 10 |
| Photo consent capture | Task 14 |
| Private upload preserving originals | Task 15 |
| Encounter linking without re-upload | Task 16 |
| Versioned annotations (immutable original) | Task 17 |
| Canonical schema persisted, renderer-independent | Task 17 |
| §6.5 independence of tasks | Task 12 |
| AT-07/21/25, V3-AT-02 write side | Tasks 4, 8, 10 |
| §9.3 write-activation release gate | Task 11 |
| §9.2 acceptance matrix ownership | Task 13 |

**Spec coverage gap explicitly retained:** procedure completion, lab transitions, photo/consent upload, stock/purchasing, invoicing, payment, refund and admin masters have **no activation task in this plan**. They remain `read_only` and require their own tasks built to the same pattern; enabling them is not implied by this plan.

**Known limitation:** no activation task can pass its own verification until a signed `Backend_Contracts_Installed_<date>.md` entry exists. Every manifest entry ships `missing` or `unverified`; the release gate (Task 11) is expected to print `NO ACTIVE WRITES` until an operator records real evidence.

**Print/export:** `contracts/capabilities.json` defines no print capability, so Plan 02 keeps every print control visible and disabled as unverified. This plan does not add a print capability and must not enable print through a role check; a future plan may add it only with a recorded contract and verified manifest entry.
