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
import { isRoleCode, type RoleCode } from "../capabilities.js";

/**
 * Static fixture map. Registry entries name a key here; nothing reads a
 * caller-supplied path and nothing opens a socket. Adding a file is a code
 * change, not a configuration change.
 */
const FIXTURE_ROWS: Readonly<Record<string, readonly unknown[]>> = {
  "clinical-visits.json": clinical.visits,
  "clinical-sections.json": clinical.sections,
  "clinical-vitals.json": clinical.vitals,
  "clinical-diagnoses.json": clinical.diagnoses,
  "clinical-medications.json": clinical.medications,
  "clinical-photos.json": clinical.photos,
  "clinical-labs.json": clinical.labs,
  "nurse-tasks.json": clinical.nurseTasks,
  "clinical-procedures.json": clinical.procedures,
  "my-day-counts.json": clinical.myDayCounts,
  "clinical-next-patients.json": clinical.nextPatients,
  "patients.json": patients,
  "appointments.json": appointments,
  "encounters.json": encounters,
  "staff.json": staff,
  "work-items.json": workItems,
  "profile.json": [profile],
  // One fixture file, one static key per collection: the registry never reads
  // a caller-supplied path.
  "frontdesk-patients.json": frontdesk.patients,
  "billing-patient-summary.json": frontdesk.patients,
  "frontdesk-appointments.json": frontdesk.appointments,
  "frontdesk-availability.json": frontdesk.availability,
  "frontdesk-availability-options.json": frontdesk.availability,
  "frontdesk-queue.json": frontdesk.queue,
  "frontdesk-queue-kpis.json": frontdesk.queueKpis,
  "reception-kpis.json": frontdesk.kpis,
  "frontdesk-checkouts.json": frontdesk.checkouts,
  "frontdesk-vitals.json": frontdesk.vitals,
  "frontdesk-timeline.json": frontdesk.timeline,
  "frontdesk-payments.json": frontdesk.payments,
};

export type ReadFilters = Readonly<Record<string, string>>;

export interface FixtureQuery {
  roles: readonly RoleCode[];
  /** Already trimmed and validated by the handler; empty means no search. */
  q: string;
  /** Public DTO fields only: the handler rejects any other filter key. */
  filters: ReadFilters;
  /** Public searchable fields declared by the registry entry. */
  searchFields: readonly string[];
  limit: number;
  offset: number;
}

export interface FixturePage {
  rows: unknown[];
  total: number;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isPrimitive(value: unknown): value is string | number | boolean {
  return (
    typeof value === "string" ||
    typeof value === "number" ||
    typeof value === "boolean"
  );
}

function availabilityOptions(source: readonly unknown[]) {
  const practitioners = new Map<string, string>();
  const types = new Set<string>();
  for (const row of source) {
    if (!isRecord(row)) continue;
    if (
      typeof row.practitionerId === "string" &&
      row.practitionerId &&
      typeof row.practitionerName === "string" &&
      row.practitionerName
    )
      practitioners.set(row.practitionerId, row.practitionerName);
    if (typeof row.appointmentType === "string" && row.appointmentType)
      types.add(row.appointmentType);
  }
  return {
    practitioners: [...practitioners]
      .map(([id, name]) => ({ id, name }))
      .sort((a, b) => a.name.localeCompare(b.name) || a.id.localeCompare(b.id)),
    types: [...types].sort(),
  };
}

/** `work-items.json` is grouped per role; the session role picks one group. */
function workItemsForRole(
  groups: readonly unknown[],
  roles: readonly RoleCode[],
): unknown[] {
  for (const group of groups) {
    if (!isRecord(group) || !isRoleCode(group.role)) continue;
    if (!roles.includes(group.role)) continue;
    return Array.isArray(group.items) ? group.items : [];
  }
  return [];
}

function matchesFilters(
  row: Record<string, unknown>,
  filters: ReadonlyArray<readonly [string, string]>,
): boolean {
  return filters.every(
    ([key, expected]) =>
      Object.hasOwn(row, key) &&
      isPrimitive(row[key]) &&
      String(row[key]) === expected,
  );
}

const APPOINTMENT_SCOPES: Readonly<Record<string, readonly string[] | null>> = {
  all: null,
  checkedIn: ["arrived", "waiting", "called", "in_consultation", "completed"],
  waiting: ["waiting"],
  upcoming: ["scheduled"],
};

/**
 * Reads one fixture page. The allow-list and role filter already ran in the
 * handler, so the only inputs here are the registry's static file key and
 * validated query values.
 */
export function readFixturePage(
  fixtureFile: string | undefined,
  query: FixtureQuery,
): FixturePage {
  const file = fixtureFile;
  const source = file === undefined ? undefined : FIXTURE_ROWS[file];
  if (file === undefined || source === undefined) {
    // A registry entry without a fixture is a programming error; fail with a
    // client-safe upstream error instead of exposing the file name.
    throw new Error("UNKNOWN_FIXTURE");
  }

  let rows: unknown[] =
    file === "work-items.json"
      ? workItemsForRole(source, query.roles)
      : file === "frontdesk-availability-options.json"
        ? [availabilityOptions(source)]
        : [...source];

  const needle = query.q.toLowerCase();
  if (needle !== "") {
    const fields = query.searchFields;
    rows = rows.filter(
      (row) =>
        isRecord(row) &&
        fields.some((field) => {
          const value = row[field];
          return (
            typeof value === "string" && value.toLowerCase().includes(needle)
          );
        }),
    );
  }

  const filterEntries = Object.entries(query.filters);
  if (filterEntries.length > 0) {
    rows = rows.filter(
      (row) =>
        isRecord(row) &&
        (file === "frontdesk-appointments.json"
          ? filterEntries.every(([key, expected]) =>
              key === "scope"
                ? Object.hasOwn(APPOINTMENT_SCOPES, expected) &&
                  (APPOINTMENT_SCOPES[expected] === null ||
                    (typeof row.status === "string" &&
                      (APPOINTMENT_SCOPES[expected]?.includes(row.status) ??
                        false)))
                : matchesFilters(row, [[key, expected]]),
            )
          : file === "frontdesk-availability.json"
            ? filterEntries.every(([key, expected]) =>
                key === "dateFrom"
                  ? typeof row.date === "string" && row.date >= expected
                  : key === "dateTo"
                    ? typeof row.date === "string" && row.date <= expected
                    : matchesFilters(row, [[key, expected]]),
              )
            : matchesFilters(row, filterEntries)),
    );
  }

  if (file === "frontdesk-availability.json") {
    rows.sort((a, b) => {
      if (!isRecord(a) || !isRecord(b)) return 0;
      return `${a.date} ${a.time} ${a.id}`.localeCompare(
        `${b.date} ${b.time} ${b.id}`,
      );
    });
  }

  return {
    total: rows.length,
    rows: rows.slice(query.offset, query.offset + query.limit),
  };
}
