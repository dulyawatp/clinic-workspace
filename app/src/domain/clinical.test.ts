import { describe, expect, it } from "vitest";
import { SECTION_ORDER, sectionLabelKey } from "../features/clinical/sections";
import { initI18n } from "../i18n";
import { isValidVisitContext, type VisitContext } from "./clinical";

const baseCtx: VisitContext = {
  encounterId: "ENC-1",
  patientId: "PAT-1",
  patientName: "Fictional Patient",
  practitionerName: "Doctor",
  startedAt: "2026-11-05",
  state: "open",
};

describe("clinical section model", () => {
  it("has exactly one section per spec section", () => {
    expect(SECTION_ORDER).toEqual([
      "overview",
      "history",
      "exam",
      "note",
      "diagnoses",
      "medications",
      "procedures",
      "lab",
      "photos",
      "review",
    ]);
  });
  it("labels every section in both catalogues", () => {
    for (const locale of ["th", "en"] as const)
      for (const section of SECTION_ORDER)
        expect(initI18n(locale).t(sectionLabelKey(section))).not.toBe(
          sectionLabelKey(section),
        );
  });
  it("treats a missing encounter id as invalid context", () => {
    expect(isValidVisitContext({ ...baseCtx, encounterId: "" })).toBe(false);
    expect(isValidVisitContext({ ...baseCtx, patientId: " " })).toBe(false);
    expect(isValidVisitContext({ ...baseCtx, practitionerName: "" })).toBe(
      false,
    );
    expect(isValidVisitContext(baseCtx)).toBe(true);
  });
});
