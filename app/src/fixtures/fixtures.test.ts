import { afterEach, describe, expect, it, vi } from "vitest";
import { isRoleCode } from "../domain/capability";

/**
 * Fixture-layer contract tests (task 2 of the UI parity plan).
 *
 * The demo/sample fixtures are deterministic given a frozen clock: every
 * relative date is anchored to one captured `now`, and every seeded generator
 * (20261103 core, 778899 ops) reproduces the prototype's record volumes.
 * Derived numbers (VAT/discount/paid, thresholds, PO progress, due balances,
 * KPI series) are recomputed from source rows here as well, so a stored
 * shortcut can never disagree with the rows it displays.
 */

// Sunday 15 Nov 2026, 10:00 Bangkok — matches the frozen demo day.
const FROZEN = new Date("2026-11-15T10:00:00+07:00");
const DAY_MS = 86_400_000;

const DAY_CODES = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"] as const;

async function loadFixtures(at: Date = FROZEN) {
  vi.useFakeTimers();
  vi.setSystemTime(at);
  vi.resetModules();
  const [core, ops, clinical, authored] = await Promise.all([
    import("./data"),
    import("./data-ops"),
    import("./data-clinical"),
    import("./data-authored"),
  ]);
  return { core, ops, clinical, authored };
}

afterEach(() => {
  vi.useRealTimers();
  vi.resetModules();
});

describe("demo/sample labelling", () => {
  it("labels every fixture module and row as demo/sample data", async () => {
    const { core, ops, clinical, authored } = await loadFixtures();
    for (const module of [core, ops, clinical, authored]) {
      expect(module.SAMPLE_LABEL).toMatch(/demo|sample/i);
      expect(module.SAMPLE_LABEL).toMatch(/not|no|never|unverified|fictional/i);
    }

    const rows = [
      ...core.patients,
      ...core.appointments,
      ...core.visits,
      ...ops.items,
      ...ops.batches,
      ...ops.movements,
      ...ops.invoices,
      ...ops.payments,
      ...ops.staff,
      ...clinical.photoFrames,
      ...clinical.annotationVersions,
      ...clinical.consentRecords,
      ...clinical.labSamples,
      ...clinical.nurseAssignments,
      ...authored.permissionMatrix,
    ];
    expect(rows.length).toBeGreaterThan(0);
    for (const row of rows) expect(row.sample).toBe(true);
  });

  it("keeps authored clinical samples free of dialable patient identifiers", async () => {
    const { clinical } = await loadFixtures();
    expect(clinical.consentForm.languages).toEqual(["th", "en"]);
    for (const profile of clinical.patientProfiles) {
      expect(profile.nationalId).toMatch(/^0-0000-00000-00-0$/);
      for (const contact of profile.emergencyContacts) {
        expect(contact.phone).toMatch(/^08-FIXTURE-\d{4}$/);
      }
    }
  });
});

describe("frozen-clock determinism", () => {
  it("rebuilds byte-identical fixtures for the same frozen clock", async () => {
    const first = await loadFixtures();
    const second = await loadFixtures();

    for (const key of ["patients", "appointments", "visits"] as const) {
      expect(JSON.stringify(second.core[key])).toBe(
        JSON.stringify(first.core[key]),
      );
    }
    for (const key of ["batches", "movements", "invoices", "payments"] as const) {
      expect(JSON.stringify(second.ops[key])).toBe(
        JSON.stringify(first.ops[key]),
      );
    }
  });

  it("anchors every relative date to the captured clock, never a literal", async () => {
    const day1 = await loadFixtures(FROZEN);
    const day2 = await loadFixtures(new Date(FROZEN.getTime() + DAY_MS));

    const shift = (a: string | null, b: string | null) => {
      expect(a).not.toBeNull();
      expect(b).not.toBeNull();
      return new Date(b as string).getTime() - new Date(a as string).getTime();
    };

    const patient1 = day1.core.patientById("P-10241");
    const patient2 = day2.core.patientById("P-10241");
    expect(patient1?.consent.at).toBeTruthy();
    expect(shift(patient1?.consent.at ?? null, patient2?.consent.at ?? null)).toBe(
      DAY_MS,
    );

    const visit1 = day1.core.visitById("DC-VIS-2041");
    const visit2 = day2.core.visitById("DC-VIS-2041");
    expect(shift(visit1?.date ?? null, visit2?.date ?? null)).toBe(DAY_MS);

    const batch1 = day1.ops.batches[0];
    const batch2 = day2.ops.batches[0];
    expect(shift(batch1?.expiry ?? null, batch2?.expiry ?? null)).toBe(DAY_MS);

    // Seeded identifiers and volumes are clock-independent.
    expect(day2.core.patients.map((p) => p.id)).toEqual(
      day1.core.patients.map((p) => p.id),
    );
    expect(day2.ops.batches.map((b) => b.id)).toEqual(
      day1.ops.batches.map((b) => b.id),
    );
  });
});

describe("prototype-derived volumes", () => {
  it("reproduces the prototype masters and generated record counts", async () => {
    const { core, ops } = await loadFixtures();

    // Golden seeded volumes (frozen clock 2026-11-15). These lock the ported
    // generator so a refactor cannot silently change record shapes.
    expect(core.patients).toHaveLength(180);
    expect(core.practitioners).toHaveLength(3);
    expect(core.apptTypes).toHaveLength(6);
    expect(core.rooms).toHaveLength(4);
    expect(ops.items).toHaveLength(24);
    expect(ops.warehouses).toHaveLength(3);
    expect(ops.suppliers).toHaveLength(4);
    expect(core.appointments).toHaveLength(186);
    expect(core.visits).toHaveLength(114);
    expect(ops.invoices).toHaveLength(113);
    expect(ops.batches).toHaveLength(33);
    expect(ops.payments).toHaveLength(100);
    expect(ops.counts.bills).toBe(26);
    expect(
      ops.invoices.filter((invoice) => invoice.status !== "paid"),
    ).toHaveLength(26);
    expect(core.bills).toBe(core.counts.bills); // flat re-export matches
    expect(core.bills).toBe(0); // core placeholder until invoices exist

    // 16 authored desk appointments + 20 generated days (d -13..6, no 0).
    const desk = core.appointments.filter((a) => a.id.startsWith("DC-APT-33"));
    expect(desk).toHaveLength(16);
    expect(core.appointments.length).toBeGreaterThan(desk.length);

    const completedVisits = core.visits.filter((v) => v.state === "completed");
    expect(ops.invoices).toHaveLength(completedVisits.length);
    // Reconciled ledger: one opening receipt plus at most one usage per lot.
    expect(ops.movements).toHaveLength(ops.batches.length * 2);
  });

  it("keeps seeded generation tied to the documented seeds", async () => {
    const { core, ops } = await loadFixtures();
    expect(core.SEED_CORE).toBe(20261103);
    expect(ops.SEED_OPS).toBe(778899);

    // Golden first draws from the ported LCG (same formula as the prototype),
    // so a refactor cannot silently change the generated record shapes.
    const coreRng = core.seededRng(core.SEED_CORE);
    expect(coreRng()).toBeCloseTo(0.4757119123823941, 12);
    expect(coreRng()).toBeCloseTo(0.6070262773428112, 12);

    const opsRng = ops.seededRng(ops.SEED_OPS);
    expect(opsRng()).toBeCloseTo(0.1002659103833139, 12);
    expect(opsRng()).toBeCloseTo(0.3505487584043294, 12);
  });
});

describe("recomputed invoice money", () => {
  it("derives subtotal, VAT, total, paid and status from lines and payments", async () => {
    const { ops } = await loadFixtures();

    for (const invoice of ops.invoices) {
      const subtotal = invoice.lines.reduce(
        (sum, line) => sum + line.qty * line.rate,
        0,
      );
      expect(invoice.subtotal).toBe(subtotal);
      expect(invoice.vat).toBe(
        Math.round((invoice.subtotal - invoice.discount) * 0.07),
      );
      expect(invoice.total).toBe(
        invoice.subtotal - invoice.discount + invoice.vat,
      );

      const allocated = ops
        .paymentsForInvoice(invoice.id)
        .filter((p) => p.posted)
        .reduce((sum, p) => sum + p.amount, 0);
      expect(allocated).toBe(invoice.paid);
      expect(ops.dueOf(invoice)).toBe(Math.max(0, invoice.total - allocated));
      expect(invoice.status).toBe(
        invoice.paid >= invoice.total
          ? "paid"
          : invoice.paid > 0
            ? "partial"
            : "unpaid",
      );

      // Company IDs are required on every financial document.
      expect(invoice.companyId).toBe(ops.company.id);
      for (const payment of ops.paymentsForInvoice(invoice.id)) {
        expect(payment.companyId).toBe(ops.company.id);
        expect(payment.posted).toBe(true);
      }
    }

    for (const row of ops.outstandingRows()) expect(row.due).toBeGreaterThan(0);
  });
});

describe("stock ledger and thresholds", () => {
  it("reconstructs every batch balance from its reconciled movements", async () => {
    const { ops } = await loadFixtures();

    expect(ops.movements.map((m) => m.at)).toEqual(
      [...ops.movements.map((m) => m.at)].sort(),
    );
    expect(ops.LEDGER_ORDER).toBe("oldest-first");

    for (const batch of ops.batches) {
      const delta = ops.movements
        .filter((m) => m.batchId === batch.id && m.warehouse === batch.warehouse)
        .reduce((sum, m) => sum + m.qty, 0);
      expect(delta).toBe(batch.qty);
      expect(batch.qty).toBeGreaterThanOrEqual(0);
    }

    for (const row of ops.stockRows()) {
      expect(row.onHand).toBe(ops.stockOnHand(row.itemId, row.warehouse));
    }
  });

  it("keeps explicit sample thresholds, including the seeded tight rows", async () => {
    const { ops } = await loadFixtures();

    for (const threshold of ops.stockThresholds) {
      expect(threshold.reorderLevel).toBeGreaterThan(0);
      expect(ops.itemById(threshold.itemId)?.isStock).toBe(true);
    }
    // Retail shelf has no configured reorder point on purpose.
    expect(
      ops.stockThresholds.some((t) => ops.itemById(t.itemId)?.group === "RTL"),
    ).toBe(false);

    const lowRows = ops.stockRows().filter((row) => row.low);
    expect(lowRows.length).toBeGreaterThan(0);
    expect(
      lowRows.some((row) => ["IT-01", "IT-07", "IT-12"].includes(row.itemId)),
    ).toBe(true);
  });
});

describe("purchase-order progress", () => {
  it("credits only resolvable submitted receipt lines", async () => {
    const { ops } = await loadFixtures();

    for (const receipt of ops.receipts) {
      expect(ops.purchaseOrders.some((po) => po.id === receipt.poId)).toBe(true);
    }
    // The former dangling receipt now resolves to a closed sample PO.
    const dangling = ops.receipts.find((r) => r.id === "DC-PR-0019");
    expect(dangling?.poId).toBe("DC-PO-0106");
    expect(dangling?.lines[0]?.itemId).toBe("IT-12");
    expect(dangling?.lines[0]?.unmapped).toBeUndefined();

    const progress = ops.poProgress("DC-PO-0108");
    expect(progress).not.toBeNull();
    expect(progress?.ordered).toBe(30);
    expect(progress?.received).toBe(22);
    expect(progress?.pending).toBe(8);
    expect(progress?.percent).toBe(Math.round((22 / 30) * 100));
    expect(progress?.unmatchedLines).toEqual([]);

    for (const po of ops.purchaseOrders) {
      const row = ops.poProgress(po.id);
      expect(row?.unmatchedLines).toEqual([]);
      expect(row?.received).toBe(
        row?.lines.reduce((sum, line) => sum + line.received, 0),
      );
    }
  });
});

describe("derived metrics", () => {
  it("computes every KPI from the source rows it displays", async () => {
    const { core, ops } = await loadFixtures();

    // Rolling window used by `totals`/`doctorWorkload`, independent of the
    // weekday the frozen clock happens to land on.
    const sevenDayKeys = ops.recentDayKeys(7);
    expect(sevenDayKeys).toHaveLength(7);
    expect(sevenDayKeys.at(-1)).toBe(ops.businessDay(core.now));

    const totals = ops.totals(7);
    const invoiced = ops.invoices
      .filter((invoice) => sevenDayKeys.includes(ops.businessDay(invoice.date) ?? ""))
      .reduce((sum, invoice) => sum + invoice.total, 0);
    expect(totals.invoiced).toBe(invoiced);
    expect(totals.outstanding).toBe(totals.invoiced - totals.collected);

    const today = ops.businessDay(core.now) ?? "";
    const todayRows = core.appointments.filter(
      (appointment) => appointment.date.slice(0, 10) === today,
    );
    expect(ops.metrics("today").appointments).toBe(todayRows.length);

    const workload = ops.doctorWorkload(7);
    const workloadTotal = workload.reduce((sum, row) => sum + row.count, 0);
    const periodAppointments = core.appointments.filter((appointment) =>
      sevenDayKeys.includes(ops.businessDay(appointment.date) ?? ""),
    );
    expect(workloadTotal).toBe(periodAppointments.length);

    const revenue = ops.revenueByDay(7);
    expect(revenue.map((day) => day.key)).toEqual(sevenDayKeys);
    expect(revenue.reduce((sum, day) => sum + day.invoiced, 0)).toBe(
      totals.invoiced,
    );
  });

  it("derives confirmed revenue from appointment types, not a stored series", async () => {
    const { core, authored } = await loadFixtures();
    const series = authored.confirmedRevenueSeries(5);

    expect(series).toHaveLength(5);
    for (const day of series) {
      const expected = core.appointments
        .filter(
          (appointment) =>
            appointment.date.slice(0, 10) === day.key &&
            appointment.status !== "cancelled" &&
            appointment.status !== "noShow",
        )
        .reduce(
          (sum, appointment) =>
            sum + core.appointmentTypeById(appointment.typeId).price,
          0,
        );
      expect(day.confirmed).toBe(expected);
    }
  });
});

describe("Frappe/Marley vocabulary in the ported masters", () => {
  it("uses canonical role codes plus the Frappe role names they map to", async () => {
    const { ops } = await loadFixtures();

    for (const member of ops.staff) {
      expect(isRoleCode(member.role)).toBe(true);
      expect(member.frappeRoles.length).toBeGreaterThan(0);
      expect(member.docType).toBe("User");
    }
    expect(new Set(ops.staff.map((member) => member.role))).toEqual(
      new Set(["REC", "DOC", "NUR", "PHA", "CAS", "MGR", "ADM"]),
    );
    // The canonical code is the primary role; Frappe's human role names are
    // kept beside it instead of overwriting it.
    for (const member of ops.staff) {
      expect(member.frappeRoles).not.toContain(member.role);
    }
  });

  it("exposes weekdays as ISO numbers instead of raw day codes only", async () => {
    const { ops } = await loadFixtures();

    for (const schedule of ops.schedules) {
      expect(DAY_CODES).toContain(schedule.day);
      expect(schedule.weekday).toBe(DAY_CODES.indexOf(schedule.day) + 1);
      expect(schedule.dayLabelKey).toBe("appointment.day");
    }
  });

  it("documents the installed DocType for every ported master row", async () => {
    const { core, ops } = await loadFixtures();

    expect(core.patients.every((row) => row.docType === "Patient")).toBe(true);
    expect(
      core.appointments.every((row) => row.docType === "Patient Appointment"),
    ).toBe(true);
    expect(core.visits.every((row) => row.docType === "Patient Encounter")).toBe(
      true,
    );
    expect(ops.items.every((row) => row.docType === "Item")).toBe(true);
    expect(ops.batches.every((row) => row.docType === "Batch")).toBe(true);
    expect(ops.invoices.every((row) => row.docType === "Sales Invoice")).toBe(
      true,
    );
    expect(ops.payments.every((row) => row.docType === "Payment Entry")).toBe(
      true,
    );
    expect(ops.purchaseOrders.every((row) => row.docType === "Purchase Order")).toBe(
      true,
    );
    expect(ops.receipts.every((row) => row.docType === "Purchase Receipt")).toBe(
      true,
    );
  });
});

describe("authored gap checklist (audit fixture-coverage list)", () => {
  it("covers every prototype-unbacked designed component", async () => {
    const { core, ops, clinical, authored } = await loadFixtures();

    const checklist: ReadonlyArray<readonly [string, boolean]> = [
      ["photo frames", clinical.photoFrames.length >= 2],
      ["annotation shapes/versions", clinical.annotationVersions.length >= 2],
      [
        "annotation installed vocabulary",
        clinical.annotationVersions.every(
          (row) =>
            (row.state === "pending" || row.state === "committed") &&
            row.draft.length > 0 &&
            clinical.photoDrafts.some((draft) => draft.id === row.draft),
        ),
      ],
      ["photo draft pointer", clinical.photoDrafts.length >= 1],
      [
        "consent evidence fields",
        clinical.consentRecords.every(
          (row) =>
            row.docType === "Clinic Patient Consent" &&
            row.signatureFile.length > 0 &&
            row.signedDatetime.length > 0 &&
            row.evidenceType &&
            row.consentDate,
        ),
      ],
      [
        "consent states",
        new Set(clinical.consentRecords.map((row) => row.stateLabel.en)).size >= 3,
      ],
      [
        "consent Active|Revoked semantics",
        clinical.consentRecords.every(
          (row) => row.status === "Active" || row.status === "Revoked",
        ),
      ],
      [
        "guardian consent",
        clinical.consentRecords.some((row) => row.signerIsGuardian),
      ],
      ["consent form record", clinical.consentForm.version === "PC-2.1"],
      ["redaction notices", clinical.clinicalRedactionNotices.length >= 2],
      [
        "patient identity + emergency contact",
        clinical.patientProfiles.length >= 2 &&
          clinical.patientProfiles.every(
            (row) => row.emergencyContacts.length > 0,
          ),
      ],
      [
        "lab collected state",
        clinical.labSamples.some((row) => row.status === "collected"),
      ],
      [
        "lab cancelled state",
        clinical.labSamples.some((row) => row.status === "cancelled"),
      ],
      [
        "lab private file",
        clinical.labSamples.some((row) => row.files.some((file) => file.isPrivate)),
      ],
      [
        "lab category/priority/summary",
        clinical.labSamples.every(
          (row) => row.category && row.priority && "resultSummary" in row,
        ),
      ],
      ["nurse duty tiles", clinical.nurseDutyTiles.length >= 4],
      ["nurse assignments", clinical.nurseAssignments.length >= 1],
      ["nurse prep/aftercare", clinical.nurseAssignments.every(
        (row) => row.prepChecklist && row.aftercare,
      )],
      ["upcoming procedures", clinical.upcomingProcedures.length >= 1],
      [
        "DOC-01 recent encounters",
        clinical.recentEncountersFor("HP-01").length >= 1,
      ],
      ["DOC-02 amendment", clinical.encounterAmendments.length >= 1],
      ["DOC-02 native lab", clinical.nativeLabSample.modelSupport === "unverified"],
      [
        "DOC-03 per-consumable batches",
        clinical.procedureConsumableBatches.length >= 2 &&
          clinical.procedureConsumableBatches.every((row) =>
            ops.batches.some((batch) => batch.id === row.batchId),
          ),
      ],
      ["cashier shift", authored.cashierShift.status === "open"],
      ["draft invoice", authored.sampleDraftInvoice.docstatus === 0],
      ["cancelled invoice", authored.sampleCancelledInvoice.docstatus === 2],
      ["voided payment", authored.sampleVoidedPayment.posted === false],
      ["discount permission", authored.discountPolicy.approverRoles.length > 0],
      ["refund approval", authored.refundRequests[0]?.status === "pending-manager-approval"],
      ["MGR-01 revenue series", authored.confirmedRevenueSeries(7).length === 7],
      ["MGR-02 workload", ops.doctorWorkload(7).length === core.practitioners.length],
      ["MGR-02 exports", authored.reportExports.length === 5],
      ["MGR-02 inventory trend", authored.inventoryTrend(7).length === 7],
      ["MGR-02 PO history", authored.purchaseOrderHistory().length === ops.purchaseOrders.length],
      ["ADM-02 permission matrix", authored.permissionMatrix.length >= 10],
      ["ADM-03 practitioners", authored.practitionerMasters.length === 3],
      ["ADM-03 lab providers", authored.labProviderMasters.length >= 3],
      ["ADM-04 stock thresholds", authored.stockThresholdSettings.length >= 1],
      ["transfer movements", authored.sampleTransfers.length >= 2],
      ["reconcile request", authored.reconciliationRequests.length >= 1],
      ["REC-05 missing invoice", authored.checkoutSamples.missingInvoice.visit.state === "completed"],
      ["REC-05 incomplete visit", authored.checkoutSamples.incompleteVisit.gaps.length > 0],
      ["company IDs", authored.company.id === ops.company.id],
      ["REC-04 return-visit source", authored.returnVisitSource.source === "REC-04"],
    ];

    for (const [label, ok] of checklist) expect(ok, label).toBe(true);
  });

  it("keeps consent evidence internally consistent", async () => {
    const { core, clinical, ops } = await loadFixtures();
    const today = ops.businessDay(new Date().toISOString()) ?? "";

    for (const record of clinical.consentRecords) {
      expect(record.consentFormId).toBe(clinical.consentForm.id);
      expect(record.docType).toBe("Clinic Patient Consent");
      expect(record.status === "Active" || record.status === "Revoked").toBe(
        true,
      );
      // Required installed fields are always modelled.
      expect(record.signatureFile).toMatch(/^demo\/consent\/.+\.pdf$/);
      expect(record.signedDatetime).not.toBe("");
      if (record.patientId.startsWith("P-")) {
        expect(core.patientById(record.patientId)).toBeTruthy();
      }
    }

    const withdrawn = clinical.consentRecords.find(
      (record) => record.status === "Revoked",
    );
    expect(withdrawn?.withdrawnAt).toBeTruthy();
    expect(withdrawn?.withdrawnReason).toBeTruthy();
    expect(withdrawn?.stateLabel.en).toBe("Withdrawn");

    const expired = clinical.consentRecords.find(
      (record) => record.stateLabel.en === "Expired",
    );
    expect(expired?.status).toBe("Active");
    expect(expired?.validUntil).toBeTruthy();
    expect((expired?.validUntil ?? today) < today).toBe(true);

    const minor = clinical.consentRecords.find((record) => record.signerIsGuardian);
    expect(minor?.signerRelation).toBe("guardian");
  });

  it("derives the MGR-02 inventory trend from ledger movements", async () => {
    const { ops, authored } = await loadFixtures();

    const trend = authored.inventoryTrend(7);
    expect(trend.map((day) => day.key)).toEqual(ops.recentDayKeys(7));

    const costByBatch = new Map(ops.batches.map((b) => [b.id, b.cost]));
    for (const day of trend) {
      const upTo = ops.movements.filter(
        (m) => ops.businessDay(m.at)! <= day.key,
      );
      const sameDay = ops.movements.filter(
        (m) => ops.businessDay(m.at) === day.key,
      );
      expect(day.closingUnits).toBe(
        upTo.reduce((sum, m) => sum + m.qty, 0),
      );
      expect(day.closingValue).toBe(
        upTo.reduce((sum, m) => sum + m.qty * (costByBatch.get(m.batchId) ?? 0), 0),
      );
      expect(day.inboundUnits).toBe(
        sameDay.filter((m) => m.qty > 0).reduce((s, m) => s + m.qty, 0),
      );
      expect(day.outboundUnits).toBe(
        sameDay.filter((m) => m.qty < 0).reduce((s, m) => s - m.qty, 0),
      );
    }
    // Last closing balance reconciles with the physical batch total.
    const physical = ops.batches.reduce((sum, batch) => sum + batch.qty, 0);
    expect(trend.at(-1)?.closingUnits).toBe(physical);
  });

  it("audit rows reference the actual seeded first posted payment", async () => {
    const { ops } = await loadFixtures();
    const firstPosted = ops.payments.find((payment) => payment.posted);
    const audit = ops.audit.find((row) => row.id === "AUD-6");

    expect(firstPosted?.id).toBe("DC-PAY-0312");
    expect(firstPosted?.mode).toBe("card");
    expect(audit?.entity).toBe(firstPosted?.id);
    expect(audit?.at).toBe(firstPosted?.date);
    expect(audit?.note.en).toBe("Card · ฿7,276.00");
  });

  it("returns deep-copied metric invoice rows, never live ledger refs", async () => {
    const { ops } = await loadFixtures();
    const rows = ops.metrics("week").invoiceRows;
    expect(rows.length).toBeGreaterThan(0);
    const source = ops.invoices.find((invoice) => invoice.id === rows[0]?.id);
    expect(source).toBeDefined();
    expect(rows[0]).not.toBe(source);
    expect(rows[0]?.lines).not.toBe(source?.lines);
  });

  it("keeps authored samples out of the prototype-derived core counts", async () => {
    const { core, ops } = await loadFixtures();
    // Authored checkout visits must not change the ported volume assertions.
    expect(core.visits.some((v) => v.id.startsWith("SAMPLE-VIS"))).toBe(false);
    expect(ops.invoiceForVisit("SAMPLE-VIS-CHECKOUT-01")).toBeNull();
  });

  it("pairs transfer movements so no lot gains or loses stock twice", async () => {
    const { ops, authored } = await loadFixtures();

    for (const movement of authored.sampleTransfers) {
      expect(ops.batches.some((batch) => batch.id === movement.batchId)).toBe(true);
      expect(movement.targetWarehouse).toBeTruthy();
    }
    const net = authored.sampleTransfers.reduce((sum, row) => sum + row.qty, 0);
    expect(net).toBe(0);

    for (const request of authored.reconciliationRequests) {
      expect(request.status).toBe("pending-verification");
      expect(request.noStockChange).toBe(true);
      expect(ops.batches.some((batch) => batch.id === request.batchId)).toBe(true);
    }
  });

  it("carries inventory warehouse provenance on every purchase order and line", async () => {
    const { ops } = await loadFixtures();
    for (const po of ops.purchaseOrders) {
      expect(po.warehouse, po.id).toBeTruthy();
      for (const line of po.items) expect(line.warehouse, po.id).toBeTruthy();
    }
  });

  it("resolves the procedure consumable to a lot that owns the item", async () => {
    const { core, clinical, ops } = await loadFixtures();

    const line = core.visitById("DC-VIS-2041")?.procedures?.[0]?.consumables[0];
    // The prototype hard-coded lot B-2411 (an unrelated generated lot).
    expect(line?.batch).toBeNull();
    expect(line?.batchPolicy).toBe("oldest-usable");

    const resolved = clinical.procedureConsumableBatches.find(
      (row) => row.procedureId === "DC-PROC-118",
    );
    expect(resolved).toBeDefined();
    const batch = ops.batches.find((row) => row.id === resolved?.batchId);
    expect(batch?.itemId).toBe("IT-07");
    expect(batch?.qty).toBeGreaterThan(0);
  });

  it("ties authored checkout invoices to their sample visit patient", async () => {
    const { authored } = await loadFixtures();

    expect(authored.sampleDraftInvoice.visitId).toBe(
      authored.checkoutSamples.missingInvoice.visit.id,
    );
    expect(authored.sampleDraftInvoice.patientId).toBe(
      authored.checkoutSamples.missingInvoice.visit.patientId,
    );
    expect(authored.sampleCancelledInvoice.visitId).toBe(
      authored.checkoutSamples.incompleteVisit.visit.id,
    );
    expect(authored.sampleCancelledInvoice.patientId).toBe(
      authored.checkoutSamples.incompleteVisit.visit.patientId,
    );
  });
});
