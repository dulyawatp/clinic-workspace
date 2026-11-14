import access from "@clinic/contracts/fixtures/access.json" with {
  type: "json",
};
import accounts from "@clinic/contracts/fixtures/accounts.json" with {
  type: "json",
};
import appointments from "@clinic/contracts/fixtures/appointments.json" with {
  type: "json",
};
import clinical from "@clinic/contracts/fixtures/clinical.json" with {
  type: "json",
};
import encounters from "@clinic/contracts/fixtures/encounters.json" with {
  type: "json",
};
import frontdesk from "@clinic/contracts/fixtures/frontdesk.json" with {
  type: "json",
};
import patients from "@clinic/contracts/fixtures/patients.json" with {
  type: "json",
};
import profile from "@clinic/contracts/fixtures/profile.json" with {
  type: "json",
};
import staff from "@clinic/contracts/fixtures/staff.json" with { type: "json" };
import workItems from "@clinic/contracts/fixtures/work-items.json" with {
  type: "json",
};
import { HttpResponse, http, type RequestHandler } from "msw";
import { z } from "zod";
import {
  photoAsset,
  photoAssetBytes,
} from "../../../../proxy/src/read/photo-assets";
import { READ_RESOURCES as PROXY_READ_RESOURCES } from "../../../../proxy/src/read/registry";
import {
  type CapabilityMap,
  isRoleCode,
  type RoleCode,
} from "../../domain/capability";
import type { PatientSummary } from "../../domain/patient";
import type { SessionDTO } from "../../domain/session";
import {
  isReceptionScope,
  SCOPE_STATUSES,
} from "../../features/reception/scopes";
import type { Locale } from "../../i18n";

export const FIXTURE_CSRF_TOKEN = "fixture-csrf";

const CSRF_HEADER = "x-clinic-csrf";
const DEFAULT_PAGE_SIZE = 20;
const MAX_PAGE_SIZE = 100;

/**
 * Every override key referenced by Plans 02–05. Handlers ignore keys they do
 * not own; the plan that introduces a key documents its behavior and must add
 * it here.
 */
export interface FixtureOverrides {
  workItems?: "empty" | "error";
  profileCompany?: string | null;
  searchRows?: number;
  // Plans 03–05 (declared now; implemented by the owning plan's handler task)
  duplicates?: boolean;
  patient?: "withVitals" | "noVitals" | "duplicate";
  rows?: unknown;
  status?: string;
  visit?: string;
  withLeave?: boolean;
  availability?: unknown[];
  encounterId?: string;
  missing?: boolean | string[];
  weight?: { value: number; unit: string } | null;
  allergies?: "unknown" | "known" | "none";
  state?: string;
  savedAt?: number | null;
  diagnoses?: unknown[];
  photo?: Record<string, unknown>;
  files?: string[];
  expired?: boolean;
  reviewer?: string | null;
  consent?: "missing" | "recorded";
  counts?: Record<string, number>;
  draft?: boolean;
  ordered?: number;
  received?: number;
  lowStock?: boolean;
  lines?: number[];
  serverGrandTotal?: number;
  valuation?: "unverified" | "verified";
  serviceMapping?: string;
  statuses?: string[];
  reorder?: unknown;
  appointments?: unknown[];
  completed?: boolean;
  daysToExpiry?: number;
  posted?: boolean;
  readiness?: string;
  [extensionKey: `x-${string}`]: unknown;
}

export type FixtureScenario = {
  sessionMode?: "live" | "fixture";
  role?: RoleCode;
  caps?: CapabilityMap;
  authed?: boolean;
  locale?: Locale;
  path?: string;
} & FixtureOverrides;

type ReadResource =
  | ClinicalResource
  | "patients"
  | "appointments"
  | "encounters"
  | "staff"
  | "work-items"
  | "profile"
  | "frontdesk-patients"
  | "billing-patient-summary"
  | "frontdesk-appointments"
  | "frontdesk-availability"
  | "frontdesk-availability-options"
  | "frontdesk-queue"
  | "frontdesk-queue-kpis"
  | "reception-kpis"
  | "frontdesk-checkouts"
  | "frontdesk-vitals"
  | "frontdesk-timeline"
  | "frontdesk-payments";

const CLINICAL_ROWS = {
  "clinical-visits": clinical.visits,
  "clinical-sections": clinical.sections,
  "clinical-vitals": clinical.vitals,
  "clinical-diagnoses": clinical.diagnoses,
  "clinical-medications": clinical.medications,
  "clinical-photos": clinical.photos,
  "clinical-labs": clinical.labs,
  "nurse-tasks": clinical.nurseTasks,
  "clinical-procedures": clinical.procedures,
  "my-day-counts": clinical.myDayCounts,
  "clinical-next-patients": clinical.nextPatients,
};
type ClinicalResource = keyof typeof CLINICAL_ROWS;

function isClinicalResource(value: string): value is ClinicalResource {
  return Object.hasOwn(CLINICAL_ROWS, value);
}

function clinicalRows(
  resource: ClinicalResource,
  scenario: FixtureScenario,
): unknown[] {
  if (resource === "clinical-visits" && scenario.missing === true) return [];
  const source =
    resource === "clinical-diagnoses" && scenario.diagnoses !== undefined
      ? scenario.diagnoses
      : CLINICAL_ROWS[resource];
  const selected =
    scenario.encounterId && resource !== "my-day-counts"
      ? source.filter(
          (row) =>
            typeof row === "object" &&
            row !== null &&
            "encounterId" in row &&
            row.encounterId === scenario.encounterId,
        )
      : source;
  return (selected as readonly Record<string, unknown>[]).map(
    (original, index) => {
      let row = { ...original };
      if (resource === "clinical-sections") {
        if (row.section === "history" && scenario.allergies !== undefined)
          row.allergies = scenario.allergies;
        if (row.section === "note" && scenario.state !== undefined)
          row.state = scenario.state;
        if (row.section === "note" && scenario.savedAt !== undefined)
          row.savedAt =
            scenario.savedAt === null
              ? null
              : new Date(scenario.savedAt).toISOString();
        if (row.section === "review" && Array.isArray(scenario.missing))
          row.missing = scenario.missing;
      }
      if (
        resource === "clinical-vitals" &&
        row.kind === "weight" &&
        scenario.weight !== undefined
      ) {
        row.value = scenario.weight?.value ?? null;
        if (scenario.weight) row.unit = scenario.weight.unit;
      }
      if (resource === "clinical-photos") {
        // The gallery override targets the leading card only; every row keeps
        // its manifest identity and transport metadata from the fixture.
        if (index === 0 && scenario.photo !== undefined) {
          row = {
            ...row,
            ...scenario.photo,
            id: original.id,
            encounterId: original.encounterId,
            patientId: original.patientId,
          };
        }
        if (scenario.consent !== undefined)
          row.consentStatus = scenario.consent;
      }
      if (resource === "clinical-labs") {
        if (scenario.state !== undefined) row.state = scenario.state;
        if (scenario.reviewer !== undefined) row.reviewer = scenario.reviewer;
        if (scenario.files !== undefined) row.files = scenario.files;
      }
      if (
        resource === "clinical-procedures" &&
        scenario.expired === true &&
        Array.isArray(row.lines)
      )
        row.lines = row.lines.map((line) => ({
          ...line,
          expiry: "2020-01-01",
        }));
      if (resource === "clinical-next-patients" && scenario.draft !== undefined)
        row.draft = scenario.draft;
      if (resource === "my-day-counts" && scenario.counts !== undefined)
        row = { ...row, ...scenario.counts };
      return row;
    },
  );
}

/** Front-desk surfaces served from `contracts/fixtures/frontdesk.json`. */
export const FRONTDESK_RESOURCES: ReadonlySet<string> = new Set([
  "frontdesk-patients",
  "billing-patient-summary",
  "frontdesk-appointments",
  "frontdesk-availability",
  "frontdesk-availability-options",
  "frontdesk-queue",
  "frontdesk-queue-kpis",
  "reception-kpis",
  "frontdesk-checkouts",
  "frontdesk-vitals",
  "frontdesk-timeline",
  "frontdesk-payments",
]);

type FrontDeskResource =
  | "frontdesk-patients"
  | "billing-patient-summary"
  | "frontdesk-appointments"
  | "frontdesk-availability"
  | "frontdesk-availability-options"
  | "frontdesk-queue"
  | "frontdesk-queue-kpis"
  | "reception-kpis"
  | "frontdesk-checkouts"
  | "frontdesk-vitals"
  | "frontdesk-timeline"
  | "frontdesk-payments";

/**
 * Filterable public fields per resource, mirroring the proxy registry. A key
 * outside this table is a client error, never a silently ignored filter.
 */
const FILTER_FIELDS: Readonly<Record<string, readonly string[]>> = {
  "clinical-visits": ["encounterId", "patientId"],
  "clinical-sections": ["encounterId", "patientId"],
  "clinical-vitals": ["id", "encounterId", "patientId"],
  "clinical-diagnoses": ["id", "encounterId", "patientId"],
  "clinical-medications": ["id", "encounterId", "patientId"],
  "clinical-photos": ["id", "encounterId", "patientId"],
  "clinical-labs": ["id", "encounterId", "patientId"],
  "nurse-tasks": ["id", "encounterId", "patientId"],
  "clinical-procedures": ["id", "encounterId", "patientId"],
  "clinical-next-patients": ["encounterId", "patientId"],
  "frontdesk-patients": ["id"],
  "billing-patient-summary": ["id"],
  "frontdesk-appointments": ["patientId", "id", "date", "scope"],
  "frontdesk-availability": [
    "date",
    "dateFrom",
    "dateTo",
    "practitionerId",
    "appointmentType",
  ],
  "frontdesk-queue": ["patientId", "date", "status"],
  "frontdesk-checkouts": ["patientId", "handoff"],
  "frontdesk-vitals": ["patientId"],
  "frontdesk-timeline": ["patientId", "kind"],
  "frontdesk-payments": ["patientId"],
  "reception-kpis": ["scope"],
};

const UNSAFE_FILTER_KEYS: ReadonlySet<string> = new Set([
  "__proto__",
  "constructor",
  "prototype",
]);

function isFrontDeskResource(value: string): value is FrontDeskResource {
  return FRONTDESK_RESOURCES.has(value);
}

interface PatientDTO {
  id: string;
  hn: string;
  fullName: string;
  phone: string;
  lastVisit: string;
  fictional: true;
}

interface WorkItemDTO {
  id: string;
  kind: string;
  labelKey: string;
  count: number;
  routeKey?: string;
  requiredCapability?: string;
}

function isReadResource(value: string): value is ReadResource {
  if (isClinicalResource(value)) return true;
  if (isFrontDeskResource(value)) return true;
  switch (value) {
    case "patients":
    case "appointments":
    case "encounters":
    case "staff":
    case "work-items":
    case "profile":
      return true;
    default:
      return false;
  }
}

/**
 * Fixture-only, unverified role filter. Never an authorization contract, and
 * `access.json` is the single role source for every read resource — the same
 * file the proxy reads, so the two surfaces cannot drift.
 */
function matrixRoles(resource: ReadResource): readonly RoleCode[] {
  const roles: unknown = Object.hasOwn(access.resources, resource)
    ? (access.resources as Record<string, unknown>)[resource]
    : [];
  return Array.isArray(roles) ? roles.filter(isRoleCode) : [];
}

function accountRole(account: (typeof accounts)[number]): RoleCode {
  for (const role of account.user.roles) {
    if (isRoleCode(role)) return role;
  }
  throw new Error("Fixture account has no known role code");
}

function sessionFor(
  role: RoleCode,
  caps: CapabilityMap,
  sessionEpoch: string,
): SessionDTO {
  const account = accounts.find((entry) => accountRole(entry) === role);
  if (account === undefined) {
    throw new Error(`No fixture account for role ${role}`);
  }
  return {
    sessionEpoch,
    user: {
      id: account.user.id,
      displayName: account.user.displayName,
      roles: [role],
    },
    capabilities: caps,
  };
}

/** Strips `internal_notes`: the client DTO never declares it. */
function toPatientDTO(patient: (typeof patients)[number]): PatientDTO {
  return {
    id: patient.id,
    hn: patient.hn,
    fullName: patient.fullName,
    phone: patient.phone,
    lastVisit: patient.lastVisit,
    fictional: true,
  };
}

interface DirectoryFixtureRow {
  id: string;
  fullName: string;
  mobileMasked: string;
  dob?: string;
  lastVisitAt?: string;
  latestPractitioner?: string;
  duplicateCandidate?: boolean;
}

/** Directory rows carry a masked mobile only; no identity number is copied. */
function toDirectoryDTO(row: DirectoryFixtureRow): PatientSummary {
  const dto: PatientSummary = {
    id: row.id,
    fullName: row.fullName,
    mobileMasked: row.mobileMasked,
    fictional: true,
  };
  if (row.dob !== undefined) dto.dob = row.dob;
  if (row.lastVisitAt !== undefined) dto.lastVisitAt = row.lastVisitAt;
  if (row.latestPractitioner !== undefined)
    dto.latestPractitioner = row.latestPractitioner;
  if (row.duplicateCandidate === true) dto.duplicateCandidate = true;
  return dto;
}

function toWorkItemDTO(item: (typeof workItems)[number]["items"][number]) {
  const dto: WorkItemDTO = {
    id: item.id,
    kind: item.kind,
    labelKey: item.labelKey,
    count: item.count,
  };
  if ("routeKey" in item) dto.routeKey = item.routeKey;
  if ("requiredCapability" in item) {
    dto.requiredCapability = item.requiredCapability;
  }
  return dto;
}

function workItemsFor(role: RoleCode): WorkItemDTO[] {
  const group = workItems.find((entry) => entry.role === role);
  return group === undefined ? [] : group.items.map(toWorkItemDTO);
}

function profileRows(company: string | null | undefined) {
  if (company === null) return [];
  return [{ company: company ?? profile.company, fictional: true }];
}

function pageParams(url: URL) {
  const rawLimit = Number(url.searchParams.get("limit") ?? DEFAULT_PAGE_SIZE);
  const rawOffset = Number(url.searchParams.get("offset") ?? 0);
  const limit = Number.isFinite(rawLimit)
    ? Math.min(Math.max(Math.trunc(rawLimit), 1), MAX_PAGE_SIZE)
    : DEFAULT_PAGE_SIZE;
  const offset =
    Number.isFinite(rawOffset) && rawOffset > 0 ? Math.trunc(rawOffset) : 0;
  return { limit, offset };
}

/**
 * Same filter contract as the proxy: `filters` is a JSON object of string
 * values, limited to the resource's declared public fields. Anything else is a
 * client error so a hidden fixture field can never be probed.
 */
function parseFilters(
  raw: string | null,
  allowed: readonly string[],
): Record<string, string> | null {
  if (raw === null || raw === "") return {};
  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch {
    return null;
  }
  if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed))
    return null;
  const filters: Record<string, string> = Object.create(null);
  for (const [key, value] of Object.entries(parsed)) {
    if (UNSAFE_FILTER_KEYS.has(key) || typeof value !== "string") return null;
    if (!allowed.includes(key)) return null;
    filters[key] = value;
  }
  return filters;
}

function matchesFilters(
  row: unknown,
  filters: ReadonlyArray<readonly [string, string]>,
): boolean {
  if (typeof row !== "object" || row === null || Array.isArray(row))
    return false;
  const record = row as Record<string, unknown>;
  return filters.every(([key, expected]) => {
    if (!Object.hasOwn(record, key)) return false;
    const value = record[key];
    return typeof value === "string" ||
      typeof value === "number" ||
      typeof value === "boolean"
      ? String(value) === expected
      : false;
  });
}

function availabilityOptions(source: readonly unknown[]) {
  const practitioners = new Map<string, string>();
  const types = new Set<string>();
  for (const row of source) {
    if (typeof row !== "object" || row === null || Array.isArray(row)) continue;
    const record = row as Record<string, unknown>;
    if (
      typeof record.practitionerId === "string" &&
      record.practitionerId &&
      typeof record.practitionerName === "string" &&
      record.practitionerName
    )
      practitioners.set(record.practitionerId, record.practitionerName);
    if (typeof record.appointmentType === "string" && record.appointmentType)
      types.add(record.appointmentType);
  }
  return {
    practitioners: [...practitioners]
      .map(([id, name]) => ({ id, name }))
      .sort((a, b) => a.name.localeCompare(b.name) || a.id.localeCompare(b.id)),
    types: [...types].sort(),
  };
}

/**
 * MSW handlers for the Plan 02 fixture surface. Nothing here reaches a network
 * or a real clinic: every response comes from `contracts/fixtures`, and the
 * role filter is the same `matrix: "unverified"` fixture-only table the proxy
 * uses. Later plans prepend their own handlers with `server.use(...)`.
 */
let fixtureEpoch = 0;

const FIXTURE_FILE_PREFIX = "/api/files/";

/**
 * Mirrors the proxy's wildcard parameter: everything after `/api/files/`,
 * decoded once. Multi-segment and malformed values stay values to look up in
 * the manifest, never filesystem paths.
 */
function fixtureFileId(pathname: string): string | null {
  if (!pathname.startsWith(FIXTURE_FILE_PREFIX)) return null;
  const raw = pathname.slice(FIXTURE_FILE_PREFIX.length);
  if (raw === "") return null;
  try {
    return decodeURIComponent(raw);
  } catch {
    return null;
  }
}

export function createHandlers(
  scenario: FixtureScenario = {},
): RequestHandler[] {
  const caps = scenario.caps ?? {};
  let authed = scenario.authed ?? true;
  let role: RoleCode = scenario.role ?? "REC";
  let sessionEpoch = `fixture-session-${++fixtureEpoch}`;

  const sessionResponse = () =>
    HttpResponse.json({
      ...sessionFor(role, caps, sessionEpoch),
      ...(scenario.sessionMode ? { mode: scenario.sessionMode } : {}),
    });

  return [
    http.get("/api/session", () =>
      authed
        ? sessionResponse()
        : HttpResponse.json({ code: "AUTH_EXPIRED" }, { status: 401 }),
    ),

    http.get("/api/auth/csrf", () =>
      HttpResponse.json({ token: FIXTURE_CSRF_TOKEN }),
    ),

    http.post("/api/auth/login", async ({ request }) => {
      if (request.headers.get(CSRF_HEADER) !== FIXTURE_CSRF_TOKEN) {
        return HttpResponse.json({ code: "FORBIDDEN" }, { status: 403 });
      }

      const body: unknown = await request.json().catch(() => null);
      if (typeof body !== "object" || body === null) {
        return HttpResponse.json({ code: "VALIDATION" }, { status: 400 });
      }
      const { usr, pwd } = body as Record<string, unknown>;
      const account = accounts.find(
        (entry) => entry.usr === usr && entry.pwd === pwd,
      );
      if (account === undefined) {
        return HttpResponse.json({ code: "FORBIDDEN" }, { status: 403 });
      }

      role = accountRole(account);
      sessionEpoch = `fixture-session-${++fixtureEpoch}`;
      authed = true;
      return sessionResponse();
    }),

    http.post("/api/auth/logout", ({ request }) => {
      // A missing session is a 204 no-op, exactly like the proxy.
      if (!authed) return new HttpResponse(null, { status: 204 });
      if (request.headers.get(CSRF_HEADER) !== FIXTURE_CSRF_TOKEN) {
        return HttpResponse.json({ code: "FORBIDDEN" }, { status: 403 });
      }
      authed = false;
      return new HttpResponse(null, { status: 204 });
    }),

    http.get("/api/read/:resource", ({ request, params }) => {
      if (!authed) {
        return HttpResponse.json({ code: "AUTH_EXPIRED" }, { status: 401 });
      }

      const resource = String(params.resource);
      if (!isReadResource(resource)) {
        return HttpResponse.json({ code: "NOT_FOUND" }, { status: 404 });
      }
      if (!matrixRoles(resource).includes(role)) {
        return HttpResponse.json({ code: "FORBIDDEN" }, { status: 403 });
      }
      if (resource === "work-items" && scenario.workItems === "error") {
        return HttpResponse.error();
      }

      const url = new URL(request.url);
      // Same contract as the proxy: `q` is trimmed, and a resource without
      // declared search fields rejects a non-empty search instead of ignoring it.
      const search = (url.searchParams.get("q") ?? "").trim();
      const searchable =
        resource === "patients" ||
        resource === "frontdesk-patients" ||
        resource === "frontdesk-queue";
      if (search !== "" && !searchable) {
        return HttpResponse.json({ code: "VALIDATION" }, { status: 400 });
      }

      let rows: unknown[];
      if (isClinicalResource(resource)) {
        rows = clinicalRows(resource, scenario);
      } else {
        switch (resource) {
          case "patients": {
            const query = search.toLowerCase();
            const matched = patients
              .map(toPatientDTO)
              .filter(
                (patient) =>
                  query === "" ||
                  patient.fullName.toLowerCase().includes(query) ||
                  patient.hn.toLowerCase().includes(query) ||
                  patient.phone.toLowerCase().includes(query),
              );
            rows =
              scenario.searchRows === undefined
                ? matched
                : matched.slice(0, scenario.searchRows);
            break;
          }
          case "appointments":
            rows = appointments;
            break;
          case "encounters":
            rows = encounters;
            break;
          case "staff":
            rows = staff;
            break;
          case "work-items":
            rows = scenario.workItems === "empty" ? [] : workItemsFor(role);
            break;
          case "profile":
            rows = profileRows(scenario.profileCompany);
            break;
          case "frontdesk-patients": {
            const query = search.toLowerCase();
            const source = scenario.duplicates
              ? frontdesk.duplicates
              : frontdesk.patients;
            const matched = source
              .map(toDirectoryDTO)
              .filter(
                (patient) =>
                  query === "" ||
                  patient.fullName.toLowerCase().includes(query) ||
                  patient.id.toLowerCase().includes(query) ||
                  patient.mobileMasked.toLowerCase().includes(query),
              );
            rows =
              typeof scenario.rows === "number"
                ? matched.slice(0, Math.max(0, scenario.rows))
                : matched;
            break;
          }
          case "billing-patient-summary":
            rows = frontdesk.patients.map((row) => ({
              id: row.id,
              fullName: row.fullName,
              fictional: true,
            }));
            break;
          case "frontdesk-appointments": {
            const source = Array.isArray(scenario.appointments)
              ? scenario.appointments
              : frontdesk.appointments;
            rows =
              scenario.status === undefined
                ? source
                : source.map((row) => ({
                    ...(row as object),
                    status: scenario.status,
                  }));
            break;
          }
          case "frontdesk-availability":
            rows =
              scenario.availability ??
              (scenario.withLeave === false
                ? frontdesk.availability.filter((row) => row.state !== "leave")
                : frontdesk.availability);
            break;
          case "frontdesk-availability-options":
            rows = [
              availabilityOptions(
                scenario.availability ?? frontdesk.availability,
              ),
            ];
            break;
          case "frontdesk-queue":
            rows = frontdesk.queue.filter(
              (row) =>
                search === "" ||
                row.patientName.toLowerCase().includes(search.toLowerCase()) ||
                row.patientId.toLowerCase().includes(search.toLowerCase()),
            );
            if (
              Array.isArray(scenario.rows) &&
              scenario.rows.includes("checkedIn")
            )
              rows = frontdesk.queue
                .filter((row) => "checkedInAt" in row)
                .slice(0, 1)
                .map((row) => ({ ...row, checkInEligible: true }));
            if (Array.isArray(scenario.rows) && scenario.rows.includes("weird"))
              rows = [
                {
                  ...frontdesk.queue[0],
                  status: "marley_unknown_state",
                  checkInEligible: false,
                },
              ];
            if (
              Array.isArray(scenario.rows) &&
              scenario.rows.includes("otherDay")
            )
              rows = [
                ...rows,
                {
                  ...frontdesk.queue[0],
                  id: "other-day",
                  date: "2026-11-06",
                  patientName: "Other Day Patient",
                },
              ];
            if (scenario.status !== undefined)
              rows = rows.map((row) => ({
                ...(row as object),
                status: scenario.status,
              }));
            break;
          case "frontdesk-queue-kpis":
            rows = frontdesk.queueKpis;
            break;
          case "reception-kpis":
            rows = frontdesk.kpis;
            break;
          case "frontdesk-checkouts":
            rows = frontdesk.checkouts
              .filter(
                (row) =>
                  scenario.visit === undefined ||
                  (scenario.visit === "withNote"
                    ? row.id === "fixture-checkout-01"
                    : scenario.visit === "incomplete"
                      ? row.visitStatus === "incomplete"
                      : scenario.visit === "invoiced"
                        ? row.handoff === "invoiced"
                        : true),
              )
              .map((row) =>
                scenario.visit === "withNote"
                  ? { ...row, doctorNote: "patient has rash" }
                  : row,
              )
              .map((row) => ({
                id: row.id,
                patientId: row.patientId,
                patientName: row.patientName,
                visitId: row.visitId,
                visitStatus: row.visitStatus,
                handoff: row.handoff,
                ...("invoiceReference" in row
                  ? { invoiceReference: row.invoiceReference }
                  : {}),
                ...("outstanding" in row
                  ? { outstanding: row.outstanding, currency: row.currency }
                  : {}),
                fictional: true,
              }));
            break;
          case "frontdesk-vitals":
            // A returning patient without measurements is an explicit scenario:
            // an empty page, never a zero-filled row.
            rows = scenario.patient === "noVitals" ? [] : frontdesk.vitals;
            break;
          case "frontdesk-timeline":
            rows = frontdesk.timeline;
            break;
          case "frontdesk-payments":
            rows = frontdesk.payments;
            break;
        }
      }

      const filters = parseFilters(
        url.searchParams.get("filters"),
        FILTER_FIELDS[resource] ?? [],
      );
      if (filters === null) {
        return HttpResponse.json({ code: "VALIDATION" }, { status: 400 });
      }
      if (resource === "billing-patient-summary" && !filters.id)
        return HttpResponse.json({ code: "VALIDATION" }, { status: 400 });
      if (
        resource === "frontdesk-appointments" &&
        filters.scope &&
        !isReceptionScope(filters.scope)
      )
        return HttpResponse.json({ code: "VALIDATION" }, { status: 400 });
      const filterEntries = Object.entries(filters);
      if (filterEntries.length > 0) {
        rows = rows.filter((row) => {
          if (resource === "frontdesk-appointments")
            return filterEntries.every(([key, expected]) =>
              key === "scope"
                ? isReceptionScope(expected) &&
                  (SCOPE_STATUSES[expected] === null ||
                    (typeof (row as { status?: unknown })?.status ===
                      "string" &&
                      (SCOPE_STATUSES[expected]?.includes(
                        (row as { status: string }).status,
                      ) ??
                        false)))
                : matchesFilters(row, [[key, expected]]),
            );
          if (resource !== "frontdesk-availability")
            return matchesFilters(row, filterEntries);
          if (typeof row !== "object" || row === null || Array.isArray(row))
            return false;
          const record = row as Record<string, unknown>;
          return filterEntries.every(([key, value]) =>
            key === "dateFrom"
              ? typeof record.date === "string" && record.date >= value
              : key === "dateTo"
                ? typeof record.date === "string" && record.date <= value
                : matchesFilters(record, [[key, value]]),
          );
        });
      }

      if (resource === "frontdesk-availability")
        rows.sort((a, b) => {
          const left = a as { date: string; time: string; id: string };
          const right = b as { date: string; time: string; id: string };
          return `${left.date} ${left.time} ${left.id}`.localeCompare(
            `${right.date} ${right.time} ${right.id}`,
          );
        });

      const { limit, offset } = pageParams(url);
      if (isClinicalResource(resource)) {
        const schema = PROXY_READ_RESOURCES[resource]?.schema;
        if (!schema)
          return HttpResponse.json(
            { code: "UPSTREAM_UNAVAILABLE" },
            { status: 502 },
          );
        const parsed = z
          .array(schema)
          .safeParse(rows.slice(offset, offset + limit));
        if (!parsed.success)
          return HttpResponse.json(
            { code: "UPSTREAM_UNAVAILABLE" },
            { status: 502 },
          );
        return HttpResponse.json({ items: parsed.data, total: rows.length });
      }
      return HttpResponse.json({
        items: rows.slice(offset, offset + limit),
        total: rows.length,
      });
    }),

    // Mirror of the proxy fixture bytes route: same wildcard shape, allow-list,
    // fixture role matrix and private headers on success and error alike. No
    // path, query or upstream fallback.
    http.get("/api/files/*", ({ request }) => {
      const noStore = { "cache-control": "no-store" };
      if (!authed) {
        return HttpResponse.json(
          { code: "AUTH_EXPIRED" },
          { status: 401, headers: noStore },
        );
      }
      if (!matrixRoles("clinical-photos").includes(role)) {
        return HttpResponse.json(
          { code: "FORBIDDEN" },
          { status: 403, headers: noStore },
        );
      }

      const fileId = fixtureFileId(new URL(request.url).pathname);
      const asset = fileId === null ? undefined : photoAsset(fileId);
      const bytes = fileId === null ? undefined : photoAssetBytes(fileId);
      if (asset === undefined || bytes === undefined) {
        return HttpResponse.json(
          { code: "NOT_FOUND" },
          { status: 404, headers: noStore },
        );
      }

      return new HttpResponse(new Uint8Array(bytes), {
        headers: {
          "content-type": asset.contentType,
          "x-content-type-options": "nosniff",
          ...noStore,
          "content-length": String(asset.bytes),
        },
      });
    }),
  ];
}

export const defaultHandlers = createHandlers({ authed: false });
