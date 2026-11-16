import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ROUTES } from "../../app/routes";
import { reportExports } from "../../fixtures/data-authored";
import { coreData } from "../../fixtures/data";
import { opsData, periodKeys } from "../../fixtures/data-ops";
import { renderWithFixtures, setViewport } from "../../test/render";
import { ManagementHomePage } from "./ManagementHomePage";
import { ManagementReportsPage } from "./ManagementReportsPage";

function bodyRows(table: HTMLElement): number {
  if (table.querySelector(".pc-table-empty")) return 0;
  return table.querySelectorAll("tbody tr").length;
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe("MGR-01 clinic overview", () => {
  it("reads the eight KPI tiles from metrics(period)", async () => {
    const user = userEvent.setup();
    renderWithFixtures(<ManagementHomePage />, { role: "MGR" });
    await screen.findByTestId("management-page");

    const week = opsData.metrics("week");
    const appointmentKpi = document.querySelector(
      '[data-manager-kpi="appointments"]',
    )!;
    expect(appointmentKpi.querySelector(".pc-kpi__value")?.textContent).toBe(
      String(week.appointments),
    );
    expect(
      document.querySelector('[data-manager-kpi="outstanding"]')
        ?.textContent,
    ).toContain("ค้างชำระ · ทุกวัน");

    await user.selectOptions(
      screen.getByRole("combobox", { name: "ช่วงเวลา" }),
      "today",
    );
    const today = opsData.metrics("today");
    expect(
      document
        .querySelector('[data-manager-kpi="appointments"]')
        ?.querySelector(".pc-kpi__value")?.textContent,
    ).toBe(String(today.appointments));

    expect(
      screen.getByRole("img", {
        name: "ยอดใบแจ้งหนี้และรับเงินรายวัน",
      }),
    ).toBeVisible();
    const trend = screen.getByRole("table", {
      name: "ยอดใบแจ้งหนี้และรับเงินรายวัน",
    });
    expect(bodyRows(trend)).toBe(periodKeys("today").length);
    expect(
      within(trend).getByText("รายได้ที่ยืนยันแล้ว"),
    ).toBeVisible();
  });

  it("shows the appointment funnel and sample availability states", async () => {
    renderWithFixtures(<ManagementHomePage />, { role: "MGR" });
    await screen.findByTestId("management-page");
    const metrics = opsData.metrics("week");
    const funnel = screen.getByRole("group", {
      name: "เส้นทางนัดหมาย · ข้อมูลต้นทางไม่ใช่คิวโทเคน",
    });
    expect(funnel.querySelectorAll("strong")).toHaveLength(4);
    const total = [...funnel.querySelectorAll("strong")].reduce(
      (sum, node) => sum + Number(node.textContent),
      0,
    );
    const funnelStatuses = metrics.appointmentRows.filter((row) =>
      [
        "scheduled",
        "arrived",
        "checkedIn",
        "waiting",
        "withDoctor",
        "completed",
        "cancelled",
        "noShow",
      ].includes(row.status),
    );
    expect(total).toBe(funnelStatuses.length);
    expect(total).toBeLessThanOrEqual(metrics.appointmentRows.length);

    const availability = screen.getByRole("table", {
      name: "ความพร้อมแพทย์จากตารางตัวอย่าง",
    });
    expect(bodyRows(availability)).toBe(coreData.practitioners.length);
  });
});

describe("MGR-02 management reports", () => {
  it("scopes the appointments tab to the period source rows", async () => {
    renderWithFixtures(<ManagementReportsPage />, {
      role: "MGR",
      path: `${ROUTES.managementReports}?tab=appointments&period=week`,
    });
    await screen.findByTestId("management-reports");
    const metrics = opsData.metrics("week");
    const table = screen.getByRole("table", { name: "นัดหมาย" });
    expect(bodyRows(table)).toBe(metrics.appointmentRows.length);
  });

  it("shows doctor workload from doctorWorkload(days)", async () => {
    renderWithFixtures(<ManagementReportsPage />, {
      role: "MGR",
      path: `${ROUTES.managementReports}?tab=workload&period=week`,
    });
    await screen.findByTestId("management-reports");
    const workload = opsData.doctorWorkload(7);
    const table = screen.getByRole("table", { name: "แพทย์" });
    expect(bodyRows(table)).toBe(workload.length);
    for (const row of workload) {
      expect(
        within(table).getAllByText(String(row.count)).length,
      ).toBeGreaterThan(0);
    }
  });

  it("scopes period invoices and posted payments on the finance tab", async () => {
    renderWithFixtures(<ManagementReportsPage />, {
      role: "MGR",
      path: `${ROUTES.managementReports}?tab=finance&period=week`,
    });
    await screen.findByTestId("management-reports");
    const metrics = opsData.metrics("week");
    expect(
      bodyRows(
        screen.getByRole("table", { name: "ใบแจ้งหนี้ในช่วง" }),
      ),
    ).toBe(metrics.invoiceRows.length);
    expect(
      bodyRows(
        screen.getByRole("table", {
          name: "รับเงินโพสต์และจัดสรรในช่วง",
        }),
      ),
    ).toBe(metrics.paymentRows.length);
  });

  it("branches the completed KPI drill-down to the visit cohort", async () => {
    renderWithFixtures(<ManagementReportsPage />, {
      role: "MGR",
      path: `${ROUTES.managementReports}?tab=appointments&metric=completed&period=week`,
    });
    await screen.findByTestId("management-reports");
    const keys = periodKeys("week");
    const completed = coreData.visits.filter(
      (visit) =>
        visit.state === "completed" &&
        keys.includes(opsData.dates.dayKey(visit.date)),
    );
    expect(completed.length).toBeGreaterThan(0);
    const table = screen.getByRole("table", { name: "การตรวจ" });
    expect(bodyRows(table)).toBe(completed.length);
    expect(within(table).getAllByText(completed[0]!.id).length).toBeGreaterThan(
      0,
    );
  });

  it("branches checked-in and outstanding KPI drill-downs", async () => {
    const checkedIn = opsData.metrics("week").appointmentRows.filter(
      (row) =>
        row.checkedInAt !== null ||
        ["arrived", "checkedIn", "waiting", "withDoctor", "completed"].includes(
          row.status,
        ),
    );
    renderWithFixtures(<ManagementReportsPage />, {
      role: "MGR",
      path: `${ROUTES.managementReports}?tab=appointments&metric=checkedIn&period=week`,
    });
    await screen.findByTestId("management-reports");
    expect(
      bodyRows(screen.getByRole("table", { name: "นัดหมาย" })),
    ).toBe(checkedIn.length);
  });

  it("shows only the outstanding invoice cohort for the outstanding KPI", async () => {
    renderWithFixtures(<ManagementReportsPage />, {
      role: "MGR",
      path: `${ROUTES.managementReports}?tab=finance&metric=outstanding&period=week`,
    });
    await screen.findByTestId("management-reports");
    expect(
      bodyRows(
        screen.getByRole("table", { name: "ค้างชำระ · ทุกวัน" }),
      ),
    ).toBe(opsData.outstandingRows().length);
    expect(
      screen.queryByRole("table", {
        name: "รับเงินโพสต์และจัดสรรในช่วง",
      }),
    ).toBeNull();
  });

  it("shows only posted payments for the collected KPI", async () => {
    renderWithFixtures(<ManagementReportsPage />, {
      role: "MGR",
      path: `${ROUTES.managementReports}?tab=finance&metric=collected&period=week`,
    });
    await screen.findByTestId("management-reports");
    expect(
      bodyRows(
        screen.getByRole("table", {
          name: "รับเงินโพสต์และจัดสรรในช่วง",
        }),
      ),
    ).toBe(opsData.metrics("week").paymentRows.length);
    expect(
      screen.queryByRole("table", { name: "ใบแจ้งหนี้ในช่วง" }),
    ).toBeNull();
  });

  it("shows only expiry rows for the expiring KPI", async () => {
    renderWithFixtures(<ManagementReportsPage />, {
      role: "MGR",
      path: `${ROUTES.managementReports}?tab=inventory&metric=expiring&period=week`,
    });
    await screen.findByTestId("management-reports");
    expect(
      bodyRows(screen.getByRole("table", { name: "ล็อต" })),
    ).toBe(opsData.metrics("week").expiryRows.length);
    expect(
      screen.queryByRole("table", {
        name: "สินค้าและจุดสั่งซื้อตามคลัง",
      }),
    ).toBeNull();
  });

  it("filters the inventory cohort for the low-stock KPI", async () => {
    renderWithFixtures(<ManagementReportsPage />, {
      role: "MGR",
      path: `${ROUTES.managementReports}?tab=inventory&metric=lowStock&period=week`,
    });
    await screen.findByTestId("management-reports");
    expect(
      bodyRows(
        screen.getByRole("table", {
          name: "สินค้าและจุดสั่งซื้อตามคลัง",
        }),
      ),
    ).toBe(
      opsData.metrics("week").stockRows.filter((row) => row.low || row.out)
        .length,
    );
    expect(screen.queryByRole("table", { name: "ล็อต" })).toBeNull();
  });

  it.each([390, 834, 1440] as const)(
    "keeps the inventory report table inside its scroll wrapper at %ipx",
    async (width) => {
      setViewport(width);
      renderWithFixtures(<ManagementReportsPage />, {
        role: "MGR",
        path: `${ROUTES.managementReports}?tab=inventory&period=week`,
      });
      await screen.findByTestId("management-reports");
      expect(window.innerWidth).toBe(width);
      const table = screen.getByRole("table", {
        name: "สินค้าและจุดสั่งซื้อตามคลัง",
      });
      expect(table.closest(".pc-tablewrap")).not.toBeNull();
    },
  );

  it("labels current inventory as a snapshot and exposes the derived timeline", async () => {
    renderWithFixtures(<ManagementReportsPage />, {
      role: "MGR",
      path: `${ROUTES.managementReports}?tab=inventory&period=week`,
    });
    await screen.findByTestId("management-reports");
    const metrics = opsData.metrics("week");
    expect(
      bodyRows(
        screen.getByRole("table", {
          name: "สินค้าและจุดสั่งซื้อตามคลัง",
        }),
      ),
    ).toBe(metrics.stockRows.length);
    expect(
      bodyRows(screen.getByRole("table", { name: "ล็อต" })),
    ).toBe(metrics.expiryRows.length);
    expect(
      screen.getByText(
        "ภาพรวมคลังปัจจุบัน ไม่มีประวัติคลังครบช่วงจึงไม่สร้างแนวโน้มยอดคงเหลือ",
      ),
    ).toBeVisible();
  });

  it("downloads the filtered source rows as a local CSV blob", async () => {
    const user = userEvent.setup();
    const createObjectURL = vi.fn(() => "blob:sample");
    const revokeObjectURL = vi.fn();
    Object.defineProperty(URL, "createObjectURL", {
      configurable: true,
      value: createObjectURL,
    });
    Object.defineProperty(URL, "revokeObjectURL", {
      configurable: true,
      value: revokeObjectURL,
    });
    const click = vi
      .spyOn(HTMLAnchorElement.prototype, "click")
      .mockImplementation(() => {});

    renderWithFixtures(<ManagementReportsPage />, {
      role: "MGR",
      path: `${ROUTES.managementReports}?tab=exports&period=week`,
    });
    await screen.findByTestId("management-reports");

    const card = screen
      .getByRole("heading", { name: "ระเบียนต้นทางตามขอบเขต" })
      .closest(".pc-card")!;
    const first = reportExports[0]!;
    await user.click(
      within(card as HTMLElement).getByRole("button", {
        name: first.label.th,
      }),
    );

    expect(createObjectURL).toHaveBeenCalledTimes(1);
    const calls = createObjectURL.mock.calls as unknown[][];
    const blob = calls[0]![0] as Blob;
    expect(await blob.text()).toContain("SAMPLE");
    expect(click).toHaveBeenCalled();
    expect(revokeObjectURL).toHaveBeenCalledWith("blob:sample");
  });
});
