# Clinic Platform — 05 Operations and Management Read Delivery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver pharmacy, cashier, management and admin surfaces (PHA-01…07, CAS-01…03, MGR-01/02, ADM-01…05) as read-only screens with expiry urgency, KPI definitions and honest "unavailable" states.

**Architecture:** Three feature groups share the Plan 02 read layer but own their DTOs and queries:
`features/pharmacy`, `features/billing`, `features/management`, `features/admin`. Each defines metric definitions and drilldown links as data, so the UI cannot show a number without a stated source and scope.

**Tech Stack:** React 18, TanStack Query, Zod, MSW, Vitest/RTL, Playwright.

**Spec:** `docs/superpowers/specs/2026-11-05-clinic-application-design.md`

**Parallelism:** Depends on Plans 01–02. Runs in parallel with Plans 03 and 04. Plan 06's stock/billing/admin write tasks (A7+) depend on this plan.

## Global Constraints

**Read contract:** all data comes from Plan 02's `useReadQuery`/`readQuery` allow-list; tests use Plan 02's `renderWithCaps`/`renderAt`/`server`. This plan adds resources to the registry but never adds a fetch wrapper.
**Routing contract:** extend `ROUTES` with `pharmacy*`, `cashier`, `invoices`, `invoice(id)`, `payments`, `payment(id)`, `management*`, `admin*` entries. Product links use those helpers; tests may assert literal URLs. Each task that introduces a route adds its `ROUTES` key **and** its `NAV_MANIFEST` item (`owner: "05"`) in the same task; nav paths resolve through `ROUTES` only and must not reference a route that does not exist yet.
**Fixture contract:** extend `contracts/fixtures/*.json`, `READ_RESOURCES`, `createHandlers()` and `FixtureOverrides` from Plan 02. Do not create a second fixture directory, MSW server, session hook or fetch wrapper. Fixture role filters remain `matrix:"unverified"` and are not authorization.
**Release classification:** this plan is interim read-only. Inventory, billing and admin P0 remain incomplete until their activation tasks exist and are verified; no write is implied here.

- No stock, invoice, payment, PO or admin mutation is enabled.
- Quantity and money values are server-provided; the client never derives a balance, total, tax or allocation.
- Expiry categories are exclusive when summed; overlapping windows are explained in the UI.
- Every KPI declares date range, denominator and source; unavailable data renders `unavailable`, never zero.
- No PAN/CVV field exists anywhere.
- TH/EN complete; 390/834/1440; tables become stacked cards on phone.

## Review Focus

1. **Item with stock at two warehouses, one expired** — expiry count and balance never double-count. Test: `excludes zero-quantity batches from urgency counts` and `shows on-hand and usable as separate columns`.
2. **Purchase order partially received** — progress derives from submitted receipts, and the client does not compute it. Test: `derives PO progress from receipt totals only`.
3. **Invoice fully paid** — no payment action and no outstanding amount shown as positive. Test: `paid invoice shows zero outstanding and no pay action`.
4. **KPI with zero denominator** — shows `—`/unavailable, not `0%`. Test: `renders unavailable for a zero denominator`.
5. **Low stock with no reorder level configured** — shows "not configured", not "0". Test: `shows reorder level as not configured when absent`.

---

### Task 1: Operations domain types and metric definition registry

**Files:**
- Create: `app/src/domain/inventory.ts`, `app/src/domain/billing.ts`, `app/src/domain/metrics.ts`
- Create: `contracts/fixtures/operations.json` (pharmacy items, batches, balances, purchasing, invoices, payments, management metrics, admin users/masters; all `fictional: true`)
- Modify: `app/src/test/msw/handlers.ts` (operations `READ_RESOURCES`, scenario keys `rows`, `reorder`, `valuation`, `ordered`, `received`, `lowStock`, `lines`, `serverGrandTotal`, `state`, `statuses`, `serviceMapping`, `appointments`, `completed`, `daysToExpiry`, `posted`, `readiness`)
- Test: `app/src/domain/operations.test.ts`

**Interfaces:**
- Produces: `StockBalance = {itemId, itemName, warehouseId, batchId?, usableQty, onHandQty, uom}`; `BatchRow = {batchId, itemId, expiry, warehouseId, usableQty, daysToExpiry}`; `MetricDef = {id, labelKey, source, denominator?, range: "day"|"week"|"month", drilldown: string}`; `METRICS: Record<string, MetricDef>`; `formatMetricValue(def, value?: number | null): string`.
- Fixture rows are consumed through Plan 02's `renderWithFixtures`; this plan does not create a second fixture source.

- [ ] **Step 1: Write failing tests**

```ts
it("renders unavailable for a missing metric value", () => {
  expect(formatMetricValue(METRICS.appointmentsBooked, null)).toMatch(/—|ไม่พร้อม|unavailable/);
});
it("renders unavailable for a zero denominator", () => {
  expect(formatMetricValue({ ...METRICS.completionRate, denominator: 0 }, 0)).toMatch(/—|ไม่พร้อม|unavailable/);
});
it("defines a source and drilldown for every metric", () => {
  for (const m of Object.values(METRICS)) { expect(m.source).not.toBe(""); expect(m.drilldown).toMatch(/^\/clinic\//); }
});
it("keeps invoiced revenue and collected payment as separate metrics", () => {
  expect(METRICS.invoicedRevenue.source).not.toBe(METRICS.collectedPayment.source);
});
```

- [ ] **Step 2: Run to verify failure**

Run: `pnpm --filter @clinic/app test src/domain/operations.test.ts`
Expected: FAIL.

- [ ] **Step 3: Implement types and registry**

Metrics include appointments booked, checked-in, completed, completion rate, invoiced revenue, collected payment, outstanding, low stock, expiring batches. `formatMetricValue` returns the unavailable string for `null`, `undefined`, `NaN`, or `denominator === 0`.

- [ ] **Step 4: Run to verify pass**

Run: `pnpm --filter @clinic/app test src/domain/operations.test.ts`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/src/domain
git commit -m "feat: add operations types and metric definition registry"
```

---

### Task 2: Pharmacy home (PHA-01)

**Files:**
- Create: `app/src/features/pharmacy/PharmacyHomePage.tsx`, `StockKpi.tsx`, `usePharmacyKpis.ts`
- Test: `app/src/features/pharmacy/home.test.tsx`

**Interfaces:**
- Produces: `PharmacyHomePage()` at `/clinic/pharmacy`; counts `{stockedSkus, lowStock, outOfStock, expiring30, openPurchaseOrders}`; each KPI links to its filtered list.

- [ ] **Step 1: Write failing tests**

```tsx
it("links each kpi to a filtered destination", async () => {
  renderWithFixtures(<PharmacyHomePage />);
  expect(await screen.findByTestId("kpi-expiring30")).toHaveAttribute("href", /expiry=30/);
});
it("states warehouse scope", async () => {
  renderWithFixtures(<PharmacyHomePage />);
  expect(await screen.findByText(/คลัง|warehouse/i)).toBeVisible();
});
it("shows unavailable when a count source fails", async () => {
  server.use(http.get("/api/read/pharmacy-kpis", () => HttpResponse.error()));
  renderWithFixtures(<PharmacyHomePage />);
  expect(await screen.findByRole("alert")).toBeVisible();
});
```

- [ ] **Step 2: Run to verify failure**

Run: `pnpm --filter @clinic/app test src/features/pharmacy/home.test.tsx`
Expected: FAIL.

- [ ] **Step 3: Implement home**

Five KPI cards + six tiles: Stock Balance, Batch & Expiry, Receive, Transfer, Purchasing, Usage. Only tiles with an existing read surface are links; the rest are disabled with a reason.

- [ ] **Step 4: Run to verify pass**

Run: `pnpm --filter @clinic/app test src/features/pharmacy`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/src/features/pharmacy
git commit -m "feat: add pharmacy home with scoped kpis"
```

---

### Task 3: Items catalogue read (PHA-02)

**Files:**
- Create: `app/src/features/pharmacy/ItemsPage.tsx`, `ItemDetailDrawer.tsx`
- Test: `app/src/features/pharmacy/items.test.tsx`

**Interfaces:**
- Produces: `ItemsPage()` at `/clinic/pharmacy/items`; drawer shows item code, name, group, UOM, `isStockItem`, batch tracking, preferred warehouse.

- [ ] **Step 1: Write failing tests**

```tsx
it("distinguishes stock items from services", async () => {
  renderWithFixtures(<ItemsPage />);
  expect(await screen.findByText(/บริการ|service/i)).toBeVisible();
  expect(screen.getAllByText(/สต็อก|stock/i).length).toBeGreaterThan(0);
});
it("opens a detail drawer without a nested modal", async () => {
  renderWithFixtures(<ItemsPage />);
  await user.click(await screen.findByText("Paracetamol 500mg"));
  expect(screen.getAllByRole("dialog")).toHaveLength(1);
});
it("shows reorder level as not configured when absent", async () => {
  renderWithFixtures(<ItemDetailDrawer />, { reorder: null });
  expect(await screen.findByText(/ยังไม่ตั้งค่า|not configured/i)).toBeVisible();
  expect(screen.queryByText("0")).toBeNull();
});
```

- [ ] **Step 2: Run to verify failure**

Run: `pnpm --filter @clinic/app test src/features/pharmacy/items.test.tsx`
Expected: FAIL.

- [ ] **Step 3: Implement items**

Search by code/name; no edit control (master edit is a write task).

- [ ] **Step 4: Run to verify pass**

Run: `pnpm --filter @clinic/app test src/features/pharmacy`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/src/features/pharmacy
git commit -m "feat: add read-only item catalogue"
```

---

### Task 4: Batches and expiry urgency (PHA-03)

**Files:**
- Create: `app/src/features/pharmacy/BatchesPage.tsx`, `ExpiryFilter.tsx`, `ExpiryCell.tsx`, `ValueAtRiskP1.tsx`
- Test: `app/src/features/pharmacy/batches.test.tsx`

**Interfaces:**
- Produces: `BatchesPage()` at `/clinic/pharmacy/batches`; filter values `expired | 7 | 30 | 90 | all`; `ExpiryCell({daysToExpiry, usableQty})`; `ValueAtRiskP1` not rendered unless `P1_VALUE_AT_RISK` flag and verified valuation are present.

- [ ] **Step 1: Write failing tests**

```tsx
it("filters expired, 7, 30 and 90 windows", async () => {
  renderWithFixtures(<BatchesPage />);
  for (const label of [/หมดอายุ|expired/i, /7 วัน|7 days/i, /30 วัน|30 days/i, /90 วัน|90 days/i])
    expect(await screen.findByRole("button", { name: label })).toBeVisible();
});
it("excludes zero-quantity batches from urgency counts", async () => {
  renderWithFixtures(<BatchesPage />, { rows: [{ usableQty: 0, daysToExpiry: 3 }] });
  expect(await screen.findByText(/ไม่พบ|none/i)).toBeVisible();
});
it("shows days remaining with the window explanation", async () => {
  renderWithFixtures(<BatchesPage />, { rows: [{ usableQty: 5, daysToExpiry: 3 }] });
  expect(await screen.findByText(/อีก 3 วัน|3 days/i)).toBeVisible();
});
it("does not render value-at-risk without verified valuation", async () => {
  renderWithFixtures(<BatchesPage />, { valuation: "unverified" });
  expect(screen.queryByTestId("value-at-risk")).toBeNull();
});
```

- [ ] **Step 2: Run to verify failure**

Run: `pnpm --filter @clinic/app test src/features/pharmacy/batches.test.tsx`
Expected: FAIL.

- [ ] **Step 3: Implement expiry view**

Categories are exclusive; a footnote explains that windows overlap. No write-off or adjust control.

- [ ] **Step 4: Run to verify pass**

Run: `pnpm --filter @clinic/app test src/features/pharmacy`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/src/features/pharmacy
git commit -m "feat: add batch expiry urgency with exclusive windows"
```

---

### Task 5: Balances and ledger read (PHA-04)

**Files:**
- Create: `app/src/features/pharmacy/BalancesPage.tsx`, `LedgerTable.tsx`, `StockFilters.tsx`
- Test: `app/src/features/pharmacy/balances.test.tsx`

**Interfaces:**
- Produces: `BalancesPage()` at `/clinic/pharmacy/balances`; filters warehouse, item group, batch; ledger rows immutable read-only with source document reference.

- [ ] **Step 1: Write failing tests**

```tsx
it("shows on-hand and usable as separate columns", async () => {
  renderWithFixtures(<BalancesPage />);
  expect(await screen.findByRole("columnheader", { name: /on.?hand|คงเหลือ/i })).toBeVisible();
  expect(screen.getByRole("columnheader", { name: /usable|ใช้ได้/i })).toBeVisible();
});
it("links each movement to its source document", async () => {
  renderWithFixtures(<BalancesPage />);
  expect(await screen.findAllByRole("link", { name: /MAT-|STE-/i })).not.toHaveLength(0);
});
it("offers no edit control on a balance", async () => {
  renderWithFixtures(<BalancesPage />);
  expect(screen.queryByRole("button", { name: /แก้ไข|edit/i })).toBeNull();
});
```

- [ ] **Step 2: Run to verify failure**

Run: `pnpm --filter @clinic/app test src/features/pharmacy/balances.test.tsx`
Expected: FAIL.

- [ ] **Step 3: Implement balances**

Aggregates come from the server. Export action is permission-gated and disabled in this plan.

- [ ] **Step 4: Run to verify pass**

Run: `pnpm --filter @clinic/app test src/features/pharmacy`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/src/features/pharmacy
git commit -m "feat: add stock balances and immutable ledger read"
```

---

### Task 6: Receive, transfer and purchase order read forms (PHA-05/06/07)

**Files:**
- Create: `app/src/features/pharmacy/ReceivePage.tsx`, `MovementsPage.tsx`, `PurchasingPage.tsx`, `DocumentPreview.tsx`
- Test: `app/src/features/pharmacy/documents.test.tsx`

**Interfaces:**
- Produces: three routes; forms render server data read-only; `DocumentPreview({kind: "receipt"|"transfer"|"po", lines, totals})`; submit buttons gated by `write.stock.receive`, `write.stock.transfer`, `write.purchase_order.draft`.

- [ ] **Step 1: Write failing tests**

```tsx
it("shows batch and expiry per receive line", async () => {
  renderWithFixtures(<ReceivePage />);
  expect(await screen.findByText(/LOT-2026/)).toBeVisible();
  expect(screen.getByText(/2027-02-28/)).toBeVisible();
});
it("disables receipt submit and explains why", async () => {
  renderWithFixtures(<ReceivePage />);
  expect(await screen.findByRole("button", { name: /บันทึกรับ|submit/i })).toBeDisabled();
});
it("derives PO progress from receipt totals only", async () => {
  renderWithFixtures(<PurchasingPage />, { ordered: 100, received: 40 });
  expect(await screen.findByText(/40\s*\/\s*100/)).toBeVisible();
  expect(screen.queryByText("60%")).toBeNull(); // no local percentage
});
it("never offers a reorder auto-submit", async () => {
  renderWithFixtures(<PurchasingPage />, { lowStock: true });
  expect(screen.queryByRole("button", { name: /ยืนยันคำสั่งซื้อ|confirm purchase order/i })).toBeNull();
});
it("shows transfer source and destination before any submit", async () => {
  renderWithFixtures(<MovementsPage />);
  expect(await screen.findByLabelText(/จาก|from/i)).toBeDisabled();
  expect(screen.getByLabelText(/ไปยัง|to/i)).toBeDisabled();
});
```

- [ ] **Step 2: Run to verify failure**

Run: `pnpm --filter @clinic/app test src/features/pharmacy/documents.test.tsx`
Expected: FAIL.

- [ ] **Step 3: Implement read forms**

Totals are server values displayed with a "preview" label; no computed tax, valuation or allocation. Submit controls disabled with reason.

- [ ] **Step 4: Run to verify pass**

Run: `pnpm --filter @clinic/app test src/features/pharmacy`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/src/features/pharmacy
git commit -m "feat: add read-only stock movement and purchasing documents"
```

---

### Task 7: Cashier home and invoice read (CAS-01/02)

**Files:**
- Create: `app/src/features/billing/CashierHomePage.tsx`, `InvoicesPage.tsx`, `InvoiceDetailPage.tsx`, `InvoiceStatusChip.tsx`
- Test: `app/src/features/billing/invoices.test.tsx`

**Interfaces:**
- Produces: `CashierHomePage()` `/clinic/cashier`; `InvoicesPage()` `/clinic/invoices`; `InvoiceDetailPage()` `/clinic/invoices/:id`; `InvoiceState = "draft"|"submitted"|"paid"|"partial"|"cancelled"|"unknown"`.

- [ ] **Step 1: Write failing tests**

```tsx
it("shows invoiced vs collected separately", async () => {
  renderWithFixtures(<CashierHomePage />);
  expect(await screen.findByTestId("sum-invoiced")).toBeVisible();
  expect(screen.getByTestId("sum-collected")).toBeVisible();
  expect(screen.getByTestId("sum-invoiced")).not.toHaveTextContent(screen.getByTestId("sum-collected").textContent!);
});
it("paid invoice shows zero outstanding and no pay action", async () => {
  renderWithFixtures(<InvoiceDetailPage />, { state: "paid" });
  expect(await screen.findByText(/0\.00|฿0/)).toBeVisible();
  expect(screen.queryByRole("button", { name: /รับชำระ|receive payment/i })).toBeNull();
});
it("renders totals from the server, not recomputed", async () => {
  renderWithFixtures(<InvoiceDetailPage />, { serverGrandTotal: 1070, lines: [1000] });
  expect(await screen.findByTestId("grand-total")).toHaveTextContent("1,070");
});
it("has no card number or cvv field", async () => {
  renderWithFixtures(<InvoiceDetailPage />);
  expect(screen.queryByLabelText(/card number|cvv|pan/i)).toBeNull();
});
```

- [ ] **Step 2: Run to verify failure**

Run: `pnpm --filter @clinic/app test src/features/billing/invoices.test.tsx`
Expected: FAIL.

- [ ] **Step 3: Implement billing read**

Invoice line table shows item, qty, rate, amount as returned. Discount/tax shown only if present. Void/amend absent.

- [ ] **Step 4: Run to verify pass**

Run: `pnpm --filter @clinic/app test src/features/billing`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/src/features/billing
git commit -m "feat: add cashier and invoice read surfaces"
```

---

### Task 8: Payments and receipt read (CAS-03)

**Files:**
- Create: `app/src/features/billing/PaymentsPage.tsx`, `PaymentDetailPage.tsx`, `ReceiptPreview.tsx`, `usePaymentModes.ts`
- Test: `app/src/features/billing/payments.test.tsx`

**Interfaces:**
- Produces: `PaymentsPage()` `/clinic/payments`; `PaymentDetailPage()` `/clinic/payments/:id`; modes are `Cash | Credit Card` as **labels only**; `ReceiptPreview({payment, locale})` print-only.

- [ ] **Step 1: Write failing tests**

```tsx
it("shows cash and card as modes with no gateway fields", async () => {
  renderWithFixtures(<PaymentsPage />);
  expect(await screen.findByText(/เงินสด|cash/i)).toBeVisible();
  expect(screen.getByText(/บัตรเครดิต|credit card/i)).toBeVisible();
  expect(screen.queryByLabelText(/cvv|card number/i)).toBeNull();
});
it("shows allocation references", async () => {
  renderWithFixtures(<PaymentDetailPage />);
  expect(await screen.findByText(/ACC-SINV-/)).toBeVisible();
});
it("does not print a receipt for an unposted payment", async () => {
  renderWithFixtures(<ReceiptPreview />, { posted: false });
  expect(screen.queryByRole("button", { name: /print|พิมพ์/i })).toBeNull();
});
it("offers no refund action", async () => {
  renderWithFixtures(<PaymentDetailPage />);
  expect(screen.queryByRole("button", { name: /คืนเงิน|refund/i })).toBeNull();
});
```

- [ ] **Step 2: Run to verify failure**

Run: `pnpm --filter @clinic/app test src/features/billing/payments.test.tsx`
Expected: FAIL.

- [ ] **Step 3: Implement payment read**

Receipt preview available only when the server reports posted. No PAN/CVV path anywhere in the codebase; add a repo test in Task 10.

- [ ] **Step 4: Run to verify pass**

Run: `pnpm --filter @clinic/app test src/features/billing`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/src/features/billing
git commit -m "feat: add payment and receipt read surfaces"
```

---

### Task 9: Management dashboards and drilldowns (MGR-01/02)

**Files:**
- Create: `app/src/features/management/OverviewPage.tsx`, `ReportsPage.tsx`, `MetricCard.tsx`, `useMetrics.ts`
- Test: `app/src/features/management/management.test.tsx`

**Interfaces:**
- Produces: `OverviewPage()` `/clinic/management`; `ReportsPage()` `/clinic/management/reports`; `MetricCard({def, value})` renders label, value, range, source note, drilldown; tabs: Appointments, Treatments, Finance, Inventory, Purchasing.

- [ ] **Step 1: Write failing tests**

```tsx
it("states range and source for every kpi", async () => {
  renderWithFixtures(<OverviewPage />);
  const card = await screen.findByTestId("metric-invoicedRevenue");
  expect(card).toHaveTextContent(/วันนี้|today/i);
  expect(card).toHaveTextContent(/invoice|ใบแจ้งหนี้/i);
});
it("never shows a percentage from a zero denominator", async () => {
  renderWithFixtures(<OverviewPage />, { appointments: 0, completed: 0 });
  expect(await screen.findByTestId("metric-completionRate")).toHaveTextContent(/—|ไม่พร้อม|unavailable/);
});
it("drilldown opens a filtered list", async () => {
  renderWithFixtures(<OverviewPage />);
  await user.click(await screen.findByRole("link", { name: /ดูรายละเอียด|view details/i }));
  expect(window.location.pathname).toMatch(/^\/clinic\//);
});
it("does not assert treatment revenue without a proven mapping", async () => {
  renderWithFixtures(<ReportsPage />, { serviceMapping: "unverified" });
  expect(screen.queryByTestId("revenue-by-treatment")).toBeNull();
  expect(await screen.findByText(/ไม่พร้อม|unavailable/i)).toBeVisible();
});
```

- [ ] **Step 2: Run to verify failure**

Run: `pnpm --filter @clinic/app test src/features/management`
Expected: FAIL.

- [ ] **Step 3: Implement management read**

Charts render only with a text/table equivalent. Appointment funnel states map explicitly. No P1 revenue split unless mapping status is `verified`.

- [ ] **Step 4: Run to verify pass**

Run: `pnpm --filter @clinic/app test src/features/management`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/src/features/management
git commit -m "feat: add management metrics with stated definitions"
```

---

### Task 10: Admin read surfaces and repo safety tests

**Files:**
- Create: `app/src/features/admin/AdminHomePage.tsx`, `UsersPage.tsx`, `ClinicalMastersPage.tsx`, `SettingsPage.tsx`, `SystemPage.tsx`
- Test: `app/src/features/admin/admin.test.tsx`, `app/src/test/safety.test.ts`

**Interfaces:**
- Produces: five routes matching the manifest; `SystemPage` shows per-contract status from `/api/read/integration-status` and never a blanket "healthy".

- [ ] **Step 1: Write failing tests**

```tsx
it("shows contract status per domain instead of all healthy", async () => {
  renderWithFixtures(<SystemPage />, { statuses: { PATIENT_CREATE: "unverified" } });
  expect(await screen.findByText("PATIENT_CREATE")).toBeVisible();
  expect(screen.getByText(/unverified/i)).toBeVisible();
  expect(screen.queryByText(/all systems healthy|ทุกระบบปกติ/i)).toBeNull();
});
it("never renders a password field", async () => {
  renderWithFixtures(<UsersPage />);
  expect(document.querySelector('input[type="password"]')).toBeNull();
});
it("shows working hours with break intervals read-only", async () => {
  renderWithFixtures(<ClinicalMastersPage />);
  expect(await screen.findByText(/พัก|break/i)).toBeVisible();
});
it("setup home shows readiness warnings from real status, not a blanket ok", async () => {
  renderWithFixtures(<AdminHomePage />, { readiness: [{ item: "cash_account", state: "missing" }] });
  expect(await screen.findByText(/cash_account|บัญชีเงินสด/i)).toBeVisible();
  expect(screen.getByText(/missing|ยังไม่ตั้งค่า/i)).toBeVisible();
});
it("shows role profiles and practitioner links without an edit control", async () => {
  renderWithFixtures(<UsersPage />);
  expect(await screen.findByText(/Role Profile|โปรไฟล์บทบาท/i)).toBeVisible();
  expect(screen.queryByRole("button", { name: /แก้ไข|edit/i })).toBeNull();
});
it("contains no PAN/CVV handling in the app source", () => {
  const modules = import.meta.glob("/src/**/*.{ts,tsx}", { query: "?raw", import: "default", eager: true });
  for (const [file, source] of Object.entries(modules))
    expect(source as string, file).not.toMatch(/cvv|card_number|\bpan\b/i);
});
```

- [ ] **Step 2: Run to verify failure**

Run: `pnpm --filter @clinic/app test src/features/admin src/test/safety.test.ts`
Expected: FAIL.

- [ ] **Step 3: Implement admin read**

Users list shows identifier, display name, enabled, roles, linked practitioner, locale — no password. Settings shows pricing/account mappings read-only. System lists each contract with status and last-checked time, or "not checked".

- [ ] **Step 4: Run to verify pass**

Run: `pnpm --filter @clinic/app test src/features/admin src/test/safety.test.ts`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/src/features/admin app/src/test/safety.test.ts
git commit -m "feat: add admin read surfaces with honest contract status"
```

---

### Task 11: Operations e2e, responsive and locale review

**Files:**
- Create: `app/e2e/pharmacy.spec.ts`, `app/e2e/billing.spec.ts`, `app/e2e/management.spec.ts`, `app/e2e/admin.spec.ts`

**Interfaces:**
- Produces no new product modules; asserts the Plan 05 screens at 390 / 834 / 1440 in both locales through Plan 02's `loginAs` fixture.
- Consumes `ROUTES` (Plan 01) for navigable destinations; literal URLs appear only in test files.

- [ ] **Step 1: Write failing e2e tests**

```ts
for (const width of [390, 834, 1440]) {
  test(`pharmacy expiry usable at ${width}`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    await loginAs(page, "PHA"); await page.goto("/clinic/pharmacy/batches?expiry=30");
    await expect(page.getByRole("button", { name: /30/ })).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  });
}
test("invoice totals are not recomputed after locale switch", async ({ page }) => {
  await loginAs(page, "CAS"); await page.goto("/clinic/invoices/INV-1");
  const before = await page.getByTestId("grand-total").textContent();
  await page.getByRole("button", { name: "EN" }).click();
  expect(await page.getByTestId("grand-total").textContent()).toBe(before);
});
test("admin system page never claims all healthy", async ({ page }) => {
  await loginAs(page, "ADM"); await page.goto("/clinic/admin/system");
  await expect(page.getByText(/all systems healthy/i)).toHaveCount(0);
});
```

- [ ] **Step 2: Run to verify failure**

Run: `pnpm --filter @clinic/app e2e`
Expected: FAIL.

- [ ] **Step 3: Fix layout/selectors only**

- [ ] **Step 4: Run to verify pass**

Run: `pnpm --filter @clinic/app e2e`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/e2e
git commit -m "test: add operations responsive and locale e2e"
```

---

## Self-Review

| Spec area | Covered by |
|---|---|
| PHA-01…07 read, expiry windows, PO progress | Tasks 2–6 |
| CAS-01/02 invoice read, no card data | Task 7 |
| CAS-03 payment/receipt read | Task 8 |
| MGR-01/02 metric definitions, no fabricated KPI | Task 9 |
| ADM-01…05 read, working hours, honest status | Task 10 |
| V3-14/15/17/18/19/21/22/24 display rules | Tasks 2, 4, 6, 7, 9, 10 |
| §5 responsive, TH/EN, no clipped tables | Tasks 2–11 |
| §6.4 prohibited patterns (card data, balance edits) | Tasks 6, 7, 8, 10 |

**Deferred:** all stock/PO/invoice/payment/admin writes (Plan 06 A7+), P1 value-at-risk and revenue split (flag-gated, unavailable).

**Known gap:** valuation, ledger aggregates and service-item revenue mapping are unverified; tasks keep them behind explicit status and unavailable states.
