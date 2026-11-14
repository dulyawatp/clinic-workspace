import access from "@clinic/contracts/fixtures/access.json";
import accounts from "@clinic/contracts/fixtures/accounts.json";
import frontdesk from "@clinic/contracts/fixtures/frontdesk.json";
import patients from "@clinic/contracts/fixtures/patients.json";
import workItems from "@clinic/contracts/fixtures/work-items.json";
import { describe, expect, it } from "vitest";
import { ROUTES, type StaticRouteKey } from "../app/routes";
import { isRoleCode, type RoleCode } from "../domain/capability";
import { SCOPE_BY_KPI, SCOPE_STATUSES } from "../features/reception/scopes";
import { navFor } from "../features/shell/nav";
import en from "../i18n/catalog/en.json";
import { FRONTDESK_RESOURCES } from "./msw/handlers";

interface FixtureWorkItem {
  id: string;
  kind: string;
  labelKey: string;
  count: number;
  routeKey?: string;
  requiredCapability?: string;
}

const workItemGroups = workItems as ReadonlyArray<{
  role: string;
  items: ReadonlyArray<FixtureWorkItem>;
}>;

const workItemRows = workItemGroups.flatMap((group) =>
  group.items.map((item) => ({ role: group.role, item })),
);

describe("shared fixtures", () => {
  it("marks every fixture patient as fictional", () => {
    for (const p of patients) expect(p.fictional).toBe(true);
  });

  it("provides at least 24 patients spanning two 20-row search pages", () => {
    expect(patients.length).toBeGreaterThanOrEqual(24);
    expect(
      patients.filter((p) => p.phone.startsWith("08")).length,
    ).toBeGreaterThan(20);
  });

  it("uses Thai, English and mixed-script names", () => {
    expect(patients.some((p) => /[\u0E00-\u0E7F]/.test(p.fullName))).toBe(true);
    expect(patients.some((p) => /[A-Za-z]/.test(p.fullName))).toBe(true);
    expect(
      patients.some(
        (p) =>
          /[\u0E00-\u0E7F]/.test(p.fullName) && /[A-Za-z]/.test(p.fullName),
      ),
    ).toBe(true);
  });

  it("keeps every fixture phone an unmistakably non-dialable placeholder", () => {
    const phones = new Set<string>();
    for (const p of patients) {
      expect(p.phone).toMatch(/^08-FIXTURE-\d{4}$/);
      // A real Thai mobile number carries 9–10 digits after stripping
      // separators; the placeholder must never reach dialable length.
      expect(p.phone.replace(/\D/g, "").length).toBeLessThan(9);
      expect(phones.has(p.phone)).toBe(false);
      phones.add(p.phone);
    }
  });

  it("exposes exactly one fixture account per role code", () => {
    expect(accounts).toHaveLength(7);
    for (const account of accounts) expect(account.user.roles).toHaveLength(1);
    expect(accounts.map((a) => a.user.roles[0]).sort()).toEqual([
      "ADM",
      "CAS",
      "DOC",
      "MGR",
      "NUR",
      "PHA",
      "REC",
    ]);
    expect(new Set(accounts.map((a) => a.usr)).size).toBe(7);
    expect(new Set(accounts.map((a) => a.user.id)).size).toBe(7);
  });

  it("narrows every fixture account role to a known role code", () => {
    for (const account of accounts) {
      expect(account.user.roles.every(isRoleCode)).toBe(true);
    }
  });

  it("keeps one work-item group per role, all narrowable role codes", () => {
    expect(workItemGroups.map((group) => group.role)).toEqual([
      "REC",
      "DOC",
      "NUR",
      "PHA",
      "CAS",
      "MGR",
      "ADM",
    ]);
    for (const group of workItemGroups)
      expect(isRoleCode(group.role)).toBe(true);
    expect(new Set(workItemRows.map(({ item }) => item.id)).size).toBe(
      workItemRows.length,
    );
  });

  it("routes work items only through canonical Plan 02 route keys", () => {
    for (const { item } of workItemRows) {
      if (item.routeKey === undefined) continue;
      expect(Object.hasOwn(ROUTES, item.routeKey), item.id).toBe(true);
      expect(item.routeKey).not.toMatch(/^\//);
    }
  });

  it("resolves each routable work item to a destination its role nav reaches", () => {
    for (const { role, item } of workItemRows) {
      if (item.routeKey === undefined) continue;
      const destinations = new Set(
        navFor([role as RoleCode], {}).map((navItem) => navItem.routeKey),
      );
      expect(destinations.has(item.routeKey as StaticRouteKey), item.id).toBe(
        true,
      );
    }
  });

  it("keeps only the destinations Plan 02 has no surface for unrouted", () => {
    const unrouted = workItemRows
      .filter(({ item }) => item.routeKey === undefined)
      .map(({ item }) => item.id);
    expect(unrouted).toEqual([
      "fixture-work-mgr-reports",
      "fixture-work-adm-staff",
    ]);
  });

  it("uses navigable catalogue labels and safe non-negative counts", () => {
    for (const { item } of workItemRows) {
      expect(item.labelKey, item.id).toMatch(/^(nav|status)\./);
      expect(Object.hasOwn(en, item.labelKey), item.id).toBe(true);
      expect(Number.isSafeInteger(item.count), item.id).toBe(true);
      expect(item.count, item.id).toBeGreaterThanOrEqual(0);
    }
  });

  it("derives every reception KPI count from the rows its drilldown shows", () => {
    const rows = frontdesk.appointments;
    expect(rows.length).toBeGreaterThan(0);
    for (const kpi of frontdesk.kpis) {
      const scope = SCOPE_BY_KPI[kpi.key];
      expect(scope, kpi.id).toBeDefined();
      if (scope === undefined) continue;
      const statuses = SCOPE_STATUSES[scope];
      const expected =
        statuses === null
          ? rows.length
          : rows.filter((row) => statuses.includes(row.status)).length;
      expect(kpi.count, `${kpi.id} (${scope})`).toBe(expected);
    }
  });

  it("keeps queue and appointment identities and waiting counts consistent", () => {
    expect(frontdesk.queue).toHaveLength(frontdesk.appointments.length);
    expect(new Set(frontdesk.queue.map((row) => row.appointmentId))).toEqual(
      new Set(frontdesk.appointments.map((row) => row.id)),
    );
    for (const row of frontdesk.queue) {
      const appointment = frontdesk.appointments.find(
        (item) => item.id === row.appointmentId,
      );
      expect(appointment, row.id).toBeDefined();
      expect(row.patientId).toBe(appointment?.patientId);
      expect(row.status).toBe(appointment?.status);
    }
    const kpis = frontdesk.queueKpis[0];
    expect(kpis).toBeDefined();
    expect(kpis?.appointments).toBe(frontdesk.queue.length);
    expect(kpis?.arrivals).toBe(
      frontdesk.queue.filter((row) => row.status === "arrived").length,
    );
    expect(kpis?.waiting).toBe(
      frontdesk.queue.filter((row) => row.status === "waiting").length,
    );
    expect(kpis?.inConsultation).toBe(
      frontdesk.queue.filter((row) => row.status === "in_consultation").length,
    );
    expect(kpis?.waiting).toBe(
      frontdesk.kpis.find((kpi) => kpi.key === "waiting")?.count,
    );
  });

  it("serves every front-desk resource from the single role matrix", () => {
    const resources: Record<string, readonly string[]> = access.resources;
    for (const resource of FRONTDESK_RESOURCES) {
      expect(Object.hasOwn(resources, resource), resource).toBe(true);
      const roles = resources[resource] ?? [];
      expect(roles.length, resource).toBeGreaterThan(0);
      for (const role of roles)
        expect(isRoleCode(role), `${resource}`).toBe(true);
    }
    // The fixture file must not carry a second copy of the matrix.
    expect(frontdesk).not.toHaveProperty("roles");
  });

  it("labels the access matrix unverified and keeps the canonical rows", () => {
    expect(access).toEqual({
      matrix: "unverified",
      resources: {
        patients: ["REC", "DOC", "NUR", "MGR", "ADM"],
        appointments: ["REC", "DOC", "NUR", "MGR", "ADM"],
        encounters: ["DOC", "NUR", "ADM"],
        staff: ["ADM", "MGR"],
        "work-items": ["REC", "DOC", "NUR", "PHA", "CAS", "MGR", "ADM"],
        profile: ["REC", "DOC", "NUR", "PHA", "CAS", "MGR", "ADM"],
        "frontdesk-patients": ["REC", "DOC", "NUR", "ADM"],
        "billing-patient-summary": ["CAS", "MGR"],
        "frontdesk-appointments": ["REC", "DOC", "NUR", "ADM"],
        "frontdesk-availability": ["REC", "DOC", "NUR", "ADM"],
        "frontdesk-availability-options": ["REC", "DOC", "NUR", "ADM"],
        "frontdesk-queue": ["REC", "NUR"],
        "frontdesk-queue-kpis": ["REC", "NUR"],
        "reception-kpis": ["REC", "DOC", "NUR", "ADM"],
        "frontdesk-checkouts": ["REC", "CAS"],
        "frontdesk-vitals": ["REC", "DOC", "NUR", "ADM"],
        "frontdesk-timeline": ["REC", "DOC", "NUR", "ADM"],
        "frontdesk-payments": ["CAS", "MGR"],
        "clinical-visits": ["DOC", "NUR"],
        "clinical-sections": ["DOC"],
        "clinical-vitals": ["DOC", "NUR"],
        "clinical-diagnoses": ["DOC"],
        "clinical-medications": ["DOC"],
        "clinical-photos": ["DOC", "NUR"],
        "clinical-labs": ["DOC", "NUR"],
        "clinical-procedures": ["DOC", "NUR"],
        "clinical-next-patients": ["DOC"],
        "my-day-counts": ["DOC"],
        "nurse-tasks": ["NUR"],
      },
    });
  });
});
