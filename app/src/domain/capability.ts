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

export type Capability =
  | "write.patient.create"
  | "write.patient.update"
  | "write.appointment.book"
  | "write.appointment.reschedule"
  | "write.appointment.cancel"
  | "write.appointment.checkin"
  | "write.encounter.draft"
  | "write.encounter.complete"
  | "write.medication.order"
  | "write.procedure.complete"
  | "write.lab.order"
  | "write.lab.review"
  | "write.photo.consent"
  | "write.photo.upload"
  | "write.photo.link"
  | "write.photo.annotate"
  | "write.stock.receive"
  | "write.stock.transfer"
  | "write.stock.adjust"
  | "write.purchase_order.draft"
  | "write.purchase_order.submit"
  | "write.invoice.submit"
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

export type CapabilityMap = Partial<Record<Capability, CapabilityState>>;

// JSON owns names and order; the closed union is checked against it in tests.
export const CAPABILITY_NAMES: readonly Capability[] =
  contractNames.filter(isCapability);

export function isCapability(value: unknown): value is Capability {
  return typeof value === "string" && contractNames.includes(value);
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
