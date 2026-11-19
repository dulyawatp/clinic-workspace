import contractNames from "@clinic/contracts/capabilities.json" with {
  type: "json",
};

export type RoleCode = "REC" | "DOC" | "NUR" | "PHA" | "CAS" | "MGR" | "ADM";

const ROLE_CODES: ReadonlySet<string> = new Set([
  "REC",
  "DOC",
  "NUR",
  "PHA",
  "CAS",
  "MGR",
  "ADM",
]);

/** Narrows untrusted data (e.g. JSON fixtures) to a `RoleCode`. */
export function isRoleCode(value: unknown): value is RoleCode {
  return typeof value === "string" && ROLE_CODES.has(value);
}

/**
 * Closed capability union, kept in the same order as
 * `contracts/capabilities.json`. The shared contract is compared against
 * `CAPABILITY_NAMES` in `capabilities.test.ts`; there is no open string index.
 */
export type Capability =
  | "write.patient.create"
  | "write.patient.update"
  | "write.appointment.book"
  | "write.appointment.reschedule"
  | "write.appointment.cancel"
  | "write.appointment.no_show"
  | "write.appointment.checkin"
  | "write.encounter.draft"
  | "write.encounter.complete"
  | "write.encounter.amend"
  | "write.medication.order"
  | "write.procedure.complete"
  | "write.procedure.create"
  | "write.lab.order"
  | "write.lab.review"
  | "write.photo.consent"
  | "write.photo.upload"
  | "write.photo.link"
  | "write.photo.annotate"
  | "write.stock.receive"
  | "write.stock.transfer"
  | "write.stock.adjust"
  | "write.stock.consume"
  | "write.purchase_order.draft"
  | "write.purchase_order.submit"
  | "write.invoice.submit"
  | "write.invoice.void_amend"
  | "write.visit_invoice.submit"
  | "write.visit_invoice.discount"
  | "write.payment.receive"
  | "write.payment.refund"
  | "write.admin.user"
  | "write.admin.master"
  | "read.patient"
  | "read.appointment"
  | "read.encounter"
  | "read.lab"
  | "read.photo"
  | "read.inventory"
  | "read.billing"
  | "read.report"
  | "read.admin";

export type CapabilityState =
  | "enabled"
  | "read_only"
  | "blocked_unverified"
  | "denied"
  | "error_upstream";

/** Sparse by design: a missing capability is never treated as enabled. */
export type CapabilityMap = Partial<Record<Capability, CapabilityState>>;

export const CAPABILITY_NAMES = [
  "write.patient.create",
  "write.patient.update",
  "write.appointment.book",
  "write.appointment.reschedule",
  "write.appointment.cancel",
  "write.appointment.no_show",
  "write.appointment.checkin",
  "write.encounter.draft",
  "write.encounter.complete",
  "write.encounter.amend",
  "write.medication.order",
  "write.procedure.complete",
  "write.procedure.create",
  "write.lab.order",
  "write.lab.review",
  "write.photo.consent",
  "write.photo.upload",
  "write.photo.link",
  "write.photo.annotate",
  "write.stock.receive",
  "write.stock.transfer",
  "write.stock.adjust",
  "write.stock.consume",
  "write.purchase_order.draft",
  "write.purchase_order.submit",
  "write.invoice.submit",
  "write.invoice.void_amend",
  "write.visit_invoice.submit",
  "write.visit_invoice.discount",
  "write.payment.receive",
  "write.payment.refund",
  "write.admin.user",
  "write.admin.master",
  "read.patient",
  "read.appointment",
  "read.encounter",
  "read.lab",
  "read.photo",
  "read.inventory",
  "read.billing",
  "read.report",
  "read.admin",
] as const satisfies readonly Capability[];

export function isCapability(value: unknown): value is Capability {
  return (
    typeof value === "string" &&
    (CAPABILITY_NAMES as readonly string[]).includes(value)
  );
}

export function getCapability(
  map: CapabilityMap,
  capability: Capability,
): CapabilityState {
  if (
    !isCapability(capability) ||
    typeof map !== "object" ||
    map === null ||
    Array.isArray(map) ||
    !Object.hasOwn(map, capability)
  )
    return "read_only";

  const state: unknown = map[capability];
  switch (state) {
    case "enabled":
    case "read_only":
    case "blocked_unverified":
    case "denied":
    case "error_upstream":
      return state;
    default:
      return "read_only";
  }
}

export interface CapabilityOptions {
  /**
   * Enabled only for an explicitly configured live lab session whose native
   * roles have been validated *and* whose `CLINIC_LIVE_PHOTO_WRITES` opt-in is
   * on. Fixture sessions and default (browse-only) live labs never pass this.
   */
  livePhotoUpload?: boolean;
  /**
   * Enabled only for an explicitly configured live lab session whose native
   * roles validated as reception *and* whose `CLINIC_LIVE_PATIENT_WRITES`
   * opt-in is on. Fixture sessions and default live labs never pass this.
   */
  livePatientCreate?: boolean;
  /**
   * Enabled only for an explicitly configured live lab session whose native
   * roles validated as clinician *and* whose `CLINIC_LIVE_ENCOUNTER_WRITES`
   * opt-in is on. Fixture sessions and default live labs never pass this.
   */
  liveEncounterDraft?: boolean;
}

/**
 * Active flags alone never prove contract verification. No verified manifest
 * exists in this foundation: every capability remains read_only, including
 * requested active operations, except the explicitly configured live lab photo
 * upload and patient create capabilities above.
 */
export function buildCapabilityMap(
  _active: ReadonlySet<string>,
  options: CapabilityOptions = {},
): CapabilityMap {
  const map: CapabilityMap = {};
  for (const name of contractNames) {
    if (isCapability(name)) map[name] = "read_only";
  }
  if (options.livePhotoUpload === true) {
    map["write.photo.upload"] = "enabled";
  }
  if (options.livePatientCreate === true) {
    map["write.patient.create"] = "enabled";
  }
  if (options.liveEncounterDraft === true) {
    map["write.encounter.draft"] = "enabled";
  }
  return map;
}
