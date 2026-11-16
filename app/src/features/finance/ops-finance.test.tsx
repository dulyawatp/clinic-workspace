import { fireEvent, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { ROUTES } from "../../app/routes";
import { opsData } from "../../fixtures/data-ops";
import { formatMoney } from "../../i18n/format";
import { useLocale } from "../../i18n/LocaleProvider";
import { renderWithFixtures, setViewport } from "../../test/render";
import {
  allocationsFor,
  allInvoices,
  eligibleInvoices,
  invoiceById,
  invoiceOutstanding,
  refundRequestsFor,
  shiftTotals,
} from "../operations/ops-model";
import { CashierPage } from "./CashierPage";
import { InvoicesPage } from "./InvoicesPage";
import { PaymentsPage } from "./PaymentsPage";

function bodyRows(table: HTMLElement): number {
  return table.querySelectorAll("tbody tr").length;
}

function Language() {
  const { setLocale } = useLocale();
  return (
    <button type="button" onClick={() => setLocale("en")}>
      English
    </button>
  );
}

describe("CAS-01 pending bills & shift", () => {
  it("derives shift totals and the all-dates outstanding queue from fixtures", async () => {
    const user = userEvent.setup();
    renderWithFixtures(
      <>
        <Language />
        <CashierPage />
      </>,
      { role: "CAS" },
    );
    await screen.findByTestId("cashier-page");
    await user.click(screen.getByRole("button", { name: "English" }));

    expect(
      screen.getAllByRole("heading", { name: "Pending bills" }).length,
    ).toBeGreaterThan(0);
    const totals = shiftTotals();
    expect(
      screen.getAllByText(formatMoney(totals.total, "en")).length,
    ).toBeGreaterThan(0);
    expect(screen.queryByText("Demo Clinic (DC)")).toBeNull();
    expect(screen.getAllByText("Demo Clinic").length).toBeGreaterThan(0);

    const queue = screen.getByRole("table", { name: "Outstanding queue" });
    expect(bodyRows(queue)).toBe(Math.min(20, opsData.outstandingRows().length));
    expect(opsData.outstandingRows()).toHaveLength(opsData.counts.bills);

    expect(
      screen.getByRole("link", { name: /Unpaid invoices/ }),
    ).toHaveAttribute("href", `${ROUTES.invoices}?filter=outstanding`);
    expect(
      screen.getByRole("link", { name: /Posted allocated cash/ }),
    ).toHaveAttribute("href", `${ROUTES.payments}?mode=cash`);
    expect(
      screen.getByRole("link", { name: /Posted allocated card/ }),
    ).toHaveAttribute("href", `${ROUTES.payments}?mode=card`);
  });

  it("filters the posted collection list by calendar day and mode", async () => {
    const user = userEvent.setup();
    renderWithFixtures(<CashierPage />, { role: "CAS" });
    await screen.findByTestId("cashier-page");

    const payments = opsData.paymentRows({});
    const day = opsData.dates.dayKey(payments[0]!.date);
    const onDay = payments.filter(
      (row) => opsData.dates.dayKey(row.date) === day,
    );

    fireEvent.change(screen.getByLabelText("วันที่ตามปฏิทิน"), {
      target: { value: day },
    });
    const table = screen.getByRole("table", {
      name: "แหล่งรับเงินตามตัวกรอง",
    });
    expect(bodyRows(table)).toBe(onDay.length);

    await user.selectOptions(
      screen.getByRole("combobox", { name: "วิธีชำระ" }),
      "card",
    );
    expect(bodyRows(table)).toBe(
      onDay.filter((row) => row.mode === "card").length,
    );

    const company = screen.getByRole("combobox", { name: "บริษัท" });
    expect(company).toBeDisabled();
    expect(company).toHaveAttribute(
      "aria-describedby",
      "company-source-unavailable",
    );
    expect(
      screen.getAllByText("ไม่มีแหล่งบริษัทในข้อมูลตัวอย่าง").length,
    ).toBeGreaterThan(0);
  });
});

describe("CAS-02 invoices", () => {
  it("shows every sample document and filters partly-paid invoices", async () => {
    const user = userEvent.setup();
    renderWithFixtures(
      <>
        <Language />
        <InvoicesPage />
      </>,
      { role: "CAS", path: `${ROUTES.invoices}?filter=partial` },
    );
    await screen.findByTestId("invoices-page");
    await user.click(screen.getByRole("button", { name: "English" }));

    const table = screen.getByRole("table", { name: "Sample invoices" });
    const partials = allInvoices().filter(
      (invoice) => invoice.status === "partial",
    );
    expect(bodyRows(table)).toBe(partials.length);
    expect(within(table).getAllByText("Partly Paid")).toHaveLength(
      partials.length,
    );

    // Draft and cancelled documents remain visible in the unfiltered list.
    await user.selectOptions(
      screen.getByRole("combobox", { name: "Filter records" }),
      "all",
    );
    const allTable = screen.getByRole("table", { name: "Sample invoices" });
    expect(screen.getByText("SAMPLE-INV-DRAFT-01")).toBeVisible();
    expect(screen.getByText("SAMPLE-INV-CANCELLED-01")).toBeVisible();
    expect(within(allTable).getByText("Draft")).toBeVisible();
    expect(within(allTable).getByText("Cancelled")).toBeVisible();
  });

  it("resolves one invoice with fixture totals and blocked writes", async () => {
    const invoice = invoiceById("DC-INV-00218")!;
    renderWithFixtures(<InvoicesPage />, {
      role: "CAS",
      path: `${ROUTES.invoices}?id=${invoice.id}`,
    });
    await screen.findByTestId("invoices-page");

    const lineTable = screen.getByRole("table", { name: invoice.id });
    expect(bodyRows(lineTable)).toBe(invoice.lines.length);
    for (const line of invoice.lines) {
      expect(
        within(lineTable).getAllByText(formatMoney(line.qty * line.rate, "th"))
          .length,
      ).toBeGreaterThan(0);
    }

    const draft = screen.getByRole("button", {
      name: "สร้างร่างจากรายการที่ยังไม่เรียกเก็บ",
    });
    expect(draft).toBeDisabled();
    expect(draft).toHaveAttribute(
      "aria-describedby",
      "invoice-draft-unavailable",
    );
    const refund = screen.getByRole("button", {
      name: "ขอคืนเงิน / ยกเลิก",
    });
    expect(refund).toBeDisabled();
    expect(refund).toHaveAttribute(
      "aria-describedby",
      "invoice-refund-unavailable",
    );
    expect(refund).toHaveAccessibleDescription(
      "ไม่พร้อมใช้งานในระบบนี้ รอการตรวจสอบระบบหลังบ้าน",
    );

    const refunds = screen.getByRole("table", {
      name: "ขออนุมัติคืนเงินแยก",
    });
    expect(bodyRows(refunds)).toBe(refundRequestsFor().length);
  });
});

describe("CAS-03 payments", () => {
  it("derives allocation arithmetic and labels card mode truthfully", async () => {
    const user = userEvent.setup();
    const invoice = eligibleInvoices().find(
      (row) =>
        row.status === "partial" &&
        allocationsFor(row.id).some((payment) => payment.mode === "card"),
    )!;
    expect(invoice).toBeDefined();
    const allocations = allocationsFor(invoice.id);
    const allocated = allocations.reduce((sum, row) => sum + row.amount, 0);
    const due = invoiceOutstanding(invoice);

    renderWithFixtures(
      <>
        <Language />
        <PaymentsPage />
      </>,
      {
        role: "CAS",
        path: `${ROUTES.payments}?invoice=${invoice.id}&mode=card`,
      },
    );
    await screen.findByTestId("payments-page");
    await user.click(screen.getByRole("button", { name: "English" }));

    expect(screen.getByLabelText("Received amount (THB)")).toHaveValue(due);
    const dl = document.querySelector(".pc-dl") as HTMLElement;
    expect(
      within(dl).getAllByText(formatMoney(allocated, "en")).length,
    ).toBeGreaterThan(0);
    expect(
      within(dl).getAllByText(formatMoney(due, "en")).length,
    ).toBeGreaterThan(0);
    expect(
      within(dl).getByText(formatMoney(0, "en")),
    ).toBeVisible();

    const post = screen.getByRole("button", {
      name: "Review & post sample payment",
    });
    expect(post).toBeDisabled();
    expect(post).toHaveAttribute("aria-describedby", "payment-not-connected");

    const linked = screen.getByRole("table", {
      name: "Linked posted payments & receipts",
    });
    expect(bodyRows(linked)).toBe(allocations.length);
    expect(within(linked).getAllByText("Credit Card").length).toBeGreaterThan(0);

    // The cancelled sample payment is shown as a non-posted document.
    expect(screen.getByText(/SAMPLE-PAY-VOID-01/)).toBeVisible();
    expect(
      screen.getByText("ยกเลิกรับชำระหลังยกเลิกใบแจ้งหนี้"),
    ).toBeVisible();
  });

  it("states that an over-amount cannot be posted instead of clamping it", async () => {
    const user = userEvent.setup();
    const invoice = eligibleInvoices().find(
      (row) => invoiceOutstanding(row) > 0,
    )!;
    const due = invoiceOutstanding(invoice);
    renderWithFixtures(<PaymentsPage />, {
      role: "CAS",
      path: `${ROUTES.payments}?invoice=${invoice.id}`,
    });
    await screen.findByTestId("payments-page");

    const amount = screen.getByLabelText("จำนวนรับชำระ (THB)");
    await user.clear(amount);
    await user.type(amount, String(due + 100));

    expect(amount).toHaveAttribute(
      "aria-describedby",
      "payment-amount-limit",
    );
    expect(
      screen.getByText(
        "จำนวนต้องมากกว่า 0 ไม่เกินยอดค้าง และบัตรต้องมีเลขอ้างอิงสลิป",
      ),
    ).toBeVisible();
    expect(
      screen.getAllByText("ไม่พร้อมใช้งาน").length,
    ).toBeGreaterThan(0);
    expect(
      screen.getByRole("button", {
        name: "ตรวจทานและรับเงินตัวอย่าง",
      }),
    ).toBeDisabled();
  });

  it.each([390, 834, 1440] as const)(
    "keeps the invoice table inside its scroll wrapper at %ipx",
    async (width) => {
      setViewport(width);
      renderWithFixtures(<InvoicesPage />, { role: "CAS" });
      await screen.findByTestId("invoices-page");
      expect(window.innerWidth).toBe(width);
      const table = screen.getByRole("table", { name: "รายการใบแจ้งหนี้ตัวอย่าง" });
      expect(table.closest(".pc-tablewrap")).not.toBeNull();
    },
  );

  it("does not preselect a draft invoice for collection", async () => {
    renderWithFixtures(<PaymentsPage />, {
      role: "CAS",
      path: `${ROUTES.payments}?invoice=SAMPLE-INV-DRAFT-01`,
    });
    await screen.findByTestId("payments-page");
    const select = screen.getByRole("combobox", { name: "ใบแจ้งหนี้" });
    expect(select).not.toHaveValue("SAMPLE-INV-DRAFT-01");
    expect(
      eligibleInvoices().some((invoice) => invoice.id === "SAMPLE-INV-DRAFT-01"),
    ).toBe(false);
  });
});
