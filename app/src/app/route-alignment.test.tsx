import { screen, waitFor } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { roleHomeSurface, SURFACES, surfacePath } from "../design/surfaces";
import type { RoleCode } from "../domain/capability";
import { RoleHomePage } from "../features/home/RoleHomePage";
import { renderAt, renderWithDataRouter } from "../test/render";
import { appRoutes } from "./router";
import { ROUTES } from "./routes";

/** Every designed route address, copied from the design surface manifest. */
const DESIGN_PATHS = [
  "/clinic",
  "/clinic/search",
  "/clinic/system",
  "/clinic/patients",
  "/clinic/patients/new",
  "/clinic/patients/:id",
  "/clinic/patients/:id/photos",
  "/clinic/patients/:id/photos/compare",
  "/clinic/reception",
  "/clinic/reception/queue",
  "/clinic/appointments",
  "/clinic/appointments/:id",
  "/clinic/reception/checkout",
  "/clinic/doctor",
  "/clinic/visits/:id",
  "/clinic/procedures/:id",
  "/clinic/labs/:id",
  "/clinic/nurse",
  "/clinic/nurse/tasks",
  "/clinic/pharmacy",
  "/clinic/pharmacy/items",
  "/clinic/pharmacy/batches",
  "/clinic/pharmacy/balances",
  "/clinic/pharmacy/receive",
  "/clinic/pharmacy/movements",
  "/clinic/pharmacy/purchasing",
  "/clinic/cashier",
  "/clinic/invoices",
  "/clinic/payments",
  "/clinic/management",
  "/clinic/management/reports",
  "/clinic/admin",
  "/clinic/admin/users",
  "/clinic/admin/clinical",
  "/clinic/admin/settings",
  "/clinic/admin/system",
];

const ROLE_HOME_ROUTE: Readonly<Record<RoleCode, string>> = {
  REC: ROUTES.reception,
  DOC: ROUTES.doctor,
  NUR: ROUTES.nurse,
  PHA: ROUTES.pharmacy,
  CAS: ROUTES.cashier,
  MGR: ROUTES.management,
  ADM: ROUTES.admin,
};

const ROLE_HOME_TITLE: Readonly<Record<RoleCode, string>> = {
  REC: "หน้าหลักงานต้อนรับ",
  DOC: "ตารางวันนี้ของแพทย์",
  NUR: "ผู้ป่วยที่ดูแล",
  PHA: "ห้องยาและคลัง",
  CAS: "รายการรอเรียกเก็บ",
  MGR: "ภาพรวมคลินิก",
  ADM: "ตั้งค่าระบบ",
};

describe("design route alignment", () => {
  it("addresses every designed surface at its designed path", () => {
    const paths = SURFACES.filter((surface) => !surface.preAuth).map(
      (surface) => surfacePath(surface.id),
    );
    expect(new Set(paths)).toEqual(new Set(DESIGN_PATHS));
    expect(ROUTES.login).toBe("/clinic/login");
  });

  it("keeps the React-era finance surface out of the designed addresses", () => {
    expect(DESIGN_PATHS).not.toContain(ROUTES.legacyFinance);
    expect(ROUTES.legacyFinance).toBe("/clinic/finance");
  });
});

describe("legacy redirects", () => {
  it.each([
    ["CAS", ROUTES.cashier],
    ["REC", ROUTES.cashier],
    ["MGR", ROUTES.management],
    ["DOC", ROUTES.home],
  ] as const)(
    "sends the old finance link for %s to %s",
    async (role, target) => {
      const { router } = renderWithDataRouter(appRoutes, {
        initialEntries: [ROUTES.legacyFinance],
        role,
      });
      await waitFor(() => expect(router.state.location.pathname).toBe(target));
    },
  );

  it("forwards a legacy lab-photo detail link to the patient photo surface", async () => {
    const { router } = renderWithDataRouter(appRoutes, {
      initialEntries: [ROUTES.livePhoto("workspace-1")],
      role: "DOC",
      sessionMode: "live",
    });
    await waitFor(() =>
      expect(router.state.location.pathname).toBe(
        ROUTES.livePhotosPatient("workspace-1"),
      ),
    );
  });

  it("forwards the legacy lab-photo index to the role home for clinicians", async () => {
    const { router } = renderWithDataRouter(appRoutes, {
      initialEntries: [ROUTES.livePhotos],
      role: "DOC",
      sessionMode: "live",
    });
    await waitFor(() =>
      expect(router.state.location.pathname).toBe(ROUTES.home),
    );
  });

  it.each(["REC", "CAS", "MGR", "ADM", "PHA"] as const)(
    "keeps the legacy lab-photo index closed to %s",
    async (role) => {
      const { router } = renderWithDataRouter(appRoutes, {
        initialEntries: [ROUTES.livePhotos],
        role,
        sessionMode: "live",
      });
      const denied = await screen.findByTestId("role-guard-denied");
      expect(denied).toHaveAttribute("data-surface", "PAT-04");
      expect(router.state.location.pathname).toBe(ROUTES.livePhotos);
      expect(
        screen.getByRole("heading", { name: "ภาพถ่ายก่อนตรวจ" }),
      ).toBeVisible();
    },
  );
});

describe("APP-02 role home dispatch", () => {
  it.each(["REC", "DOC", "NUR", "PHA", "CAS", "MGR", "ADM"] as const)(
    "opens the %s designed home from /clinic",
    async (role) => {
      renderAt(ROUTES.home, <RoleHomePage />, { role });
      expect(await screen.findByTestId("role-home")).toBeVisible();
      expect(
        screen.getByRole("heading", { name: ROLE_HOME_TITLE[role] }),
      ).toBeVisible();
      expect(screen.getByRole("link", { name: "เปิด" })).toHaveAttribute(
        "href",
        ROLE_HOME_ROUTE[role],
      );
    },
  );

  it("dispatches a multi-role session to the first held role's home", () => {
    // The fixture session harness is single-role, so multi-role dispatch is
    // asserted on the pure selector; the rendered cases above stay one role
    // each rather than overclaiming a multi-role session.
    expect(roleHomeSurface(["NUR", "REC"])).toBe("NUR-01");
    expect(roleHomeSurface(["CAS", "ADM"])).toBe("CAS-01");
    expect(roleHomeSurface([])).toBeUndefined();
  });
});

describe("operations, finance, management and admin surfaces replace the shell", () => {
  it.each([
    ["pharmacyItems", "PHA", "pharmacy-items", "PHA-02"],
    ["pharmacyBatches", "PHA", "pharmacy-batches", "PHA-03"],
    ["pharmacyBalances", "PHA", "pharmacy-balances", "PHA-04"],
    ["pharmacyReceive", "PHA", "pharmacy-receive", "PHA-05"],
    ["pharmacyMovements", "PHA", "pharmacy-movements", "PHA-06"],
    ["pharmacyPurchasing", "PHA", "pharmacy-purchasing", "PHA-07"],
    ["cashier", "CAS", "cashier-page", "CAS-01"],
    ["invoices", "CAS", "invoices-page", "CAS-02"],
    ["payments", "CAS", "payments-page", "CAS-03"],
    ["management", "MGR", "management-page", "MGR-01"],
    ["managementReports", "MGR", "management-reports", "MGR-02"],
    ["admin", "ADM", "admin-home", "ADM-01"],
    ["adminUsers", "ADM", "admin-users", "ADM-02"],
    ["adminClinical", "ADM", "admin-clinical", "ADM-03"],
    ["adminSettings", "ADM", "admin-settings", "ADM-04"],
    ["adminSystem", "ADM", "admin-system", "ADM-05"],
  ] as const)(
    "renders %s as its designed surface instead of the unavailable shell",
    async (routeKey, role, testId, surface) => {
      const path = ROUTES[routeKey].replace(":id", "sample-1");
      renderWithDataRouter(appRoutes, { initialEntries: [path], role });
      const page = await screen.findByTestId(testId);
      expect(page).toHaveAttribute("data-surface", surface);
      // Nothing here may fall back to the placeholder's unavailable copy.
      expect(
        screen.queryByRole("heading", { name: "หน้านี้ยังไม่เชื่อมต่อข้อมูล" }),
      ).toBeNull();
    },
  );
});

describe("clinical and nursing surfaces replace the shell", () => {
  it.each([
    ["doctor", "DOC", "doctor-home", "DOC-01"],
    ["nurse", "NUR", "nurse-home", "NUR-01"],
    ["nurseTasks", "NUR", "nurse-tasks", "NUR-02"],
  ] as const)(
    "renders %s as its designed surface instead of the unavailable shell",
    async (routeKey, role, testId, surface) => {
      renderWithDataRouter(appRoutes, {
        initialEntries: [ROUTES[routeKey]],
        role,
      });
      const page = await screen.findByTestId(testId);
      expect(page).toHaveAttribute("data-surface", surface);
    },
  );

  it.each([
    ["procedurePattern", "DOC", "DOC-03", "procedure-detail"],
    ["labPattern", "DOC", "DOC-04", "lab-workspace"],
  ] as const)(
    "renders %s with its designed not-found state for an unknown id",
    async (routeKey, role, surface, testId) => {
      renderWithDataRouter(appRoutes, {
        initialEntries: [ROUTES[routeKey].replace(":id", "unknown-1")],
        role,
      });
      const page = await screen.findByTestId(testId);
      expect(page).toHaveAttribute("data-surface", surface);
    },
  );
});
