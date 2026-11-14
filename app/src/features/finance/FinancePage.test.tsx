import { screen } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { expect, it, vi } from "vitest";
import { server } from "../../test/msw/server";
import { renderWithFixtures } from "../../test/render";
import { navFor } from "../shell/nav";
import { FinancePage } from "./FinancePage";

const endpoint = "*/api/live/synthetic-finance";

it.each(["MGR", "CAS"] as const)(
  "shows live synthetic totals and scope without write controls for %s",
  async (role) => {
    renderWithFixtures(<FinancePage />, { role, sessionMode: "live" });
    server.use(
      http.get(endpoint, () =>
        HttpResponse.json({
          currency: "THB",
          invoice_count: 2,
          invoiced: 1250,
          outstanding: 50,
        }),
      ),
    );
    expect(await screen.findByText("1,250")).toBeVisible();
    expect(screen.getByText(/ไม่ใช่รายได้รวมของคลินิก/)).toBeVisible();
    expect(screen.queryByRole("button")).toBeNull();
  },
);

it("shows empty when no invoices exist", async () => {
  renderWithFixtures(<FinancePage />, { role: "MGR", sessionMode: "live" });
  server.use(
    http.get(endpoint, () =>
      HttpResponse.json({
        currency: "THB",
        invoice_count: 0,
        invoiced: 0,
        outstanding: 0,
      }),
    ),
  );
  expect(await screen.findByText(/ไม่พบใบแจ้งหนี้จำลอง/)).toBeVisible();
});

it.each(["MGR", "CAS"] as const)(
  "shows loading then a sanitized error for %s when the live request fails",
  async (role) => {
    let release: () => void = () => {};
    const pending = new Promise<void>((resolve) => {
      release = resolve;
    });
    const read = vi.fn(async () => {
      await pending;
      return HttpResponse.json(
        { message: "private upstream detail" },
        { status: 500 },
      );
    });
    renderWithFixtures(<FinancePage />, { role, sessionMode: "live" });
    server.use(http.get(endpoint, read));
    await vi.waitFor(() => expect(read).toHaveBeenCalled());
    expect(screen.getByRole("status")).toBeVisible();
    release();
    expect(
      await screen.findByText("ไม่สามารถแสดงข้อมูลการเงินจำลองได้"),
    ).toBeVisible();
    expect(screen.queryByText("private upstream detail")).toBeNull();
  },
);

it.each([
  { currency: "THB", invoice_count: -1, invoiced: 1, outstanding: 1 },
  { currency: "bad", invoice_count: 1, invoiced: 1, outstanding: 1 },
  { currency: "THB", invoice_count: 1, invoiced: "1", outstanding: 1 },
])("rejects invalid data", async (body) => {
  renderWithFixtures(<FinancePage />, { role: "MGR", sessionMode: "live" });
  server.use(http.get(endpoint, () => HttpResponse.json(body)));
  expect(
    await screen.findByText("ไม่สามารถแสดงข้อมูลการเงินจำลองได้"),
  ).toBeVisible();
});

it.each([
  { role: "MGR" as const, sessionMode: "fixture" as const },
  { role: "CAS" as const, sessionMode: "fixture" as const },
  { role: "DOC" as const, sessionMode: "live" as const },
])(
  "does not fetch outside live manager or cashier session",
  async (scenario) => {
    const read = vi.fn(() => HttpResponse.json({}));
    renderWithFixtures(<FinancePage />, scenario);
    server.use(http.get(endpoint, read));
    expect(
      await screen.findByText("ไม่สามารถแสดงข้อมูลการเงินจำลองได้"),
    ).toBeVisible();
    expect(read).not.toHaveBeenCalled();
  },
);

it("advertises finance only to live managers and cashiers", () => {
  expect(navFor(["MGR"], {}, "live").map((item) => item.id)).toContain(
    "finance",
  );
  expect(navFor(["MGR"], {}, "fixture").map((item) => item.id)).not.toContain(
    "finance",
  );
  expect(navFor(["CAS"], {}, "live").map((item) => item.id)).toContain(
    "finance",
  );
  expect(navFor(["CAS"], {}, "fixture").map((item) => item.id)).not.toContain(
    "finance",
  );
  expect(navFor(["DOC"], {}, "live").map((item) => item.id)).not.toContain(
    "finance",
  );
});
