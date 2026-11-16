import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { ROUTES } from "../../app/routes";
import {
  labProviderMasters,
  permissionMatrix,
  practitionerMasters,
  stockThresholdPolicy,
  stockThresholdSettings,
} from "../../fixtures/data-authored";
import { opsData, staff } from "../../fixtures/data-ops";
import { renderWithFixtures, setViewport } from "../../test/render";
import { warehouseName } from "../operations/ops-model";
import {
  CHECK_STATUS_COPY,
  V3_GATES,
  systemChecks,
} from "../operations/system-gates";
import { AdminClinicalPage } from "./AdminClinicalPage";
import { AdminHomePage } from "./AdminHomePage";
import { AdminSettingsPage } from "./AdminSettingsPage";
import { AdminSystemPage } from "./AdminSystemPage";
import { AdminUsersPage } from "./AdminUsersPage";

function bodyRows(table: HTMLElement): number {
  return table.querySelectorAll("tbody tr").length;
}

function statusCount(status: "unverified" | "extension" | "decision") {
  return systemChecks().filter((check) => check.status === status).length;
}

describe("ADM-01 setup overview", () => {
  it("renders each sample check with its own status", async () => {
    renderWithFixtures(<AdminHomePage />, { role: "ADM" });
    await screen.findByTestId("admin-home");

    const table = screen.getByRole("table", {
      name: "ประตูตรวจสอบก่อนใช้งานจริง",
    });
    expect(bodyRows(table)).toBe(systemChecks().length);
    expect(
      within(table).getAllByText(CHECK_STATUS_COPY.unverified[0]),
    ).toHaveLength(statusCount("unverified"));
    expect(
      within(table).getAllByText(CHECK_STATUS_COPY.extension[0]),
    ).toHaveLength(statusCount("extension"));
    expect(
      within(table).getAllByText(CHECK_STATUS_COPY.decision[0]),
    ).toHaveLength(statusCount("decision"));
    expect(
      screen.getByText(
        "ไม่มีการตรวจสุขภาพเซิร์ฟเวอร์จริงในต้นแบบ จึงไม่แสดงสถานะระบบปกติปลอม",
      ),
    ).toBeVisible();
  });
});

describe("ADM-02 staff & permissions", () => {
  it("shows the sample staff and read-only permission matrix", async () => {
    renderWithFixtures(<AdminUsersPage />, { role: "ADM" });
    await screen.findByTestId("admin-users");

    expect(
      bodyRows(
        screen.getByRole("table", {
          name: "บัญชีตัวอย่างและสิทธิ์งาน",
        }),
      ),
    ).toBe(staff.length);

    const matrix = screen.getByRole("table", {
      name: "ขอบเขตสิทธิ์ตัวอย่าง",
    });
    expect(bodyRows(matrix)).toBe(permissionMatrix.length);
    expect(within(matrix).getByRole("columnheader", { name: "DocType" })).toBeVisible();
    expect(within(matrix).getByRole("columnheader", { name: "Role" })).toBeVisible();
    expect(
      within(matrix).getAllByText(permissionMatrix[0]!.doctype).length,
    ).toBeGreaterThan(0);
    // Presentation-only matrix: no control can pretend to change a right.
    expect(screen.queryAllByRole("button")).toHaveLength(0);
    expect(screen.queryAllByRole("checkbox")).toHaveLength(0);
    expect(
      screen.getByText(
        "ตัวตนตัวอย่างมีเฉพาะบทบาท เซสชันไม่มีข้อมูลเชื่อมแพทย์ จึงยังไม่ยืนยันการเชื่อมตัวตน",
      ),
    ).toBeVisible();
  });
});

describe("ADM-03 clinical masters", () => {
  it("renders practitioner and lab-provider masters with sample-contract labels", async () => {
    const user = userEvent.setup();
    renderWithFixtures(<AdminClinicalPage />, {
      role: "ADM",
      path: `${ROUTES.adminClinical}?tab=practitioners`,
    });
    await screen.findByTestId("admin-clinical");

    const practitioners = screen.getByRole("table", { name: "แพทย์" });
    expect(bodyRows(practitioners)).toBe(practitionerMasters.length);
    expect(
      within(practitioners).getByText(practitionerMasters[0]!.registrationNo),
    ).toBeVisible();

    await user.click(screen.getByRole("button", { name: "ผู้ให้บริการแล็บ" }));
    const providers = screen.getByRole("table", {
      name: "ผู้ให้บริการแล็บ",
    });
    expect(bodyRows(providers)).toBe(labProviderMasters.length);
    expect(
      within(providers).getAllByText("สัญญาตัวอย่างยังไม่ยืนยัน"),
    ).toHaveLength(labProviderMasters.length);
    expect(
      screen.getByRole("button", { name: "ผู้ให้บริการแล็บ" }),
    ).toHaveAttribute("aria-pressed", "true");
  });

  it("shows the same hours source the availability helper validates", async () => {
    renderWithFixtures(<AdminClinicalPage />, {
      role: "ADM",
      path: `${ROUTES.adminClinical}?tab=hours`,
    });
    await screen.findByTestId("admin-clinical");
    expect(screen.getByText("Asia/Bangkok")).toBeVisible();
    const config = opsData.hours();
    const hours = screen.getByRole("table", {
      name: "เวลาทำการ พัก และลา",
    });
    expect(bodyRows(hours)).toBe(
      config.clinic.length + config.breaks.length + config.leave.length,
    );
  });
});

describe("ADM-04 inventory, pricing & settings", () => {
  it("lists per item–location thresholds and discloses unconfigured locations", async () => {
    const user = userEvent.setup();
    renderWithFixtures(<AdminSettingsPage />, { role: "ADM" });
    await screen.findByTestId("admin-settings");

    const thresholds = screen.getByRole("table", {
      name: "จุดสั่งซื้อ",
    });
    expect(bodyRows(thresholds)).toBe(stockThresholdSettings.length);
    const first = stockThresholdSettings[0]!;
    const row = within(thresholds).getByText(first.itemId).closest("tr")!;
    expect(within(row).getByText(String(first.reorderLevel))).toBeVisible();
    expect(
      within(row).getByText(warehouseName(first.warehouse, "th")),
    ).toBeVisible();

    await user.click(screen.getByRole("button", { name: "เงินสด/บัตร" }));
    expect(screen.getByText("DC-CASH-DEMO")).toBeVisible();
    expect(screen.getByText("DC-CARD-DEMO")).toBeVisible();
    expect(
      screen.getByText(
        "รหัสบัญชีตัวอย่างไม่ใช่การยืนยันผังบัญชีจริง ต้องตรวจการตั้งค่าก่อนรับเงินจริง",
      ),
    ).toBeVisible();
    expect(screen.getByText(new RegExp(stockThresholdPolicy.note.th))).toBeVisible();
  });
});

describe("ADM-05 audit & readiness", () => {
  it("keeps statuses truthful, the audit trail id-only and all BC3 gates unverified", async () => {
    const user = userEvent.setup();
    renderWithFixtures(<AdminSystemPage />, { role: "ADM" });
    await screen.findByTestId("admin-system");

    const overview = screen.getByRole("table", {
      name: "ไม่มีการเชื่อมต่อเซิร์ฟเวอร์จริง",
    });
    expect(bodyRows(overview)).toBe(systemChecks().length);
    expect(
      within(overview).getAllByText(CHECK_STATUS_COPY.decision[0]),
    ).toHaveLength(statusCount("decision"));

    await user.click(screen.getByRole("button", { name: "ประวัติรายการ" }));
    const auditTable = screen.getByRole("table", {
      name: "บันทึกตัวอย่าง ปกปิดข้อมูลละเอียดอ่อน",
    });
    expect(bodyRows(auditTable)).toBeGreaterThan(0);
    const actor = auditTable.querySelector("tbody tr td:nth-child(2)")!;
    expect(actor.textContent?.trim()).toMatch(/^[a-z]+$/);
    expect(
      screen.getByText(
        "ผู้ใช้แสดงเป็นรหัสบัญชี ยังไม่ยืนยันชื่อในข้อมูลตัวอย่าง",
      ),
    ).toBeVisible();

    await user.click(screen.getByRole("button", { name: "ประตูตรวจสัญญา" }));
    const gates = screen.getByRole("table", { name: "ประตูตรวจสัญญา" });
    expect(bodyRows(gates)).toBe(V3_GATES.length);
    expect(within(gates).getAllByText("ยังไม่ยืนยัน")).toHaveLength(
      V3_GATES.length,
    );
    expect(within(gates).getByText(/BC3-01/)).toBeVisible();
  });

  it.each([390, 834, 1440] as const)(
    "keeps the audit table inside its scroll wrapper at %ipx",
    async (width) => {
      setViewport(width);
      renderWithFixtures(<AdminSystemPage />, {
        role: "ADM",
        path: `${ROUTES.adminSystem}?tab=audit`,
      });
      await screen.findByTestId("admin-system");
      expect(window.innerWidth).toBe(width);
      const table = screen.getByRole("table", {
        name: "บันทึกตัวอย่าง ปกปิดข้อมูลละเอียดอ่อน",
      });
      expect(table.closest(".pc-tablewrap")).not.toBeNull();
    },
  );
});
