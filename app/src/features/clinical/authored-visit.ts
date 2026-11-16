/**
 * Pure state logic for the designed DOC-02 canonical editor demo.
 *
 * The editor is a fixture-driven state machine: values start from the authored
 * sample encounter, a local save only marks the state, completion locks the
 * form, and an amendment is stored beside the original. No function here
 * performs I/O — the surface has no clinical write capability in this build.
 */
import type { Visit } from "../../fixtures/data";

export interface AuthoredVisitValues {
  complaint: string;
  hpi: string;
  past: string;
  surgical: string;
  family: string;
  allergyReviewed: boolean;
  temp: string;
  pulse: string;
  resp: string;
  sys: string;
  dia: string;
  height: string;
  weight: string;
  examArea: string;
  examFindings: string;
  assessment: string;
  plan: string;
  rationale: string;
  followup: string;
  diagnosisCode: string;
  diagnosisText: string;
  primary: boolean;
}

export type VitalKey =
  | "temp"
  | "pulse"
  | "resp"
  | "sys"
  | "dia"
  | "height"
  | "weight";

export const VITAL_RANGES: Readonly<
  Record<VitalKey, readonly [number, number]>
> = {
  temp: [30, 43],
  pulse: [25, 250],
  resp: [5, 60],
  sys: [50, 250],
  dia: [30, 160],
  height: [40, 250],
  weight: [2, 350],
};

export const VITAL_FIELD_KEYS: Readonly<Record<VitalKey, string>> = {
  temp: "field.temp",
  pulse: "field.pulse",
  resp: "field.resp",
  sys: "field.sys",
  dia: "field.dia",
  height: "field.height",
  weight: "field.weight",
};

/** Required note/finding fields, with the catalogue label of each. */
export const REQUIRED_FIELDS: ReadonlyArray<{
  readonly key: keyof AuthoredVisitValues;
  readonly labelKey: string;
}> = [
  { key: "complaint", labelKey: "clinical.complaint" },
  { key: "hpi", labelKey: "clinical.hpi" },
  { key: "examFindings", labelKey: "clinical.findings" },
  { key: "assessment", labelKey: "clinical.assessment" },
  { key: "plan", labelKey: "clinical.plan" },
  { key: "diagnosisText", labelKey: "field.diagnosisText" },
  { key: "followup", labelKey: "clinical.followup" },
];

const number = (value: number | undefined): string =>
  value === undefined ? "" : String(value);

export function initialVisitValues(
  visit: Visit,
  locale: "th" | "en",
): AuthoredVisitValues {
  const diagnosis = visit.diagnoses[0];
  return {
    complaint: visit.history.complaint ?? "",
    hpi: visit.history.hpi ?? "",
    past: visit.history.past ?? "",
    surgical: visit.history.surgical ?? "",
    family: visit.history.family ?? "",
    allergyReviewed: visit.history.reviewedAllergies,
    temp: number(visit.exam.vitals?.temp),
    pulse: number(visit.exam.vitals?.pulse),
    resp: number(visit.exam.vitals?.resp),
    sys: number(visit.exam.vitals?.sys),
    dia: number(visit.exam.vitals?.dia),
    height: number(visit.exam.vitals?.height),
    weight: number(visit.exam.vitals?.weight),
    examArea: visit.exam.site ?? visit.exam.area ?? "",
    examFindings: visit.exam.findings ?? "",
    assessment: visit.note.assessment ?? "",
    plan: visit.note.plan ?? "",
    rationale: visit.note.rationale ?? "",
    followup: visit.note.followup ?? "",
    diagnosisCode: diagnosis?.code ?? "",
    diagnosisText: diagnosis ? diagnosis.label[locale] : "",
    primary: diagnosis?.primary ?? true,
  };
}

export function missingRequiredFields(
  values: AuthoredVisitValues,
): readonly string[] {
  return REQUIRED_FIELDS.filter(
    (field) =>
      typeof values[field.key] === "string" &&
      (values[field.key] as string).trim().length === 0,
  ).map((field) => field.labelKey);
}

export function outOfRangeVitals(
  values: AuthoredVisitValues,
): readonly VitalKey[] {
  return (Object.keys(VITAL_RANGES) as VitalKey[]).filter((key) => {
    const raw = values[key];
    if (raw.trim() === "") return false;
    const parsed = Number(raw);
    if (!Number.isFinite(parsed)) return true;
    const [min, max] = VITAL_RANGES[key];
    return parsed < min || parsed > max;
  });
}

export type Readiness = "missing" | "ready" | "locked";

export function readinessOf(
  values: AuthoredVisitValues,
  phase: "draft" | "completed",
): { state: Readiness; missing: readonly string[] } {
  if (phase === "completed") return { state: "locked", missing: [] };
  const missing = missingRequiredFields(values);
  return { state: missing.length === 0 ? "ready" : "missing", missing };
}

export type CompletionCheck =
  | { readonly ok: true }
  | {
      readonly ok: false;
      readonly reason: "required" | "allergy" | "vitals";
      readonly detail?: VitalKey;
    };

/** Prototype completion gates: required text, allergy review, vital ranges. */
export function checkCompletion(values: AuthoredVisitValues): CompletionCheck {
  if (missingRequiredFields(values).length > 0)
    return { ok: false, reason: "required" };
  if (!values.allergyReviewed) return { ok: false, reason: "allergy" };
  const outOfRange = outOfRangeVitals(values);
  if (outOfRange.length > 0)
    return { ok: false, reason: "vitals", detail: outOfRange[0] };
  return { ok: true };
}
