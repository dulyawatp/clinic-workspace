import access from "@clinic/contracts/fixtures/access.json" with {
  type: "json",
};
import { type ZodType, z } from "zod";
import { isRoleCode, type RoleCode } from "../capabilities.js";
import { PHOTO_ASSETS } from "./photo-assets.js";

/**
 * `fixture` means the rows come from `@clinic/contracts/fixtures` only.
 * `verified`/`unverified` exist for later plans; Plan 02 has no verified
 * read contract and never calls a live backend.
 */
export type ReadContract = "fixture" | "verified" | "unverified";

/** ROLE-MATRIX is DECISION_REQUIRED: every matrix in this plan is unverified. */
export type ReadMatrix = "unverified";

export interface ReadResource {
  /** Enforced at the proxy only; the client validates the envelope alone. */
  schema: ZodType;
  contract: ReadContract;
  matrix: ReadMatrix;
  /** Fixture-only, unverified presentation filter from `access.json`. */
  roles?: RoleCode[];
  /** Static key into the fixture map; never a filesystem path. */
  fixtureFile?: string;
  /** Public DTO fields a client may filter on; empty means no filter support. */
  filterFields: readonly string[];
  /** Public DTO fields searched by `q`; empty means `q` is unsupported. */
  searchFields: readonly string[];
}

function fixtureRoles(resource: keyof typeof access.resources): RoleCode[] {
  return access.resources[resource].filter(isRoleCode);
}

/**
 * Single fixture-only role source for every read surface, including the photo
 * bytes route. Never an authorization contract; `matrix: "unverified"`.
 */
export function fixtureResourceRoles(
  resource: keyof typeof access.resources,
): RoleCode[] {
  return fixtureRoles(resource);
}

/**
 * Filter/search surfaces are derived from the *public* DTO shape, so a hidden
 * fixture field (e.g. `internal_notes`) can never be named by a client.
 */
function publicFields(shape: z.ZodRawShape): string[] {
  return Object.keys(shape);
}

const patientSchema = z.object({
  id: z.string(),
  hn: z.string(),
  fullName: z.string(),
  phone: z.string(),
  lastVisit: z.string(),
  fictional: z.literal(true),
});

const appointmentSchema = z.object({
  id: z.string(),
  patientId: z.string(),
  patientName: z.string(),
  scheduledAt: z.string(),
  status: z.string(),
  clinician: z.string(),
  fictional: z.literal(true),
});

const encounterSchema = z.object({
  id: z.string(),
  patientId: z.string(),
  patientName: z.string(),
  occurredAt: z.string(),
  type: z.string(),
  clinician: z.string(),
  fictional: z.literal(true),
});

const staffSchema = z.object({
  id: z.string(),
  displayName: z.string(),
  role: z.string(),
  fictional: z.literal(true),
});

const workItemSchema = z.object({
  id: z.string(),
  kind: z.string(),
  labelKey: z.string(),
  count: z.number(),
  routeKey: z.string().optional(),
  requiredCapability: z.string().optional(),
});

const profileSchema = z.object({
  company: z.string(),
  fictional: z.literal(true),
});

const frontdeskPatientSchema = z.object({
  id: z.string(),
  fullName: z.string(),
  mobileMasked: z.string(),
  dob: z.string().optional(),
  lastVisitAt: z.string().optional(),
  latestPractitioner: z.string().optional(),
  duplicateCandidate: z.boolean().optional(),
  fictional: z.literal(true),
});
const billingPatientSchema = z.object({
  id: z.string(),
  fullName: z.string(),
  fictional: z.literal(true),
});

const frontdeskAppointmentSchema = z.object({
  id: z.string(),
  patientId: z.string(),
  patientName: z.string(),
  practitionerId: z.string(),
  practitionerName: z.string(),
  startsAt: z.string(),
  date: z.string(),
  type: z.string(),
  status: z.string(),
  checkedInAt: z.string().optional(),
  checkInEligible: z.boolean().optional(),
  fictional: z.literal(true),
});

const availabilitySchema = z.object({
  id: z.string(),
  date: z.string(),
  time: z.string(),
  practitionerId: z.string(),
  practitionerName: z.string(),
  state: z.enum(["available", "booked", "blocked", "leave", "break"]),
  appointmentId: z.string().optional(),
  appointmentType: z.string().optional(),
  fictional: z.literal(true),
});

const availabilityOptionsSchema = z.object({
  practitioners: z.array(z.object({ id: z.string(), name: z.string() })),
  types: z.array(z.string()),
});

const frontdeskQueueSchema = z.object({
  id: z.string(),
  appointmentId: z.string(),
  patientId: z.string(),
  patientName: z.string(),
  practitionerName: z.string(),
  type: z.string(),
  date: z.string(),
  startsAt: z.string(),
  status: z.string(),
  checkInEligible: z.boolean(),
  checkedInAt: z.string().optional(),
  arrivalAt: z.string().optional(),
  waitMinutes: z.number().nonnegative().optional(),
  fictional: z.literal(true),
});

const queueKpisSchema = z.object({
  id: z.string(),
  date: z.string(),
  asOf: z.string(),
  appointments: z.number().nonnegative(),
  arrivals: z.number().nonnegative(),
  waiting: z.number().nonnegative(),
  inConsultation: z.number().nonnegative(),
  fictional: z.literal(true),
});

const receptionKpiSchema = z.object({
  id: z.string(),
  key: z.string(),
  labelKey: z.string(),
  count: z.number(),
  scope: z.string(),
  date: z.string(),
  fictional: z.literal(true),
});

const frontdeskCheckoutSchema = z.object({
  id: z.string(),
  patientId: z.string(),
  patientName: z.string(),
  visitId: z.string(),
  visitStatus: z.string(),
  handoff: z.string(),
  invoiceReference: z.string().optional(),
  outstanding: z.number().nonnegative().optional(),
  currency: z.string().optional(),
  fictional: z.literal(true),
});

/** A missing measurement stays `null`; the proxy never coerces it to zero. */
const frontdeskVitalsSchema = z.object({
  id: z.string(),
  patientId: z.string(),
  measuredAt: z.string(),
  source: z.string(),
  recordedBy: z.string(),
  temp: z.number().nullable(),
  pulse: z.number().nullable(),
  systolic: z.number().nullable(),
  diastolic: z.number().nullable(),
  respiratory: z.number().nullable(),
  height: z.number().nullable(),
  weight: z.number().nullable(),
  fictional: z.literal(true),
});

const frontdeskTimelineSchema = z.object({
  id: z.string(),
  patientId: z.string(),
  kind: z.string(),
  at: z.string(),
  reference: z.string(),
  fictional: z.literal(true),
});

const frontdeskPaymentSchema = z.object({
  id: z.string(),
  patientId: z.string(),
  issuedAt: z.string(),
  status: z.string(),
  total: z.number(),
  currency: z.string(),
  fictional: z.literal(true),
});

const clinicalBase = {
  encounterId: z.string(),
  patientId: z.string(),
  fictional: z.literal(true),
};

const PHOTO_URL_PREFIX = "/api/files/";

/**
 * A fixture photo row is only valid when it names one manifest asset and
 * repeats that asset's identity and transport metadata exactly. Syntactically
 * valid values are not enough: an unknown id, a swapped url or a well-formed
 * but wrong hash/size must fail the read closed rather than reach a client.
 */
function matchesPhotoAsset(row: {
  id: string;
  patientId: string;
  category: string;
  bodyArea: string;
  viewpoint: string | null;
  url: string;
  contentType: string;
  bytes: number;
  sha256: string;
  width: number;
  height: number;
}): boolean {
  if (!row.url.startsWith(PHOTO_URL_PREFIX)) return false;
  const asset = PHOTO_ASSETS.get(row.url.slice(PHOTO_URL_PREFIX.length));
  return (
    asset !== undefined &&
    asset.photoId === row.id &&
    asset.patientId === row.patientId &&
    asset.category === row.category &&
    asset.bodyArea === row.bodyArea &&
    asset.viewpoint === row.viewpoint &&
    asset.contentType === row.contentType &&
    asset.bytes === row.bytes &&
    asset.sha256 === row.sha256 &&
    asset.width === row.width &&
    asset.height === row.height
  );
}

const clinicalSchemas = {
  "clinical-visits": z.object({
    ...clinicalBase,
    patientName: z.string(),
    appointmentId: z.string().optional(),
    practitionerName: z.string(),
    startedAt: z.string(),
    state: z.string(),
  }),
  "clinical-sections": z.object({
    ...clinicalBase,
    section: z.string(),
    chiefComplaint: z.string().optional(),
    hpi: z.string().optional(),
    past: z.string().optional(),
    allergies: z.string().optional(),
    findings: z.string().optional(),
    state: z.string().optional(),
    savedAt: z.string().nullable().optional(),
    assessment: z.string().optional(),
    plan: z.string().optional(),
    missing: z.array(z.string()).optional(),
  }),
  "clinical-vitals": z.object({
    ...clinicalBase,
    id: z.string(),
    kind: z.string(),
    value: z.number().nullable(),
    unit: z.string(),
    measuredAt: z.string(),
    source: z.string(),
  }),
  "clinical-diagnoses": z.object({
    ...clinicalBase,
    id: z.string(),
    code: z.string(),
    description: z.string(),
  }),
  "clinical-medications": z.object({
    ...clinicalBase,
    id: z.string(),
    name: z.string(),
    state: z.string(),
  }),
  "clinical-photos": z
    .object({
      ...clinicalBase,
      id: z.string(),
      category: z.string(),
      bodyArea: z.string(),
      viewpoint: z.string().min(1).nullable(),
      takenAt: z.string(),
      consentStatus: z.string(),
      url: z.string().regex(/^\/api\/files\/[A-Za-z0-9_-]+$/),
      version: z.number(),
      // Transport metadata for the fixture bytes: the client may reference the
      // original hash and intrinsic size, but never an upstream or public URL.
      contentType: z.literal("image/png"),
      bytes: z.number().int().positive(),
      sha256: z.string().regex(/^[0-9a-f]{64}$/),
      width: z.number().int().positive(),
      height: z.number().int().positive(),
    })
    .superRefine((row, ctx) => {
      if (!matchesPhotoAsset(row)) {
        ctx.addIssue({ code: "custom", message: "PHOTO_ASSET_MISMATCH" });
      }
    }),
  "clinical-labs": z.object({
    ...clinicalBase,
    id: z.string(),
    kind: z.string(),
    state: z.string(),
    reviewer: z.string().nullable(),
    files: z.array(z.string()),
  }),
  "nurse-tasks": z.object({
    ...clinicalBase,
    id: z.string(),
    patientName: z.string(),
    kind: z.string(),
    state: z.string(),
  }),
  "clinical-procedures": z.object({
    ...clinicalBase,
    id: z.string(),
    name: z.string(),
    state: z.string(),
    lines: z.array(
      z.object({
        item: z.string(),
        qty: z.number(),
        uom: z.string(),
        batch: z.string(),
        expiry: z.string(),
        warehouse: z.string(),
      }),
    ),
  }),
  "my-day-counts": z.object({
    fictional: z.literal(true),
    myAppointmentsToday: z.number(),
    openDrafts: z.number(),
    labsAwaitingReview: z.number(),
    followUps: z.number(),
  }),
  "clinical-next-patients": z.object({
    ...clinicalBase,
    patientName: z.string(),
    appointmentId: z.string(),
    draft: z.boolean(),
  }),
};

/**
 * The complete read allow-list. An unlisted resource is a 404 before any
 * fixture is touched. Downstream plans append entries with the same shape.
 */
export const READ_RESOURCES: Record<string, ReadResource> = {
  ...Object.fromEntries(
    Object.entries(clinicalSchemas).map(([name, schema]) => [
      name,
      {
        schema,
        contract: "fixture" as const,
        matrix: "unverified" as const,
        roles: fixtureRoles(name as keyof typeof clinicalSchemas),
        fixtureFile: `${name}.json`,
        filterFields:
          name === "my-day-counts"
            ? []
            : name === "clinical-visits"
              ? ["encounterId", "patientId"]
              : ["id", "encounterId", "patientId"].filter(
                  (field) => field in schema.shape,
                ),
        searchFields: [],
      },
    ]),
  ),
  patients: {
    schema: patientSchema,
    contract: "fixture",
    matrix: "unverified",
    roles: fixtureRoles("patients"),
    fixtureFile: "patients.json",
    filterFields: publicFields(patientSchema.shape),
    searchFields: ["fullName", "hn", "phone"],
  },
  appointments: {
    schema: appointmentSchema,
    contract: "fixture",
    matrix: "unverified",
    roles: fixtureRoles("appointments"),
    fixtureFile: "appointments.json",
    filterFields: publicFields(appointmentSchema.shape),
    searchFields: [],
  },
  encounters: {
    schema: encounterSchema,
    contract: "fixture",
    matrix: "unverified",
    roles: fixtureRoles("encounters"),
    fixtureFile: "encounters.json",
    filterFields: publicFields(encounterSchema.shape),
    searchFields: [],
  },
  staff: {
    schema: staffSchema,
    contract: "fixture",
    matrix: "unverified",
    roles: fixtureRoles("staff"),
    fixtureFile: "staff.json",
    filterFields: publicFields(staffSchema.shape),
    searchFields: [],
  },
  "work-items": {
    schema: workItemSchema,
    contract: "fixture",
    matrix: "unverified",
    roles: fixtureRoles("work-items"),
    fixtureFile: "work-items.json",
    filterFields: publicFields(workItemSchema.shape),
    searchFields: [],
  },
  profile: {
    schema: profileSchema,
    contract: "fixture",
    matrix: "unverified",
    roles: fixtureRoles("profile"),
    fixtureFile: "profile.json",
    filterFields: publicFields(profileSchema.shape),
    searchFields: [],
  },
  "frontdesk-patients": {
    schema: frontdeskPatientSchema,
    contract: "fixture",
    matrix: "unverified",
    roles: fixtureRoles("frontdesk-patients"),
    fixtureFile: "frontdesk-patients.json",
    filterFields: publicFields(frontdeskPatientSchema.shape),
    searchFields: ["fullName", "id", "mobileMasked"],
  },
  "billing-patient-summary": {
    schema: billingPatientSchema,
    contract: "fixture",
    matrix: "unverified",
    roles: fixtureRoles("billing-patient-summary"),
    fixtureFile: "billing-patient-summary.json",
    filterFields: ["id"],
    searchFields: [],
  },
  "frontdesk-appointments": {
    schema: frontdeskAppointmentSchema,
    contract: "fixture",
    matrix: "unverified",
    roles: fixtureRoles("frontdesk-appointments"),
    fixtureFile: "frontdesk-appointments.json",
    filterFields: [...publicFields(frontdeskAppointmentSchema.shape), "scope"],
    searchFields: [],
  },
  "frontdesk-availability": {
    schema: availabilitySchema,
    contract: "fixture",
    matrix: "unverified",
    roles: fixtureRoles("frontdesk-availability"),
    fixtureFile: "frontdesk-availability.json",
    filterFields: [
      ...publicFields(availabilitySchema.shape),
      "dateFrom",
      "dateTo",
    ],
    searchFields: [],
  },
  "frontdesk-availability-options": {
    schema: availabilityOptionsSchema,
    contract: "fixture",
    matrix: "unverified",
    roles: fixtureRoles("frontdesk-availability-options"),
    fixtureFile: "frontdesk-availability-options.json",
    filterFields: [],
    searchFields: [],
  },
  "frontdesk-queue": {
    schema: frontdeskQueueSchema,
    contract: "fixture",
    matrix: "unverified",
    roles: fixtureRoles("frontdesk-queue"),
    fixtureFile: "frontdesk-queue.json",
    filterFields: ["patientId", "date", "status"],
    searchFields: ["patientName", "patientId"],
  },
  "frontdesk-queue-kpis": {
    schema: queueKpisSchema,
    contract: "fixture",
    matrix: "unverified",
    roles: fixtureRoles("frontdesk-queue-kpis"),
    fixtureFile: "frontdesk-queue-kpis.json",
    filterFields: [],
    searchFields: [],
  },
  "reception-kpis": {
    schema: receptionKpiSchema,
    contract: "fixture",
    matrix: "unverified",
    roles: fixtureRoles("reception-kpis"),
    fixtureFile: "reception-kpis.json",
    filterFields: publicFields(receptionKpiSchema.shape),
    searchFields: [],
  },
  "frontdesk-checkouts": {
    schema: frontdeskCheckoutSchema,
    contract: "fixture",
    matrix: "unverified",
    roles: fixtureRoles("frontdesk-checkouts"),
    fixtureFile: "frontdesk-checkouts.json",
    filterFields: ["patientId", "handoff"],
    searchFields: [],
  },
  "frontdesk-vitals": {
    schema: frontdeskVitalsSchema,
    contract: "fixture",
    matrix: "unverified",
    roles: fixtureRoles("frontdesk-vitals"),
    fixtureFile: "frontdesk-vitals.json",
    filterFields: publicFields(frontdeskVitalsSchema.shape),
    searchFields: [],
  },
  "frontdesk-timeline": {
    schema: frontdeskTimelineSchema,
    contract: "fixture",
    matrix: "unverified",
    roles: fixtureRoles("frontdesk-timeline"),
    fixtureFile: "frontdesk-timeline.json",
    filterFields: publicFields(frontdeskTimelineSchema.shape),
    searchFields: [],
  },
  "frontdesk-payments": {
    schema: frontdeskPaymentSchema,
    contract: "fixture",
    matrix: "unverified",
    roles: fixtureRoles("frontdesk-payments"),
    fixtureFile: "frontdesk-payments.json",
    filterFields: publicFields(frontdeskPaymentSchema.shape),
    searchFields: [],
  },
};
