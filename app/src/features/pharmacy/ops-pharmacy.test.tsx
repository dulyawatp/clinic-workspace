import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it } from "vitest";
import { ROUTES } from "../../app/routes";
import { opsData } from "../../fixtures/data-ops";
import { useLocale } from "../../i18n/LocaleProvider";
import { installDialogMethods } from "../../test/dialog";
import { renderWithFixtures, setViewport } from "../../test/render";
import { useOpsRoles } from "../operations/ops-role";
import {
  expiryBucketCounts,
  expiryRowsFor,
  itemStockByWarehouse,
  ledgerView,
  purchaseOrdersFor,
  receivablePurchaseOrders,
} from "../operations/ops-model";
import { PharmacyBalancesPage } from "./PharmacyBalancesPage";
import { PharmacyBatchesPage } from "./PharmacyBatchesPage";
import { PharmacyItemsPage } from "./PharmacyItemsPage";
import { PharmacyMovementsPage } from "./PharmacyMovementsPage";
import { PharmacyPurchasingPage } from "./PharmacyPurchasingPage";
import { PharmacyReceivePage } from "./PharmacyReceivePage";

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

function RolesProbe() {
  const roles = useOpsRoles();
  return <span data-testid="roles">{roles.join(",")}</span>;
}

afterEach(() => {
  document.querySelectorAll("dialog[open]").forEach((dialog) => {
    (dialog as HTMLDialogElement).removeAttribute("open");
  });
});

describe("PHA-02 item catalogue", () => {
  it("filters, searches and shows per-warehouse thresholds in the drawer", async () => {
    const user = userEvent.setup();
    const restore = installDialogMethods();
    renderWithFixtures(<PharmacyItemsPage />, { role: "PHA" });
    await screen.findByTestId("pharmacy-items");

    const table = screen.getByRole("table");
    expect(bodyRows(table)).toBe(opsData.items.length);

    await user.selectOptions(
      screen.getByRole("combobox", { name: "กรองรายการ" }),
      "stock",
    );
    expect(bodyRows(table)).toBe(opsData.items.filter((item) => item.isStock).length);

    const item = opsData.itemById("IT-03")!;
    await user.type(screen.getByRole("searchbox", { name: "ค้นหารายการ" }), item.code);
    expect(bodyRows(table)).toBe(1);

    const row = within(table).getByText(item.name.th).closest("tr")!;
    await user.click(within(row).getByRole("button", { name: "รายละเอียด" }));

    const dialog = await screen.findByRole("dialog");
    expect(
      within(dialog).getByRole("heading", {
        name: "สินค้าและจุดสั่งซื้อตามคลัง",
      }),
    ).toBeVisible();
    const stockTable = within(dialog).getByRole("table");
    const stockRows = itemStockByWarehouse("IT-03");
    expect(bodyRows(stockTable)).toBe(stockRows.length);
    for (const stockRow of stockRows) {
      const stockLine = within(stockTable)
        .getByText(`${stockRow.onHand} / ${stockRow.usable}`)
        .closest("tr")!;
      expect(
        within(stockLine).getByText(String(stockRow.threshold)),
      ).toBeVisible();
    }
    restore();
  });

  it("marks an unconfigured location threshold as unavailable", async () => {
    const user = userEvent.setup();
    const restore = installDialogMethods();
    renderWithFixtures(<PharmacyItemsPage />, { role: "PHA" });
    await screen.findByTestId("pharmacy-items");

    const item = opsData.itemById("IT-19")!;
    const table = screen.getByRole("table");
    const row = within(table).getByText(item.name.th).closest("tr")!;
    await user.click(within(row).getByRole("button", { name: "รายละเอียด" }));

    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getAllByText("ไม่พร้อมใช้งาน")).toHaveLength(2);
    restore();
  });

  it("switches the catalogue copy to English", async () => {
    const user = userEvent.setup();
    renderWithFixtures(
      <>
        <Language />
        <PharmacyItemsPage />
      </>,
      { role: "PHA" },
    );
    await screen.findByTestId("pharmacy-items");
    await user.click(screen.getByRole("button", { name: "English" }));
    expect(
      screen.getByRole("heading", { name: "Items catalogue" }),
    ).toBeVisible();
    expect(
      screen.getByRole("columnheader", { name: "Batch tracked" }),
    ).toBeVisible();
  });
});

describe("PHA-03 batches & expiry", () => {
  it("renders exclusive buckets that sum to the rows and filters cumulatively", async () => {
    const user = userEvent.setup();
    renderWithFixtures(<PharmacyBatchesPage />, { role: "PHA" });
    await screen.findByTestId("pharmacy-batches");

    const all = expiryRowsFor("all", "all");
    const buckets = expiryBucketCounts(all);
    const strip = [...document.querySelectorAll(".ops-buckets strong")].map(
      (node) => Number(node.textContent),
    );
    expect(strip).toEqual([
      buckets.expired,
      buckets["7"],
      buckets["30"],
      buckets["90"],
      buckets.later,
    ]);
    expect(strip.reduce((sum, value) => sum + value, 0)).toBe(all.length);

    const table = screen.getByRole("table");
    expect(bodyRows(table)).toBe(all.length);

    await user.selectOptions(
      screen.getByRole("combobox", { name: "ช่วงหมดอายุ" }),
      "30",
    );
    expect(bodyRows(table)).toBe(expiryRowsFor("all", "30").length);
    expect(expiryRowsFor("all", "30").length).toBe(
      buckets["7"] + buckets["30"],
    );
  });
});

describe("PHA-04 balances & ledger", () => {
  it("filters balances and shows only core ledger rows on the ledger tab", async () => {
    const user = userEvent.setup();
    renderWithFixtures(<PharmacyBalancesPage />, { role: "PHA" });
    await screen.findByTestId("pharmacy-balances");

    const table = screen.getByRole("table");
    expect(bodyRows(table)).toBe(opsData.stockRows().length);
    expect(screen.getByRole("button", { name: "ส่งออกข้อมูลที่กรอง" })).toBeVisible();

    await user.selectOptions(
      screen.getByRole("combobox", { name: "สถานะคลัง" }),
      "low",
    );
    expect(bodyRows(table)).toBe(
      opsData.stockRows().filter((row) => row.low).length,
    );

    await user.click(screen.getByRole("button", { name: "บัญชีคลัง" }));
    expect(bodyRows(table)).toBe(opsData.movements.length);
    expect(
      screen.queryByRole("button", { name: "ส่งออกข้อมูลที่กรอง" }),
    ).toBeNull();
    expect(screen.queryByText("SAMPLE-STE-9001")).toBeNull();
  });
});

describe("PHA-05 purchase-order receipt", () => {
  it("shows submitted-receipt progress and a disabled, disclosed review control", async () => {
    renderWithFixtures(<PharmacyReceivePage />, { role: "PHA" });
    await screen.findByTestId("pharmacy-receive");

    const first = receivablePurchaseOrders()[0]!;
    const progress = screen.getByRole("progressbar", { name: "รับแล้ว" });
    expect(progress).toHaveAttribute("value", String(first.progress.percent));
    expect(progress).toHaveAttribute("max", "100");
    expect(screen.getByText(first.progress.receiptIds.join(", "))).toBeVisible();

    const review = screen.getByRole("button", { name: "ตรวจทานและรับสินค้า" });
    expect(review).toBeDisabled();
    expect(review).toHaveAttribute("aria-describedby", "receive-not-connected");

    const lineTable = screen.getByRole("table");
    expect(bodyRows(lineTable)).toBe(first.progress.lines.length);
    for (const line of first.progress.lines) {
      expect(
        within(lineTable).getAllByText(String(line.pending)).length,
      ).toBeGreaterThan(0);
    }
  });

  it("offers no receipt when the requested PO cannot receive", async () => {
    const nonReceivable = opsData.purchaseOrders.find(
      (po) =>
        !receivablePurchaseOrders().some((row) => row.po.id === po.id),
    )!;
    expect(nonReceivable).toBeDefined();
    renderWithFixtures(<PharmacyReceivePage />, {
      role: "PHA",
      path: `${ROUTES.pharmacyReceive}?po=${encodeURIComponent(nonReceivable.id)}`,
    });
    await screen.findByTestId("pharmacy-receive");
    expect(screen.queryByRole("progressbar")).toBeNull();
    expect(screen.getByRole("link", { name: "เปิดจัดซื้อ" })).toBeVisible();
  });
});

describe("PHA-06 stock operations", () => {
  it("records a session-only transfer without editing the fixture ledger", async () => {
    const user = userEvent.setup();
    const before = opsData.movements.length;
    renderWithFixtures(
      <>
        <RolesProbe />
        <PharmacyMovementsPage />
      </>,
      { role: "PHA" },
    );
    await screen.findByText("PHA");

    const batch = opsData
      .expiryRows()
      .find(
        (row) =>
          row.usableQty > 0 &&
          opsData.movements.some(
            (movement) => movement.batchId === row.id && movement.qty > 0,
          ),
      )!;
    const target = opsData.warehouses.find(
      (warehouse) => warehouse.id !== batch.warehouse,
    )!;

    await user.selectOptions(
      screen.getByRole("combobox", { name: "ล็อต / คลังต้นทาง" }),
      batch.id,
    );
    await user.selectOptions(
      screen.getByRole("combobox", { name: "คลังปลายทาง" }),
      target.id,
    );
    await user.type(screen.getByLabelText("จำนวน"), "1");
    await user.selectOptions(
      screen.getByRole("combobox", { name: "เหตุผลที่อนุญาต" }),
      "Replenishment",
    );
    await user.type(
      screen.getByLabelText("เอกสารอ้างอิง / เลขหัตถการ"),
      "SAMPLE-DOC-1",
    );
    await user.click(screen.getByRole("button", { name: "ตรวจสอบและยืนยัน" }));

    const status = await screen.findByRole("status");
    expect(within(status).getByText("บันทึกธุรกรรมตัวอย่างแล้ว")).toBeVisible();
    expect(within(status).getByText(/SAMPLE-DOC-1/)).toBeVisible();
    expect(opsData.movements).toHaveLength(before);

    const ledger = screen.getByRole("table");
    expect(bodyRows(ledger)).toBe(ledgerView().length);
    expect(
      within(ledger).getAllByText("SAMPLE-STE-9001").length,
    ).toBeGreaterThan(0);
  });

  it("keeps reconciliation administrator-only and request-only", async () => {
    const user = userEvent.setup();
    const batch = opsData
      .expiryRows()
      .find((row) =>
        opsData.movements.some(
          (movement) => movement.batchId === row.id && movement.qty > 0,
        ),
      )!;

    renderWithFixtures(
      <>
        <RolesProbe />
        <PharmacyMovementsPage />
      </>,
      { role: "PHA" },
    );
    await screen.findByText("PHA");
    await user.click(
      screen.getByRole("button", {
        name: "คำขอกระทบยอด (จำกัดสิทธิ์)",
      }),
    );
    await user.selectOptions(
      screen.getByRole("combobox", { name: "ล็อต / คลังต้นทาง" }),
      batch.id,
    );
    await user.selectOptions(
      screen.getByRole("combobox", { name: "เหตุผลที่อนุญาต" }),
      "Count discrepancy",
    );
    await user.type(
      screen.getByLabelText("เอกสารอ้างอิง / เลขหัตถการ"),
      "SAMPLE-RECO-1",
    );
    await user.click(
      screen.getByRole("button", {
        name: "เก็บคำขอ ไม่เปลี่ยนยอดคลัง",
      }),
    );

    expect(
      screen.getAllByText(
        "ต้องมีสิทธิ์กระทบยอดแยก ใช้ผู้ดูแลตัวอย่าง",
      ).length,
    ).toBeGreaterThan(0);
    expect(screen.queryByRole("status")).toBeNull();
  });

  it("stores an administrator reconciliation as a request, not a movement", async () => {
    const user = userEvent.setup();
    const before = opsData.movements.length;
    const batch = opsData
      .expiryRows()
      .find((row) =>
        opsData.movements.some(
          (movement) => movement.batchId === row.id && movement.qty > 0,
        ),
      )!;

    renderWithFixtures(
      <>
        <RolesProbe />
        <PharmacyMovementsPage />
      </>,
      { role: "ADM" },
    );
    await screen.findByText("ADM");
    await user.click(
      screen.getByRole("button", {
        name: "คำขอกระทบยอด (จำกัดสิทธิ์)",
      }),
    );
    await user.selectOptions(
      screen.getByRole("combobox", { name: "ล็อต / คลังต้นทาง" }),
      batch.id,
    );
    await user.type(screen.getByLabelText("จำนวน"), "0");
    await user.selectOptions(
      screen.getByRole("combobox", { name: "เหตุผลที่อนุญาต" }),
      "Count discrepancy",
    );
    await user.type(
      screen.getByLabelText("เอกสารอ้างอิง / เลขหัตถการ"),
      "SAMPLE-RECO-2",
    );
    await user.click(
      screen.getByRole("button", {
        name: "เก็บคำขอ ไม่เปลี่ยนยอดคลัง",
      }),
    );

    const status = await screen.findByRole("status");
    expect(status).toHaveTextContent("เก็บคำขอ ไม่เปลี่ยนยอดคลัง");
    expect(opsData.movements).toHaveLength(before);
  });

  it.each([390, 834, 1440] as const)(
    "keeps the dense ledger inside a scroll wrapper at %ipx",
    async (width) => {
      setViewport(width);
      renderWithFixtures(<PharmacyMovementsPage />, { role: "PHA" });
      await screen.findByTestId("pharmacy-movements");
      expect(window.innerWidth).toBe(width);
      const table = screen.getByRole("table");
      const wrapper = table.closest(".pc-tablewrap");
      expect(wrapper).not.toBeNull();
      expect(wrapper).toContainElement(table);
    },
  );
});

describe("PHA-07 purchasing", () => {
  it("filters purchase orders and reports computed progress in the drawer", async () => {
    const user = userEvent.setup();
    const restore = installDialogMethods();
    renderWithFixtures(<PharmacyPurchasingPage />, { role: "PHA" });
    await screen.findByTestId("pharmacy-purchasing");

    const table = screen.getByRole("table");
    expect(bodyRows(table)).toBe(purchaseOrdersFor("all", "all", "").length);
    expect(
      screen.getByRole("button", { name: "สร้างร่างใบสั่งซื้อ" }),
    ).toBeDisabled();

    await user.selectOptions(
      screen.getByRole("combobox", { name: "สถานะ" }),
      "draft",
    );
    const drafts = purchaseOrdersFor("all", "draft", "");
    expect(bodyRows(table)).toBe(drafts.length);
    expect(within(table).getAllByText("ฉบับร่าง")).toHaveLength(drafts.length);

    await user.selectOptions(
      screen.getByRole("combobox", { name: "สถานะ" }),
      "all",
    );
    const first = purchaseOrdersFor("all", "open", "")[0]!;
    const progress = opsData.poProgress(first.id)!;
    const allTable = screen.getByRole("table");
    const row = within(allTable).getByText(first.id).closest("tr")!;
    await user.click(within(row).getByRole("button", { name: "รายละเอียด" }));
    const dialog = await screen.findByRole("dialog");
    const dialogTable = within(dialog).getByRole("table");
    expect(bodyRows(dialogTable)).toBe(progress.lines.length);
    expect(
      within(dialog).getByText(progress.receiptIds.join(", ") || "ไม่มีใบรับที่ยืนยัน"),
    ).toBeVisible();
    restore();
  });

  it("lists suppliers as sample contacts", async () => {
    const user = userEvent.setup();
    renderWithFixtures(<PharmacyPurchasingPage />, { role: "PHA" });
    await screen.findByTestId("pharmacy-purchasing");
    await user.click(screen.getByRole("button", { name: "ผู้จำหน่าย" }));
    const table = screen.getByRole("table");
    expect(bodyRows(table)).toBe(opsData.suppliers.length);
    expect(
      within(table).getByText(opsData.suppliers[0]!.contact),
    ).toBeVisible();
  });
});
