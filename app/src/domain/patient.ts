/**
 * List-row patient DTO. Identity numbers are never part of a summary: the
 * mobile arrives masked and no dialable or national-ID value is returned.
 *
 * `duplicateCandidate` is declared by the server (the duplicate rule is a
 * backend contract). The client never infers duplicates from name or phone.
 */
export interface PatientSummary {
  id: string;
  fullName: string;
  mobileMasked: string;
  dob?: string;
  lastVisitAt?: string;
  fictional: true;
  duplicateCandidate?: boolean;
  /** Server-declared last practitioner; never inferred from a timeline row. */
  latestPractitioner?: string;
}

export interface BillingPatientSummary {
  id: string;
  fullName: string;
  fictional: true;
}

/**
 * One saved measurement batch. A missing measurement is `null` in the fixture
 * and is rendered as "not measured": it is never coerced to zero or an empty
 * string that could read as a value.
 */
export interface LatestVitals {
  id: string;
  patientId: string;
  measuredAt: string;
  source: string;
  recordedBy: string;
  temp: number | null;
  pulse: number | null;
  systolic: number | null;
  diastolic: number | null;
  respiratory: number | null;
  height: number | null;
  weight: number | null;
  fictional: true;
}

/** Timeline kinds are permission-filtered by the surface that renders them. */
export type TimelineKind = "visit" | "lab" | "treatment" | "photo";

export const TIMELINE_KINDS: readonly TimelineKind[] = [
  "visit",
  "lab",
  "treatment",
  "photo",
];

export function isTimelineKind(value: unknown): value is TimelineKind {
  return (
    typeof value === "string" && TIMELINE_KINDS.includes(value as TimelineKind)
  );
}

export interface TimelineEntry {
  id: string;
  patientId: string;
  kind: TimelineKind;
  at: string;
  /** Owning record id; a reference is displayed, never turned into a URL. */
  reference: string;
  fictional: true;
}

export interface PaymentEntry {
  id: string;
  patientId: string;
  issuedAt: string;
  status: string;
  total: number;
  currency: string;
  fictional: true;
}

const PAYMENT_LABELS: Readonly<Record<string, string>> = {
  paid: "status.paid",
  partial: "status.partial",
  unpaid: "status.unpaid",
};
export function mapPaymentStatus(raw: string): string | null {
  return Object.hasOwn(PAYMENT_LABELS, raw)
    ? (PAYMENT_LABELS[raw] ?? null)
    : null;
}
