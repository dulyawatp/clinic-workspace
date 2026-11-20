import { describe, expect, it } from "vitest";
import { sampleTransfers, reconciliationRequests } from "../../fixtures/data-authored";
import { COMPANY_ID, opsData } from "../../fixtures/data-ops";
import { opsPairKey } from "./ops-copy";
import {
  companyDisplayKey,
  expiryBucketCounts,
  expiryRowsFor,
  invoiceStatusKey,
  itemStockByWarehouse,
  ledgerView,
  paymentModeKey,
  receivablePurchaseOrders,
} from "./ops-model";
import en from "../../i18n/catalog/en.json";
import th from "../../i18n/catalog/th.json";

const opsV3Keys = Object.keys(th).filter((key) => key.startsWith("ops.v3."));

describe("operations copy catalogue", () => {
  it("keeps the staged catalogue complete", () => {
    expect({
      catalogueKeys: Object.keys(th).length,
      opsV3Keys: opsV3Keys.length,
      opsVocabKeys: Object.keys(th).filter((key) =>
        key.startsWith("ops.vocab."),
      ).length,
    }).toMatchInlineSnapshot(`
      {
        "catalogueKeys": 2437,
        "opsV3Keys": 488,
        "opsVocabKeys": 16,
      }
    `);
  });

  it("resolves every ops.v3 key in both languages", () => {
    for (const key of opsV3Keys) {
      expect(th[key as keyof typeof th], key).toEqual(expect.any(String));
      expect(en[key as keyof typeof en], key).toEqual(expect.any(String));
      expect(th[key as keyof typeof th].trim(), key).not.toBe("");
      expect(en[key as keyof typeof en].trim(), key).not.toBe("");
    }
  });

  it("maps the prototype's Thai/English pairs onto staged keys", () => {
    expect(opsPairKey("แพทย์", "Practitioner")).toBe("ops.v3.8bf36a9d4e");
    expect(opsPairKey("โอนย้าย", "Transfer")).toMatch(/^ops\./);
    expect(opsPairKey("รับเข้าคลัง", "Receive stock")).toMatch(/^ops\./);
    expect(opsPairKey("กระทบยอดและตัดจำหน่าย", "Reconciliation and write-off")).toMatch(
      /^ops\./,
    );
  });

  it("fails loudly instead of falling back when a phrase is unstaged", () => {
    expect(() => opsPairKey("ข้อความที่ยังไม่ staged", "Unstaged phrase")).toThrow(
      'Unstaged operations copy: "ข้อความที่ยังไม่ staged" / "Unstaged phrase"',
    );
  });
});

describe("operations read model", () => {
  it("counts expiry lots in exclusive buckets that sum to the row set", () => {
    const rows = expiryRowsFor("all", "all");
    const buckets = expiryBucketCounts(rows);

    expect(Object.values(buckets).reduce((sum, value) => sum + value, 0)).toBe(
      rows.length,
    );
    for (const row of rows) {
      expect(buckets[row.urgency as keyof typeof buckets]).toBeGreaterThan(0);
    }
    expect(buckets.expired).toBe(
      rows.filter((row) => row.daysRemaining !== null && row.daysRemaining < 0)
        .length,
    );
    expect(buckets["7"]).toBe(
      rows.filter(
        (row) =>
          row.daysRemaining !== null &&
          row.daysRemaining >= 0 &&
          row.daysRemaining <= 7,
      ).length,
    );
    expect(buckets["30"]).toBe(
      rows.filter(
        (row) =>
          row.daysRemaining !== null &&
          row.daysRemaining > 7 &&
          row.daysRemaining <= 30,
      ).length,
    );
    expect(buckets["90"]).toBe(
      rows.filter(
        (row) =>
          row.daysRemaining !== null &&
          row.daysRemaining > 30 &&
          row.daysRemaining <= 90,
      ).length,
    );
    expect(buckets.later).toBe(
      rows.filter(
        (row) =>
          row.daysRemaining === null ||
          (row.daysRemaining !== null && row.daysRemaining > 90),
      ).length,
    );
  });

  it("keeps cumulative expiry windows consistent with exclusive buckets", () => {
    const buckets = expiryBucketCounts(expiryRowsFor("all", "all"));
    expect(expiryRowsFor("all", "7").length).toBe(buckets["7"]);
    expect(expiryRowsFor("all", "30").length).toBe(buckets["7"] + buckets["30"]);
    expect(expiryRowsFor("all", "90").length).toBe(
      buckets["7"] + buckets["30"] + buckets["90"],
    );
    expect(expiryRowsFor("all", "expired").length).toBe(buckets.expired);
  });

  it("reads reorder thresholds per item–location, never the item master", () => {
    const rows = itemStockByWarehouse("IT-03");
    const fixtureRows = opsData
      .stockRows()
      .filter((row) => row.itemId === "IT-03")
      .map((row) => row.reorderLevel);

    expect(rows).toHaveLength(2);
    expect(rows.map((row) => row.threshold)).toEqual(fixtureRows);
    expect(new Set(rows.map((row) => row.threshold)).size).toBeGreaterThan(1);
    expect(opsData.itemById("IT-03")?.reorder).not.toEqual(rows[0]?.threshold);
    expect(opsData.itemById("IT-03")?.reorder).not.toEqual(rows[1]?.threshold);
  });

  it("subtracts expired lots from usable stock but not from physical stock", () => {
    const expired = opsData
      .expiryRows()
      .filter((row) => row.daysRemaining !== null && row.daysRemaining < 0);
    expect(expired.length).toBeGreaterThan(0);
    for (const row of expired) expect(row.usableQty).toBe(0);

    for (const row of opsData.stockRows()) {
      const lots = opsData.batches.filter(
        (batch) =>
          batch.itemId === row.itemId && batch.warehouse === row.warehouse,
      );
      const usable = lots.reduce((sum, batch) => {
        const days = opsData.expiryDays(batch.expiry);
        return sum + (days === null || days < 0 ? 0 : batch.qty);
      }, 0);
      expect(row.usable, `${row.itemId}@${row.warehouse}`).toBe(usable);
      expect(row.onHand).toBe(
        lots.reduce((sum, batch) => sum + batch.qty, 0),
      );
    }
  });

  it("merges the core ledger with authored transfers and reconcile requests only in the view", () => {
    const view = ledgerView();
    const coreIds = new Set(opsData.movements.map((row) => row.id));

    expect(view).toHaveLength(
      opsData.movements.length +
        sampleTransfers.length +
        reconciliationRequests.length,
    );
    // Core rows keep their identity and stay chronological.
    expect(opsData.movements.every((row) => coreIds.has(row.id))).toBe(true);
    for (let index = 1; index < view.length; index += 1) {
      expect(view[index - 1]!.at <= view[index]!.at).toBe(true);
    }

    const transfers = view.filter((row) => row.kind === "transfer");
    expect(transfers).toHaveLength(sampleTransfers.length);
    expect(transfers.reduce((sum, row) => sum + (row.qty ?? 0), 0)).toBe(0);
    expect(transfers.every((row) => row.documentRef !== null)).toBe(true);

    const reconciliations = view.filter((row) => row.kind === "reconciliation");
    expect(reconciliations).toHaveLength(reconciliationRequests.length);
    for (const row of reconciliations) {
      expect(row.qty).toBeNull();
      expect(row.requestOnly).toBe(true);
      expect(row.counted).not.toBeNull();
    }

    // The source fixtures themselves were not mutated by building the view.
    expect(
      opsData.movements.some(
        (row) => (row.type as string) === "reconciliation",
      ),
    ).toBe(false);
  });

  it("computes PO progress from submitted receipt sources", () => {
    const rows = receivablePurchaseOrders();
    expect(rows.length).toBeGreaterThan(0);
    for (const row of rows) {
      expect(row.progress).toEqual(opsData.poProgress(row.po.id));
      expect(row.progress.pending).toBeGreaterThan(0);
      expect(["submitted", "partlyReceived"]).toContain(row.po.status);
      expect(row.po.supplierId).not.toBeNull();
    }
    expect(rows.map((row) => row.po.status)).not.toContain("draft");
  });

  it("maps invoice status, payment mode and company without exposing sample codes", () => {
    expect(invoiceStatusKey("partial")).toBe("ops.vocab.partlyPaid");
    expect(paymentModeKey("card")).toBe("ops.vocab.creditCard");
    expect(companyDisplayKey(COMPANY_ID)).toBe("ops.vocab.company");
    expect(companyDisplayKey(null)).toBeNull();
    expect(companyDisplayKey("Other (XY)")).toBeNull();
  });

  it("keeps the outstanding queue equal to the fixture bill count", () => {
    expect(opsData.outstandingRows()).toHaveLength(opsData.counts.bills);
    expect(opsData.outstandingRows().every((row) => row.due > 0)).toBe(true);
  });
});
