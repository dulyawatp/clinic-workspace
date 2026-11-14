import frontdesk from "@clinic/contracts/fixtures/frontdesk.json";
import { describe, expect, it } from "vitest";
import type { AppointmentSummary } from "./appointment";
import type { PatientSummary } from "./patient";
import { mapQueueStatus, QUEUE_STATUSES } from "./queue";

function makePatient(): PatientSummary {
  return {
    id: "fixture-dir-01",
    fullName: "สมชาย ใจดี",
    mobileMasked: "081-XXX-2233",
    dob: "1985-04-12",
    lastVisitAt: "2026-11-03",
    fictional: true,
  };
}

describe("queue status display map", () => {
  it("never maps an unknown status onto waiting", () => {
    expect(mapQueueStatus("marley_unknown_state")).toEqual({
      labelKey: null,
      raw: "marley_unknown_state",
    });
  });

  it("keeps the raw value alongside a known label", () => {
    expect(mapQueueStatus("arrived").raw).toBe("arrived");
  });

  it("maps every declared status explicitly, not by substring", () => {
    for (const status of QUEUE_STATUSES) {
      const mapped = mapQueueStatus(status);
      expect(mapped.raw).toBe(status);
      expect(mapped.labelKey).not.toBeNull();
    }
    expect(mapQueueStatus("waiting_room").labelKey).toBeNull();
  });
});

describe("patient summary", () => {
  it("does not expose a full identity number in a summary", () => {
    const p: PatientSummary = makePatient();
    expect(JSON.stringify(p)).not.toMatch(/\d{13}/);
  });

  it("carries an already-masked mobile that is not dialable", () => {
    for (const row of frontdesk.patients) {
      expect(row.mobileMasked).toMatch(/X/i);
      const digits = row.mobileMasked.replace(/\D/g, "");
      expect(digits.length).toBeLessThan(9);
    }
  });
});

describe("front-desk fixtures", () => {
  it("marks every front-desk row as fictional", () => {
    for (const group of [
      frontdesk.patients,
      frontdesk.duplicates,
      frontdesk.appointments,
      frontdesk.queue,
      frontdesk.kpis,
      frontdesk.checkouts,
    ]) {
      for (const row of group) expect(row.fictional).toBe(true);
    }
  });

  it("declares appointment rows in the summary shape", () => {
    for (const row of frontdesk.appointments) {
      const summary: AppointmentSummary = row;
      expect(summary.startsAt).toBe(row.startsAt);
      expect(summary.status).toBe(row.status);
    }
  });
});
