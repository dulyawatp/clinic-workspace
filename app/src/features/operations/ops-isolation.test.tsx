import { screen } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import type { ReactElement } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { RoleCode } from "../../domain/capability";
import { AdminClinicalPage } from "../admin/AdminClinicalPage";
import { AdminHomePage } from "../admin/AdminHomePage";
import { AdminSettingsPage } from "../admin/AdminSettingsPage";
import { AdminSystemPage } from "../admin/AdminSystemPage";
import { AdminUsersPage } from "../admin/AdminUsersPage";
import { CashierPage } from "../finance/CashierPage";
import { InvoicesPage } from "../finance/InvoicesPage";
import { PaymentsPage } from "../finance/PaymentsPage";
import { ManagementHomePage } from "../management/ManagementHomePage";
import { ManagementReportsPage } from "../management/ManagementReportsPage";
import { PharmacyBalancesPage } from "../pharmacy/PharmacyBalancesPage";
import { PharmacyBatchesPage } from "../pharmacy/PharmacyBatchesPage";
import { PharmacyItemsPage } from "../pharmacy/PharmacyItemsPage";
import { PharmacyMovementsPage } from "../pharmacy/PharmacyMovementsPage";
import { PharmacyPurchasingPage } from "../pharmacy/PharmacyPurchasingPage";
import { PharmacyReceivePage } from "../pharmacy/PharmacyReceivePage";
import { SystemStatesPage } from "../system/SystemStatesPage";
import { server } from "../../test/msw/server";
import { NATIVE_BOOTSTRAP, nativeSession, postSpy } from "../../test/network-spy";
import { renderWithFixtures } from "../../test/render";
import { V3_GATES, systemChecks } from "./system-gates";

afterEach(() => {
  vi.unstubAllEnvs();
  vi.restoreAllMocks();
});

const surfaces: ReadonlyArray<{
  readonly name: string;
  readonly role: RoleCode;
  readonly testId: string;
  readonly ui: () => ReactElement;
}> = [
  { name: "PHA-02", role: "PHA", testId: "pharmacy-items", ui: () => <PharmacyItemsPage /> },
  { name: "PHA-03", role: "PHA", testId: "pharmacy-batches", ui: () => <PharmacyBatchesPage /> },
  { name: "PHA-04", role: "PHA", testId: "pharmacy-balances", ui: () => <PharmacyBalancesPage /> },
  { name: "PHA-05", role: "PHA", testId: "pharmacy-receive", ui: () => <PharmacyReceivePage /> },
  { name: "PHA-06", role: "PHA", testId: "pharmacy-movements", ui: () => <PharmacyMovementsPage /> },
  { name: "PHA-07", role: "PHA", testId: "pharmacy-purchasing", ui: () => <PharmacyPurchasingPage /> },
  { name: "CAS-01", role: "CAS", testId: "cashier-page", ui: () => <CashierPage /> },
  { name: "CAS-02", role: "CAS", testId: "invoices-page", ui: () => <InvoicesPage /> },
  { name: "CAS-03", role: "CAS", testId: "payments-page", ui: () => <PaymentsPage /> },
  { name: "MGR-01", role: "MGR", testId: "management-page", ui: () => <ManagementHomePage /> },
  { name: "MGR-02", role: "MGR", testId: "management-reports", ui: () => <ManagementReportsPage /> },
  { name: "ADM-01", role: "ADM", testId: "admin-home", ui: () => <AdminHomePage /> },
  { name: "ADM-02", role: "ADM", testId: "admin-users", ui: () => <AdminUsersPage /> },
  { name: "ADM-03", role: "ADM", testId: "admin-clinical", ui: () => <AdminClinicalPage /> },
  { name: "ADM-04", role: "ADM", testId: "admin-settings", ui: () => <AdminSettingsPage /> },
  { name: "ADM-05", role: "ADM", testId: "admin-system", ui: () => <AdminSystemPage /> },
];

describe("design surfaces have no transport", () => {
  it.each(surfaces)(
    "$name renders from fixtures without a read or write request",
    async ({ role, testId, ui }) => {
      vi.stubEnv("VITE_CLINIC_NATIVE", "1");
      server.use(
        http.get(NATIVE_BOOTSTRAP, () => nativeSession(role)),
      );
      const posts = postSpy();
      const fetchSpy = vi.spyOn(globalThis, "fetch");
      try {
        renderWithFixtures(ui(), { role });
        await screen.findByTestId(testId);

        const urls = fetchSpy.mock.calls.map((call) => String(call[0]));
        expect(urls.length).toBeGreaterThan(0);
        expect(urls.every((url) => url === NATIVE_BOOTSTRAP)).toBe(true);
        expect(posts.posts).toEqual([]);
      } finally {
        posts.stop();
      }
    },
  );
});

describe("APP-04 integration-status truth", () => {
  it("renders the sample checks and all BC3 gates without a pass claim", async () => {
    renderWithFixtures(<SystemStatesPage />, { role: "ADM" });

    const checks = await screen.findByRole("table", {
      name: "ตรวจความพร้อม",
    });
    expect(checks.querySelectorAll("tbody tr")).toHaveLength(
      systemChecks().length,
    );
    expect(checks.textContent).toContain("ยังไม่ยืนยัน");
    expect(checks.textContent).toContain("ต้องมีส่วนขยาย");
    expect(checks.textContent).toContain("ต้องตัดสินใจ");

    const gates = screen.getByRole("table", { name: "ประตูตรวจสัญญา" });
    expect(gates.querySelectorAll("tbody tr")).toHaveLength(V3_GATES.length);
    expect(
      gates.querySelectorAll("tbody tr .pc-badge--warning"),
    ).toHaveLength(V3_GATES.length);
    expect(gates.textContent).not.toContain("ผ่านแล้ว");

    // The task-6 sections must not introduce duplicate live-region roles.
    expect(screen.getAllByRole("status")).toHaveLength(1);
    expect(screen.getAllByRole("alert")).toHaveLength(1);
    expect(screen.getAllByRole("textbox")).toHaveLength(1);
    expect(screen.getAllByRole("article")).toHaveLength(1);
  });
});
