export type VisitSection =
  | "overview"
  | "history"
  | "exam"
  | "note"
  | "diagnoses"
  | "medications"
  | "procedures"
  | "lab"
  | "photos"
  | "review";

export interface VisitContext {
  encounterId: string;
  patientId: string;
  patientName: string;
  appointmentId?: string;
  practitionerName: string;
  startedAt: string;
  state: string;
}

export function isValidVisitContext(context: VisitContext): boolean {
  return [
    context.encounterId,
    context.patientId,
    context.practitionerName,
  ].every((value) => value.trim().length > 0);
}
