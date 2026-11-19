import { coreData, type Visit } from "../../fixtures/data";
import { opsData } from "../../fixtures/data-ops";
import { translateClinicalText } from "../../i18n";

export const SAMPLE_REPORT = "sample-lab-report.pdf";
export type PhotoSide = "before" | "after";

export interface SampleHistoryPreview {
  visitId: string;
  reportAdded: boolean;
  sides: PhotoSide[];
  savedNote: string | null;
}

export function approvedVisitNarrative(visit: Visit, translate: (key: string) => string) {
  return [
    visit.history.hpi,
    visit.exam.findings,
    visit.note.assessment ?? (visit.note.assessmentKey && translate(visit.note.assessmentKey)),
    visit.note.plan ?? (visit.note.planKey && translate(visit.note.planKey)),
    visit.note.followup,
  ].filter((line): line is string => Boolean(line)).map(translateClinicalText).join("\n\n");
}

export function sampleLabFiles(visit: Visit): string[] {
  const files = opsData.externalLabs
    .filter((row) => row.patientId === visit.patientId && row.visitId === visit.id)
    .flatMap((row) => (row.file ? [row.file] : []));
  return [...new Set([...files, ...(visit.labs ?? []).flatMap((row) => row.file ? [row.file] : [])])];
}

/** Authored association for the synthetic timeline illustration, not a pairing
 * inferred from a patient-scoped gallery or timestamps. */
export function samplePhotoSides(visit: Visit): PhotoSide[] {
  if (visit.patientId !== "P-10241") return [];
  if (visit.id === "DC-VIS-2041") return ["before"];
  return visit.id === "SAMPLE-HISTORY-02" ? ["before", "after"] : [];
}

/** Preview-only earlier encounters, never linked as routable clinical records. */
export function sampleVisitHistory(patientId: string): Visit[] {
  const visits = coreData.visitsFor(patientId);
  if (patientId === "P-10241") {
    for (const [id, days, noteKey] of [
      ["SAMPLE-HISTORY-02", -21, "workspace.history.followUp"],
      ["SAMPLE-HISTORY-01", -42, "workspace.history.consultation"],
    ] as const) {
      visits.push({
        id,
        patientId,
        docType: "Patient Encounter",
        appointmentId: null,
        practitioner: "HP-01",
        date: coreData.dates.at(days, "10:00"),
        state: "completed",
        history: { reviewedAllergies: true },
        exam: {},
        note: { assessmentKey: noteKey, signed: true },
        diagnoses: [],
        meds: [],
        sample: true,
      });
    }
  }
  return visits.sort((left, right) => Date.parse(right.date) - Date.parse(left.date));
}
