import { describe, expect, it } from "vitest";
import { loadConfig } from "./config.js";

function liveEnv(overrides: NodeJS.ProcessEnv = {}): NodeJS.ProcessEnv {
  return {
    UPSTREAM_MODE: "live",
    CLINIC_LIVE_LAB: "1",
    FRAPPE_ORIGIN: "https://lab.example",
    CLINIC_SYNTHETIC_PATIENTS: "PAT-1=SYNTH-PATIENT-1",
    CLINIC_LIVE_CLINICIAN_ROLES: "Clinic Clinician",
    ...overrides,
  };
}

describe("loadConfig", () => {
  it("defaults to fixture mode with no live upstream", () => {
    const config = loadConfig({});
    expect(config.upstreamMode).toBe("fixture");
    expect(config.live).toBeUndefined();
  });

  it("keeps fixture mode when the lab opt-in is set without live mode", () => {
    const config = loadConfig({
      UPSTREAM_MODE: "fixture",
      CLINIC_LIVE_LAB: "1",
      FRAPPE_ORIGIN: "https://lab.example",
    });
    expect(config.upstreamMode).toBe("fixture");
    expect(config.live).toBeUndefined();
  });

  it("refuses live mode without the explicit lab opt-in", () => {
    expect(() => loadConfig(liveEnv({ CLINIC_LIVE_LAB: undefined }))).toThrow(
      /LIVE_LAB_NOT_ENABLED/,
    );
  });

  it("refuses live mode without a Frappe origin", () => {
    expect(() => loadConfig(liveEnv({ FRAPPE_ORIGIN: undefined }))).toThrow(
      /MISSING_FRAPPE_ORIGIN/,
    );
  });

  it("refuses a non-http or non-canonical Frappe origin", () => {
    expect(() =>
      loadConfig(liveEnv({ FRAPPE_ORIGIN: "file:///etc/passwd" })),
    ).toThrow(/(INVALID|INSECURE)_FRAPPE_ORIGIN/);
    expect(() =>
      loadConfig(liveEnv({ FRAPPE_ORIGIN: "https://lab.example/" })),
    ).toThrow(/(INVALID|INSECURE)_FRAPPE_ORIGIN/);
  });

  it("allows http only for a loopback lab origin", () => {
    expect(() =>
      loadConfig(liveEnv({ FRAPPE_ORIGIN: "http://lab.example:8000" })),
    ).toThrow(/INSECURE_FRAPPE_ORIGIN/);
    expect(() =>
      loadConfig(liveEnv({ FRAPPE_ORIGIN: "http://127.0.0.1:8000" })),
    ).not.toThrow();
    expect(() =>
      loadConfig(liveEnv({ FRAPPE_ORIGIN: "http://localhost:8000" })),
    ).not.toThrow();
  });

  it("refuses live mode without explicit clinician roles", () => {
    expect(() =>
      loadConfig(liveEnv({ CLINIC_LIVE_CLINICIAN_ROLES: undefined })),
    ).toThrow(/MISSING_LIVE_CLINICIAN_ROLES/);
  });

  it("refuses live mode without an explicit synthetic patient allow-list", () => {
    expect(() =>
      loadConfig(liveEnv({ CLINIC_SYNTHETIC_PATIENTS: undefined })),
    ).toThrow(/MISSING_SYNTHETIC_PATIENTS/);
  });

  it("accepts the complete lab opt-in and keeps the mapping server-side", () => {
    const config = loadConfig(liveEnv());
    expect(config.upstreamMode).toBe("live");
    expect(config.live?.origin).toBe("https://lab.example");
    expect(config.live?.syntheticPatients.get("PAT-1")).toBe("SYNTH-PATIENT-1");
    expect(config.live?.syntheticPatients.size).toBe(1);
  });

  it("rejects a duplicate or malformed patient allow-list", () => {
    expect(() =>
      loadConfig(
        liveEnv({
          CLINIC_SYNTHETIC_PATIENTS: "PAT-1=A,PAT-1=B",
        }),
      ),
    ).toThrow(/INVALID_SYNTHETIC_PATIENTS/);
    expect(() =>
      loadConfig(
        liveEnv({
          CLINIC_SYNTHETIC_PATIENTS: "PAT 1=A",
        }),
      ),
    ).toThrow(/INVALID_SYNTHETIC_PATIENTS/);
    expect(() =>
      loadConfig(
        liveEnv({
          CLINIC_SYNTHETIC_PATIENTS: "../PAT-1=A",
        }),
      ),
    ).toThrow(/INVALID_SYNTHETIC_PATIENTS/);
    expect(() =>
      loadConfig(
        liveEnv({
          CLINIC_SYNTHETIC_PATIENTS: "PAT-1=..",
        }),
      ),
    ).toThrow(/INVALID_SYNTHETIC_PATIENTS/);
  });

  it("keeps the configured native allow-list server-side", () => {
    const config = loadConfig(liveEnv());
    expect(config.live?.clinicianRoles).toEqual(new Set(["Clinic Clinician"]));
  });

  it("accepts explicit reception roles and rejects broad or overlapping role maps", () => {
    expect(
      loadConfig(liveEnv({ CLINIC_LIVE_RECEPTION_ROLES: "DC Receptionist" }))
        .live?.receptionRoles,
    ).toEqual(new Set(["DC Receptionist"]));
    for (const role of [
      "All",
      "Guest",
      "System Manager",
      "Administrator",
      "Desk User",
    ]) {
      expect(() =>
        loadConfig(liveEnv({ CLINIC_LIVE_RECEPTION_ROLES: role })),
      ).toThrow(/INVALID_LIVE_ROLE_MAP/);
      expect(() =>
        loadConfig(liveEnv({ CLINIC_LIVE_CLINICIAN_ROLES: role })),
      ).toThrow(/INVALID_LIVE_ROLE_MAP/);
    }
    expect(() =>
      loadConfig(liveEnv({ CLINIC_LIVE_RECEPTION_ROLES: "Clinic Clinician" })),
    ).toThrow(/INVALID_LIVE_ROLE_MAP/);
  });

  it("requires explicit synthetic items for pharmacy and rejects non-demo items", () => {
    expect(() =>
      loadConfig(liveEnv({ CLINIC_LIVE_PHARMACY_ROLES: "DC Pharmacy" })),
    ).toThrow(/MISSING_SYNTHETIC_ITEMS/);
    expect(() =>
      loadConfig(
        liveEnv({
          CLINIC_LIVE_PHARMACY_ROLES: "DC Pharmacy",
          CLINIC_SYNTHETIC_ITEMS: "REAL-ITEM",
        }),
      ),
    ).toThrow(/INVALID_SYNTHETIC_ITEMS/);
    expect(
      loadConfig(
        liveEnv({
          CLINIC_LIVE_PHARMACY_ROLES: "DC Pharmacy",
          CLINIC_SYNTHETIC_ITEMS: "DEMO-ITEM-1",
        }),
      ).live?.syntheticItems,
    ).toEqual(new Set(["DEMO-ITEM-1"]));
  });

  it("keeps the synthetic appointment allow-list empty and optional", () => {
    expect(loadConfig(liveEnv()).live?.syntheticAppointments).toEqual(
      new Set(),
    );
    expect(
      loadConfig(liveEnv({ CLINIC_SYNTHETIC_APPOINTMENTS: "  " })).live
        ?.syntheticAppointments,
    ).toEqual(new Set());
  });

  it("parses a comma-separated synthetic appointment allow-list", () => {
    expect(
      loadConfig(liveEnv({ CLINIC_SYNTHETIC_APPOINTMENTS: "APPT-1, APPT-2" }))
        .live?.syntheticAppointments,
    ).toEqual(new Set(["APPT-1", "APPT-2"]));
  });

  it("rejects unsafe synthetic appointment names", () => {
    for (const raw of ["../APPT-1", "APPT  1", "APPT\n1"]) {
      expect(() =>
        loadConfig(liveEnv({ CLINIC_SYNTHETIC_APPOINTMENTS: raw })),
      ).toThrow(/INVALID_SYNTHETIC_APPOINTMENTS/);
    }
  });

  it("accepts a native Patient docname with single spaces but never traversal", () => {
    const spaced = loadConfig(
      liveEnv({ CLINIC_SYNTHETIC_PATIENTS: "PAT-1=Synthetic Patient One" }),
    );
    expect(spaced.live?.syntheticPatients.get("PAT-1")).toBe(
      "Synthetic Patient One",
    );
    expect(() =>
      loadConfig(
        liveEnv({ CLINIC_SYNTHETIC_PATIENTS: "PAT-1=Synthetic  Patient" }),
      ),
    ).toThrow(/INVALID_SYNTHETIC_PATIENTS/);
  });

  it("defaults live photo writes off for a live lab", () => {
    expect(loadConfig(liveEnv()).livePhotoWrites).toBe(false);
    expect(
      loadConfig(liveEnv({ CLINIC_LIVE_PHOTO_WRITES: "0" })).livePhotoWrites,
    ).toBe(false);
    expect(
      loadConfig(liveEnv({ CLINIC_LIVE_PHOTO_WRITES: "yes" })).livePhotoWrites,
    ).toBe(false);
  });

  it("enables live photo writes only with the explicit numeric opt-in", () => {
    expect(
      loadConfig(liveEnv({ CLINIC_LIVE_PHOTO_WRITES: "1" })).livePhotoWrites,
    ).toBe(true);
    expect(
      loadConfig(liveEnv({ CLINIC_LIVE_PHOTO_WRITES: "true" })).livePhotoWrites,
    ).toBe(true);
  });

  it("never enables live photo writes in fixture mode", () => {
    const config = loadConfig({
      UPSTREAM_MODE: "fixture",
      CLINIC_LIVE_PHOTO_WRITES: "1",
      CLINIC_LIVE_PATIENT_WRITES: "1",
    });
    expect(config.upstreamMode).toBe("fixture");
    expect(config.livePhotoWrites).toBe(false);
    expect(config.livePatientWrites).toBe(false);
  });

  it("keeps patient create opt-in independent of the photo opt-in", () => {
    expect(loadConfig(liveEnv()).livePatientWrites).toBe(false);
    expect(
      loadConfig(liveEnv({ CLINIC_LIVE_PHOTO_WRITES: "1" })).livePatientWrites,
    ).toBe(false);
    expect(
      loadConfig(liveEnv({ CLINIC_LIVE_PATIENT_WRITES: "1" })).livePhotoWrites,
    ).toBe(false);
    expect(
      loadConfig(liveEnv({ CLINIC_LIVE_PATIENT_WRITES: "true" }))
        .livePatientWrites,
    ).toBe(true);
  });

  it("keeps encounter draft opt-in independent of the other write opt-ins", () => {
    expect(loadConfig(liveEnv()).liveEncounterWrites).toBe(false);
    expect(
      loadConfig(liveEnv({ CLINIC_LIVE_PATIENT_WRITES: "1" }))
        .liveEncounterWrites,
    ).toBe(false);
    expect(
      loadConfig(liveEnv({ CLINIC_LIVE_PHOTO_WRITES: "1" }))
        .liveEncounterWrites,
    ).toBe(false);
    expect(
      loadConfig(
        liveEnv({
          CLINIC_LIVE_ENCOUNTER_WRITES: "true",
          CLINIC_SYNTHETIC_ENCOUNTER_APPOINTMENT_TYPE: "Synthetic Follow Up",
          CLINIC_SYNTHETIC_ENCOUNTER_PRACTITIONERS:
            "clinician@lab.example=PRACT-1",
        }),
      ).liveEncounterWrites,
    ).toBe(true);
  });

  it("refuses the encounter opt-in without a server-owned mapping", () => {
    expect(() =>
      loadConfig(liveEnv({ CLINIC_LIVE_ENCOUNTER_WRITES: "1" })),
    ).toThrow(/MISSING_SYNTHETIC_ENCOUNTER_CONFIG/);
    expect(() =>
      loadConfig(
        liveEnv({
          CLINIC_LIVE_ENCOUNTER_WRITES: "1",
          CLINIC_SYNTHETIC_ENCOUNTER_APPOINTMENT_TYPE: "Synthetic Follow Up",
        }),
      ),
    ).toThrow(/MISSING_SYNTHETIC_ENCOUNTER_CONFIG/);
    expect(() =>
      loadConfig(
        liveEnv({
          CLINIC_LIVE_ENCOUNTER_WRITES: "1",
          CLINIC_SYNTHETIC_ENCOUNTER_PRACTITIONERS:
            "clinician@lab.example=PRACT-1",
        }),
      ),
    ).toThrow(/MISSING_SYNTHETIC_ENCOUNTER_CONFIG/);
  });

  it("parses the encounter practitioner mapping and appointment type", () => {
    const config = loadConfig(
      liveEnv({
        CLINIC_SYNTHETIC_ENCOUNTER_APPOINTMENT_TYPE: "Synthetic Follow Up",
        CLINIC_SYNTHETIC_ENCOUNTER_PRACTITIONERS:
          "clinician@lab.example=PRACT-1, other@lab.example=PRACT-2",
      }),
    );
    expect(config.live?.encounterAppointmentType).toBe("Synthetic Follow Up");
    expect(
      config.live?.encounterPractitioners?.get("clinician@lab.example"),
    ).toBe("PRACT-1");
    expect(config.live?.encounterPractitioners?.get("other@lab.example")).toBe(
      "PRACT-2",
    );
    expect(config.liveEncounterWrites).toBe(false);
  });

  it("rejects a malformed encounter mapping", () => {
    expect(() =>
      loadConfig(
        liveEnv({
          CLINIC_SYNTHETIC_ENCOUNTER_PRACTITIONERS: "clinician@lab.example",
        }),
      ),
    ).toThrow(/INVALID_SYNTHETIC_ENCOUNTER_PRACTITIONERS/);
    expect(() =>
      loadConfig(
        liveEnv({
          CLINIC_SYNTHETIC_ENCOUNTER_PRACTITIONERS:
            "clinician@lab.example=PRACT-1,clinician@lab.example=PRACT-2",
        }),
      ),
    ).toThrow(/INVALID_SYNTHETIC_ENCOUNTER_PRACTITIONERS/);
    expect(() =>
      loadConfig(
        liveEnv({
          CLINIC_SYNTHETIC_ENCOUNTER_APPOINTMENT_TYPE: "../escape",
        }),
      ),
    ).toThrow(/INVALID_SYNTHETIC_ENCOUNTER_APPOINTMENT_TYPE/);
  });
});
