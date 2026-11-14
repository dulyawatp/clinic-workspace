# Clinic Platform — 03 Front Desk and Patient Read Delivery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver the reception and patient surfaces (REC-01…05, PAT-01…03) as read-only screens with every write control disabled and honestly labelled, including the queue status mapping annotation.

**Architecture:** Feature folders own their queries and view models; all data comes from Plan 02's `useReadQuery`. Counts come from server aggregate endpoints, never client-side filters. Appointment and queue actions render through the canonical `WriteGate` from Plan 01 Task 9; this plan never defines its own gate.

**Tech Stack:** React 18, TanStack Query, React Hook Form (read-only form state), Zod, MSW, Vitest/RTL, Playwright.

**Spec:** `docs/superpowers/specs/2026-11-05-clinic-application-design.md`

**Parallelism:** Depends on Plans 01–02. Runs in parallel with Plans 04 and 05 (independent feature folders). Plan 06's `WA-PAT-01`, `WA-APT-01`, `WA-APT-02` depend on this plan.

## Global Constraints

**Routing contract:** extend `ROUTES` with `patients`, `patient(id)`, `reception`, `queue`, `appointments`, `appointment(id)`, `checkout`; product links use those helpers. Tests may assert literal URLs. Each task that introduces a route adds its `ROUTES` key **and** its `NAV_MANIFEST` item (`owner: "03"`) in the same task; nav paths resolve through `ROUTES` only and must not reference a route that does not exist yet.
**Read/fixture contract:** extend Plan 02's `READ_RESOURCES`, `contracts/fixtures/*.json`, `createHandlers()` and `FixtureOverrides`; never add a second fixture directory, fetch wrapper or session hook. Fixture role filters remain labelled `matrix:"unverified"` and are not authorization.
**Release classification:** these surfaces are interim read-only. P0 for REC/PAT is complete only when `WA-PAT-01`, `WA-APT-01` and `WA-APT-02` are enabled and their acceptance rows pass.

- No write action is enabled; every gated control uses the shared `WriteGate` with an explicit reason and stays visible-but-disabled (never hidden).
  Gated navigational tiles/actions use `RoutedControl` imported from `@/ui/WriteGate` directly beneath the gate; ordinary Router `Link` and custom routed wrappers are unsupported gate roots. Buttons use the shared `Button` or native button. Read-only navigation outside a gate remains ordinary Router `Link`.
- Reception must not receive clinical note text; cashier must not receive clinical detail.
- Patient identity numbers are never returned to list DTOs and never appear in URLs.
- Empty means empty: no placeholder counts, no fabricated KPIs, no `0` where the source is missing.
- Queue statuses are display concepts mapped to a documented source; unmapped values render as the raw code with an admin annotation, never guessed.
- Thai and English complete for every string; 390/834/1440 reviewed.

## Review Focus

1. **Two patients with the same name and phone** — duplicate candidates are warnings; opening the wrong record must be impossible from the list. Test: `lists duplicate candidates separately`.
2. **Appointment already checked in** — no second check-in control is offered. Test: `renders no check-in for an already checked-in row`.
3. **Queue status value not in the display map** — renders raw code plus annotation, never silently relabelled. Test: `never maps an unknown status onto waiting`.
4. **Returning patient with no vitals** — shows "no measurements yet", never zeros. Test: `shows no-measurements copy instead of zeros`.
5. **Reception opens checkout for an incomplete visit** — handoff shows blocked reason, no invoice action. Test: `blocks handoff when the visit is incomplete`.

---

### Task 1: Front-desk domain types and display maps

**Files:**
- Create: `app/src/domain/patient.ts`, `app/src/domain/appointment.ts`, `app/src/domain/queue.ts`
- Create: `contracts/fixtures/frontdesk.json` (patients summary rows, appointments, queue rows, reception KPIs, checkout handoffs; all `fictional: true`)
- Modify: `app/src/test/msw/handlers.ts` (front-desk `READ_RESOURCES`, scenario keys `duplicates`, `patient`, `rows`, `status`, `visit`, `withLeave`, `caps`, `role`)
- Test: `app/src/domain/domain.test.ts`

**Interfaces:**
- Produces: `PatientSummary = {id, fullName, mobileMasked, dob?, lastVisitAt?, fictional}`; `AppointmentSummary = {id, patientId, patientName, practitionerId, practitionerName, startsAt, type, status, checkedInAt?}`; `QueueStatus = "scheduled"|"arrived"|"waiting"|"called"|"in_consultation"|"completed"|"no_show"|"cancelled"`; `mapQueueStatus(raw: string): {labelKey: string | null, raw: string}`.
- Fixture rows are consumed by Plan 02's `renderWithFixtures`; this plan does not create an MSW server or a second fixture data set.

- [ ] **Step 1: Write failing tests**

```ts
it("never maps an unknown status onto waiting", () => {
  expect(mapQueueStatus("marley_unknown_state")).toEqual({ labelKey: null, raw: "marley_unknown_state" });
});
it("keeps the raw value alongside a known label", () => {
  expect(mapQueueStatus("arrived").raw).toBe("arrived");
});
it("does not expose a full identity number in a summary", () => {
  const p: PatientSummary = makePatient();
  expect(JSON.stringify(p)).not.toMatch(/\d{13}/);
});
```

- [ ] **Step 2: Run to verify failure**

Run: `pnpm --filter @clinic/app test src/domain/domain.test.ts`
Expected: FAIL — modules missing.

- [ ] **Step 3: Implement types and map**

Map keys are explicit; `null` means "render raw + annotation". No regex guessing, no defaults.

- [ ] **Step 4: Run to verify pass**

Run: `pnpm --filter @clinic/app test src/domain/domain.test.ts`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/src/domain
git commit -m "feat: add front-desk domain types with explicit queue mapping"
```

---

### Task 2: Patient directory (PAT-01)

**Files:**
- Create: `app/src/features/patients/PatientDirectoryPage.tsx`, `usePatients.ts`, `PatientRow.tsx`
- Test: `app/src/features/patients/directory.test.tsx`

**Interfaces:**
- Produces: `PatientDirectoryPage()` at `/clinic/patients`; `usePatients({q, page, pageSize})`; `PatientRow({patient, onOpen})`.

- [ ] **Step 1: Write failing tests**

```tsx
it("lists duplicate candidates separately", async () => {
  renderWithFixtures(<PatientDirectoryPage />, { duplicates: true });
  expect(await screen.findAllByText("Somchai Jaidee")).toHaveLength(2);
  expect(screen.getAllByText(/ผู้ป่วยซ้ำ|possible duplicate/i)).toHaveLength(2);
});
it("filters by Thai mobile digits", async () => {
  renderWithFixtures(<PatientDirectoryPage />);
  await user.type(screen.getByRole("searchbox"), "081");
  expect(await screen.findByText("สมชาย ใจดี")).toBeVisible();
});
it("shows pagination and total", async () => {
  renderWithFixtures(<PatientDirectoryPage />);
  expect(await screen.findByText(/ทั้งหมด|total/i)).toBeVisible();
});
it("offers register only as a disabled gated action", async () => {
  renderWithFixtures(<PatientDirectoryPage />);
  const btn = await screen.findByRole("button", { name: /ลงทะเบียน|register/i });
  expect(btn).toBeDisabled();
});
```

- [ ] **Step 2: Run to verify failure**

Run: `pnpm --filter @clinic/app test src/features/patients/directory.test.tsx`
Expected: FAIL.

- [ ] **Step 3: Implement directory**

Search input debounced; duplicate rows badged; register button inside `WriteGate capability="write.patient.create"`. Table becomes stacked cards at ≤767 px showing name, ID, phone, last visit.

- [ ] **Step 4: Run to verify pass**

Run: `pnpm --filter @clinic/app test src/features/patients`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/src/features/patients
git commit -m "feat: add patient directory with duplicate warnings"
```

---

### Task 3: Patient profile and timeline (PAT-03)

**Files:**
- Create: `app/src/features/patients/PatientProfilePage.tsx`, `PatientHeader.tsx`, `VitalsSummary.tsx`, `Timeline.tsx`
- Test: `app/src/features/patients/profile.test.tsx`

**Interfaces:**
- Produces: `PatientProfilePage()` at `/clinic/patients/:id`; `PatientHeader({patient})` sticky; `VitalsSummary({latest?: LatestVitals})`; `Timeline({items})` with permission-filtered kinds.

- [ ] **Step 1: Write failing tests**

```tsx
it("shows measurement time and units for latest vitals", async () => {
  renderWithFixtures(<PatientProfilePage />, { patient: "withVitals" });
  expect(await screen.findByText(/38\.2\s*°C/)).toBeVisible();
  expect(screen.getByText(/03\/11\/2026|3 พ.ย./)).toBeVisible();
});
it("shows no-measurements copy instead of zeros", async () => {
  renderWithFixtures(<PatientProfilePage />, { patient: "noVitals" });
  expect(await screen.findByText(/ยังไม่มีการวัด|no measurements yet/i)).toBeVisible();
  expect(screen.queryByText("0")).toBeNull();
});
it("hides payment timeline from a nurse", async () => {
  renderWithFixtures(<PatientProfilePage />, { role: "NUR" });
  expect(screen.queryByText(/payment|ชำระเงิน/i)).toBeNull();
});
it("keeps one patient identity in the header after tab change", async () => {
  renderWithFixtures(<PatientProfilePage />);
  const id = await screen.findByTestId("patient-id");
  await user.click(screen.getByRole("tab", { name: /photos|รูป/i }));
  expect(screen.getByTestId("patient-id")).toHaveTextContent(id.textContent!);
});
```

- [ ] **Step 2: Run to verify failure**

Run: `pnpm --filter @clinic/app test src/features/patients/profile.test.tsx`
Expected: FAIL.

- [ ] **Step 3: Implement profile**

Header shows name, ID, age/DOB if permitted, phone, last visit, latest practitioner, plus `Book`/`Capture` gated buttons. Tabs: Appointments, Visits, Labs, Treatments, Photos, Payments. Each tab uses a read resource; permission denial renders in-tab.

- [ ] **Step 4: Run to verify pass**

Run: `pnpm --filter @clinic/app test src/features/patients/profile.test.tsx`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/src/features/patients
git commit -m "feat: add patient profile with vitals and permission-filtered timeline"
```

---

### Task 4: Reception home (REC-01)

**Files:**
- Create: `app/src/features/reception/ReceptionHomePage.tsx`, `KpiCard.tsx`, `TodayAppointments.tsx`, `useReceptionKpis.ts`
- Test: `app/src/features/reception/home.test.tsx`

**Interfaces:**
- Produces: `ReceptionHomePage()` at `/clinic/reception`; `useReceptionKpis(range: "today"|"week"|"month")`; `KpiCard({labelKey, value, scope, href})`.

- [ ] **Step 1: Write failing tests**

```tsx
it("shows four counts, each with a date scope and drilldown", async () => {
  renderWithFixtures(<ReceptionHomePage />);
  for (const key of ["todayAppointments", "checkedIn", "waiting", "upcoming"])
    expect(await screen.findByTestId(`kpi-${key}`)).toHaveTextContent(/\d/);
  expect(screen.getAllByRole("link", { name: /ดู|view/i }).length).toBeGreaterThanOrEqual(4);
});
it("opens checkout tile only when capability allows payment", async () => {
  renderWithFixtures(<ReceptionHomePage />, { caps: {} });
  expect(screen.queryByRole("link", { name: /ชำระเงิน|collect payment/i })).toBeNull();
});
it("shows an error instead of zeros when the aggregate fails", async () => {
  server.use(http.get("/api/read/reception-kpis", () => HttpResponse.error()));
  renderWithFixtures(<ReceptionHomePage />);
  expect(await screen.findByRole("alert")).toBeVisible();
  expect(screen.queryByText("0")).toBeNull();
});
```

- [ ] **Step 2: Run to verify failure**

Run: `pnpm --filter @clinic/app test src/features/reception/home.test.tsx`
Expected: FAIL.

- [ ] **Step 3: Implement home**

Four KPI cards from one server aggregate resource; tiles: Register, New Appointment, Today's Queue, Take Photos, Collect Payment (cap-gated), Find Patient. Appointment preview lists one eligible action per row, all disabled by `WriteGate` in this plan.

- [ ] **Step 4: Run to verify pass**

Run: `pnpm --filter @clinic/app test src/features/reception/home.test.tsx`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/src/features/reception
git commit -m "feat: add reception home with record-derived kpis"
```

---

### Task 5: Appointment calendar read (REC-03)

**Files:**
- Create: `app/src/features/appointments/CalendarPage.tsx`, `CalendarGrid.tsx`, `AvailabilityLegend.tsx`
- Test: `app/src/features/appointments/calendar.test.tsx`

**Interfaces:**
- Produces: `CalendarPage()` at `/clinic/appointments`; `CalendarGrid({view: "day"|"week", practitionerId?, date})`; legend states `available | booked | blocked | leave | break`.

- [ ] **Step 1: Write failing tests**

```tsx
it("renders day and week views", async () => {
  renderWithFixtures(<CalendarPage />);
  expect(await screen.findByRole("grid")).toBeVisible();
  await user.click(screen.getByRole("button", { name: /สัปดาห์|week/i }));
  expect(screen.getByRole("grid")).toHaveAttribute("data-view", "week");
});
it("marks leave and break slots without pretending they are available", async () => {
  renderWithFixtures(<CalendarPage />, { withLeave: true });
  expect(await screen.findByLabelText(/ลาพัก|leave/i)).toHaveAttribute("data-state", "leave");
});
it("does not offer a book action while booking is unverified", async () => {
  renderWithFixtures(<CalendarPage />);
  await user.click(await screen.findByRole("button", { name: /09:30/ }));
  expect(screen.queryByRole("button", { name: /ยืนยัน|confirm booking/i })).toBeNull();
});
```

- [ ] **Step 2: Run to verify failure**

Run: `pnpm --filter @clinic/app test src/features/appointments/calendar.test.tsx`
Expected: FAIL.

- [ ] **Step 3: Implement calendar**

Grid sourced from a read resource that includes availability; conflicts rendered from server data only. Clicking a slot opens the detail panel in read mode. Phone view becomes an agenda list, not a squeezed grid.

- [ ] **Step 4: Run to verify pass**

Run: `pnpm --filter @clinic/app test src/features/appointments`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/src/features/appointments
git commit -m "feat: add read-only appointment calendar with availability legend"
```

---

### Task 6: Appointment detail, reschedule/cancel UI (REC-04, read-only)

**Files:**
- Create: `app/src/features/appointments/AppointmentPage.tsx`, `AppointmentActions.tsx`
- Test: `app/src/features/appointments/detail.test.tsx`

**Interfaces:**
- Produces: `AppointmentPage()` at `/clinic/appointments/:id`; `AppointmentActions({appointment, caps})` rendering Book/Reschedule/Cancel/No-show through `WriteGate`.

- [ ] **Step 1: Write failing tests**

```tsx
it("shows booking fields as read-only with a reason", async () => {
  renderWithFixtures(<AppointmentPage />);
  expect(await screen.findByLabelText(/วันที่|date/i)).toBeDisabled();
  expect(screen.getByText(/รอการตรวจสอบ|awaiting backend verification/i)).toBeVisible();
});
it("does not render a cancel control for a completed appointment", async () => {
  renderWithFixtures(<AppointmentPage />, { status: "completed" });
  expect(screen.queryByRole("button", { name: /ยกเลิก|cancel/i })).toBeNull();
});
it("shows the server status label, not a local status", async () => {
  renderWithFixtures(<AppointmentPage />, { status: "no_show" });
  expect(await screen.findByText(/ไม่มา|no.?show/i)).toBeVisible();
});
```

- [ ] **Step 2: Run to verify failure**

Run: `pnpm --filter @clinic/app test src/features/appointments/detail.test.tsx`
Expected: FAIL.

- [ ] **Step 3: Implement detail**

Read-only fields with disabled styling; action buttons gated by `write.appointment.book`. Reason text is visible in the diagnostics variant and in the product as "not available in this deployment".

- [ ] **Step 4: Run to verify pass**

Run: `pnpm --filter @clinic/app test src/features/appointments`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/src/features/appointments
git commit -m "feat: add read-only appointment detail with gated actions"
```

---

### Task 7: Queue read and check-in gate (REC-02)

**Files:**
- Create: `app/src/features/reception/QueuePage.tsx`, `QueueRow.tsx`, `QueueStatusChip.tsx`, `StatusMappingNote.tsx`
- Test: `app/src/features/reception/queue.test.tsx`

**Interfaces:**
- Produces: `QueuePage()` at `/clinic/reception/queue`; `QueueRow({appointment})`; `QueueStatusChip({raw})`; `StatusMappingNote({raw, source})` shown when mapping is unknown.

- [ ] **Step 1: Write failing tests**

```tsx
it("offers check-in only for an eligible appointment", async () => {
  renderWithFixtures(<QueuePage />);
  expect(await screen.findByRole("button", { name: /รับเข้าตรวจ|check in/i })).toBeDisabled();
  expect(screen.getAllByRole("button", { name: /รับเข้าตรวจ|check in/i })).toHaveLength(1);
});
it("renders no check-in for an already checked-in row", async () => {
  renderWithFixtures(<QueuePage />, { rows: ["checkedIn"] });
  expect(screen.queryByRole("button", { name: /รับเข้าตรวจ|check in/i })).toBeNull();
  expect(await screen.findByText(/รับเข้าตรวจแล้ว|checked in/i)).toBeVisible();
});
it("annotates an unmapped status with its raw value", async () => {
  renderWithFixtures(<QueuePage />, { rows: ["weird"] });
  expect(await screen.findByText("marley_unknown_state")).toBeVisible();
  expect(screen.getByText(/ยังไม่ได้แมป|not mapped/i)).toBeVisible();
});
it("filters today only", async () => {
  renderWithFixtures(<QueuePage />);
  expect(await screen.findByText(/วันนี้|today/i)).toBeVisible();
});
```

- [ ] **Step 2: Run to verify failure**

Run: `pnpm --filter @clinic/app test src/features/reception/queue.test.tsx`
Expected: FAIL.

- [ ] **Step 3: Implement queue**

Columns: time, patient, doctor, reason, arrival, wait (only if arrival timestamp exists), status, action. Walk-in action gated. Check-in button uses `WriteGate capability="write.appointment.checkin"`.

- [ ] **Step 4: Run to verify pass**

Run: `pnpm --filter @clinic/app test src/features/reception`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/src/features/reception
git commit -m "feat: add queue read with explicit status mapping"
```

---

### Task 8: Checkout and follow-up handoff (REC-05)

**Files:**
- Create: `app/src/features/reception/CheckoutPage.tsx`, `BillingHandoff.tsx`
- Test: `app/src/features/reception/checkout.test.tsx`

**Interfaces:**
- Produces: `CheckoutPage()` at `/clinic/reception/checkout`; `BillingHandoff({visit})` with states `completed | billing_needed | invoiced | paid | partial`.

- [ ] **Step 1: Write failing tests**

```tsx
it("shows no doctor note content", async () => {
  renderWithFixtures(<CheckoutPage />, { visit: "withNote" });
  expect(await screen.findByText(/ประเมิน|assessment/i)).toBeVisible(); // status only
  expect(screen.queryByText(/คนไข้มีผื่น|patient has rash/i)).toBeNull();
});
it("blocks handoff when the visit is incomplete", async () => {
  renderWithFixtures(<CheckoutPage />, { visit: "incomplete" });
  expect(await screen.findByText(/ยังตรวจไม่เสร็จ|visit not complete/i)).toBeVisible();
  expect(screen.queryByRole("button", { name: /invoice|ใบแจ้งหนี้/i })).toBeNull();
});
it("links only to an authorised invoice id", async () => {
  renderWithFixtures(<CheckoutPage />, { visit: "invoiced" });
  expect(await screen.findByRole("link", { name: /INV-/i })).toHaveAttribute("href", /^\/clinic\/invoices\//);
});
```

- [ ] **Step 2: Run to verify failure**

Run: `pnpm --filter @clinic/app test src/features/reception/checkout.test.tsx`
Expected: FAIL.

- [ ] **Step 3: Implement checkout**

Indicator only; no clinical text; follow-up booking control gated by `write.appointment.book`.

- [ ] **Step 4: Run to verify pass**

Run: `pnpm --filter @clinic/app test src/features/reception/checkout.test.tsx`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/src/features/reception
git commit -m "feat: add billing handoff without clinical disclosure"
```

---

### Task 9: Front-desk e2e, responsive and locale review

**Files:**
- Create: `app/e2e/reception.spec.ts`, `app/e2e/patient.spec.ts`

**Interfaces:**
- Consumes Plan 02 harness (`loginAs`, fixtures, MSW handlers); produces evidence commands for D1 exit. `loginAs(page, "REC")` performs the real UI sign-in through the Vite proxy with `APP_ORIGIN` matching the Playwright `baseURL`; no cookie injection.

- [ ] **Step 1: Write failing e2e tests**

```ts
for (const width of [390, 834, 1440]) {
  test(`reception home usable at ${width}`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    await loginAs(page, "REC");
    await page.goto("/clinic/reception");
    await expect(page.getByTestId("kpi-todayAppointments")).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  });
}
test("Thai labels are not clipped in the queue at 390", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await loginAs(page, "REC");
  await page.goto("/clinic/reception/queue");
  await expect(page.getByText("รับเข้าตรวจ")).toBeVisible(); // plan uses shorter TH label
});
test("switching to EN keeps the queue filter", async ({ page }) => {
  await loginAs(page, "REC"); await page.goto("/clinic/reception/queue?status=waiting");
  await page.getByRole("button", { name: "EN" }).click();
  await expect(page).toHaveURL(/status=waiting/);
});
```

- [ ] **Step 2: Run to verify failure**

Run: `pnpm --filter @clinic/app e2e e2e/reception.spec.ts`
Expected: FAIL.

- [ ] **Step 3: Fix layout issues found**

Only layout/selector adjustments; no behaviour changes outside this plan's scope.

- [ ] **Step 4: Run to verify pass**

Run: `pnpm --filter @clinic/app e2e`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/e2e
git commit -m "test: add front-desk responsive and locale e2e"
```

---

## Self-Review

| Spec area | Covered by |
|---|---|
| PAT-01/02 read + duplicates, consent separation | Tasks 1–2 |
| PAT-03 profile, vitals, timeline | Task 3 |
| REC-01 counts and tiles | Task 4 |
| REC-03/04 calendar + detail read | Tasks 5–6 |
| REC-02 queue, check-in gate, status mapping | Task 7 |
| REC-05 handoff, no clinical leak | Task 8 |
| V3-01/02/04/07/30 display rules | Tasks 4, 6, 7 |
| AT-01/02/03 display side, AT-04 (no vitals), AT-16 partial | Tasks 2–8 |
| §5 responsive + locale | Task 9 |

**Deferred:** patient create/edit writes (Plan 06 `WA-PAT-01`), booking and check-in writes (`WA-APT-01/02`), photos (Plan 04 read, Plan 06 write), payments (Plan 05 read, Plan 06 write). **P1 optional** (Kanban, request inbox, token board, queue analytics) is not planned; it stays unbuilt until separately approved.

**Known gap:** availability rows and KPI aggregates are fixture-only until their read contracts are recorded; the registry must keep them non-`verified`.
