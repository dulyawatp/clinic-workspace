import { afterEach, describe, expect, it, vi } from "vitest";
import {
  bangkokTodayKey,
  clearCreateDraft,
  CREATE_DRAFT_KEY,
  CREATE_SAMPLES_KEY,
  type DirectoryCandidate,
  emptyCreateInput,
  findDuplicateCandidates,
  type PatientCreateInput,
  readCreateDraft,
  saveSampleRegistration,
  validatePatientCreate,
  writeCreateDraft,
} from "./patient-create";

const TODAY = "2026-11-15";

function input(overrides: Partial<PatientCreateInput> = {}): PatientCreateInput {
  return {
    ...emptyCreateInput(),
    firstName: "สมชาย",
    lastName: "ใจดี",
    sex: "male",
    dob: "1990-01-02",
    phone: "081-234-5678",
    allergyStatus: "none",
    ...overrides,
  };
}

afterEach(() => {
  localStorage.clear();
  vi.restoreAllMocks();
});

describe("PAT-02 create validation", () => {
  it("accepts the designed ten-field sample", () => {
    expect(validatePatientCreate(input(), TODAY)).toEqual([]);
  });

  it("requires identity, sex, birth date and mobile", () => {
    const errors = validatePatientCreate(emptyCreateInput(), TODAY);
    expect(errors.map((error) => error.field)).toEqual([
      "firstName",
      "lastName",
      "sex",
      "dob",
      "phone",
    ]);
    expect(errors.every((error) => error.key === "patientCreate.error.required"))
      .toBe(true);
  });

  it.each(["08123456", "08123456789", "812345678", "081-234-5678x"])(
    "rejects mobile %s outside the prototype pattern",
    (phone) => {
      expect(
        validatePatientCreate(input({ phone }), TODAY).map((e) => e.field),
      ).toContain("phone");
    },
  );

  it.each(["0812345678", "081-234-567", "081 234 5678"])(
    "accepts mobile %s",
    (phone) => {
      expect(
        validatePatientCreate(input({ phone }), TODAY).map((e) => e.field),
      ).not.toContain("phone");
    },
  );

  it("rejects a future or impossible birth date", () => {
    expect(
      validatePatientCreate(input({ dob: "2026-11-16" }), TODAY)[0]?.key,
    ).toBe("patientCreate.error.dobFuture");
    expect(
      validatePatientCreate(input({ dob: "2023-02-29" }), TODAY)[0]?.key,
    ).toBe("patientCreate.error.dob");
  });

  it("validates optional email only when it is filled", () => {
    expect(
      validatePatientCreate(input({ email: "not-an-email" }), TODAY)[0]?.key,
    ).toBe("patientCreate.error.email");
    expect(validatePatientCreate(input({ email: "" }), TODAY)).toEqual([]);
  });

  it("requires an allergy name only for a known allergy", () => {
    expect(
      validatePatientCreate(
        input({ allergyStatus: "known", allergyText: " " }),
        TODAY,
      )[0]?.key,
    ).toBe("patientCreate.error.allergy");
    expect(
      validatePatientCreate(
        input({ allergyStatus: "known", allergyText: "เพนิซิลลิน" }),
        TODAY,
      ),
    ).toEqual([]);
    expect(
      validatePatientCreate(input({ allergyStatus: "none" }), TODAY),
    ).toEqual([]);
  });

  it("uses the Bangkok clinic day for a non-empty today key", () => {
    expect(bangkokTodayKey(new Date("2026-11-15T17:30:00Z"))).toBe("2026-11-16");
  });
});

describe("PAT-02 duplicate candidates", () => {
  const rows: DirectoryCandidate[] = [
    { id: "fixture-1", name: "สมชาย ใจดี", phone: "081-234-5678" },
    { id: "fixture-2", name: "อารีย์ ใจดี", phone: "082-XXX-4411" },
    { id: "fixture-3", name: "Somchai Jaidee", phone: "089-999-9999" },
  ];

  it("matches the same name with normalized whitespace and case", () => {
    expect(
      findDuplicateCandidates(
        input({ firstName: "  somchai ", lastName: "JAIDEE" }),
        rows,
      ).map((row) => row.id),
    ).toContain("fixture-3");
  });

  it("matches the same unmasked mobile in digits", () => {
    expect(
      findDuplicateCandidates(
        input({ firstName: "อื่น", lastName: "อื่น", phone: "0812345678" }),
        rows,
      ).map((row) => row.id),
    ).toEqual(["fixture-1"]);
  });

  it("never treats a masked mobile as a duplicate match", () => {
    expect(
      findDuplicateCandidates(
        input({ firstName: "อื่น", lastName: "อื่น", phone: "0820004411" }),
        rows,
      ),
    ).toEqual([]);
  });

  it("does not fuzzy-match a partial name", () => {
    expect(
      findDuplicateCandidates(
        input({
          firstName: "สมชาย",
          lastName: "อื่น",
          phone: "089-000-0000",
        }),
        rows,
      ),
    ).toEqual([]);
  });
});

describe("PAT-02 local draft", () => {
  it("survives a reload with every designed field", () => {
    const entry = input({
      email: "a@example.com",
      allergyStatus: "known",
      allergyText: "ลิโดเคน",
      address: "เขตวัฒนา",
      emergency: "ปวีณา · พี่สาว · 089-000-1111",
    });
    expect(writeCreateDraft(entry)).toBe(true);
    expect(readCreateDraft()).toEqual(entry);
  });

  it("ignores a missing, malformed or foreign-version draft", () => {
    expect(readCreateDraft()).toBeNull();
    localStorage.setItem(CREATE_DRAFT_KEY, "{not json");
    expect(readCreateDraft()).toBeNull();
    localStorage.setItem(
      CREATE_DRAFT_KEY,
      JSON.stringify({ version: 2, input: input() }),
    );
    expect(readCreateDraft()).toBeNull();
    localStorage.setItem(
      CREATE_DRAFT_KEY,
      JSON.stringify({ version: 1, input: { ...input(), sex: "robot" } }),
    );
    expect(readCreateDraft()).toBeNull();
  });

  it("clears the stored draft", () => {
    writeCreateDraft(input());
    clearCreateDraft();
    expect(localStorage.getItem(CREATE_DRAFT_KEY)).toBeNull();
  });

  it("reports a storage failure instead of throwing", () => {
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("full");
    });
    expect(writeCreateDraft(input())).toBe(false);
  });
});

describe("PAT-02 sample registration", () => {
  it("stores the full designed record locally and numbers receipts", () => {
    const first = saveSampleRegistration(
      input({ emergency: "ปวีณา · 089-000-1111" }),
    );
    const second = saveSampleRegistration(input({ firstName: "อารีย์" }));
    expect(first?.id).toBe("SAMPLE-REG-001");
    expect(second?.id).toBe("SAMPLE-REG-002");
    const rows = JSON.parse(
      localStorage.getItem(CREATE_SAMPLES_KEY) ?? "[]",
    ) as Array<{ details: PatientCreateInput }>;
    expect(rows).toHaveLength(2);
    expect(rows[0]?.details.emergency).toBe("ปวีณา · 089-000-1111");
  });
});
