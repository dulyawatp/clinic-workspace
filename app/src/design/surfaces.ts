import { ROUTES, type StaticRouteKey } from "../app/routes";
import type { RoleCode } from "../domain/capability";

/** Every role code in the installed system, in design manifest order. */
export const ALL_ROLES: readonly RoleCode[] = [
  "REC",
  "DOC",
  "NUR",
  "PHA",
  "CAS",
  "MGR",
  "ADM",
];

/** The 37 designed surfaces, keyed by their manifest id. */
export type SurfaceId =
  | "APP-01"
  | "APP-02"
  | "APP-03"
  | "APP-04"
  | "PAT-01"
  | "PAT-02"
  | "PAT-03"
  | "PAT-04"
  | "PAT-05"
  | "REC-01"
  | "REC-02"
  | "REC-03"
  | "REC-04"
  | "REC-05"
  | "DOC-01"
  | "DOC-02"
  | "DOC-03"
  | "DOC-04"
  | "NUR-01"
  | "NUR-02"
  | "PHA-01"
  | "PHA-02"
  | "PHA-03"
  | "PHA-04"
  | "PHA-05"
  | "PHA-06"
  | "PHA-07"
  | "CAS-01"
  | "CAS-02"
  | "CAS-03"
  | "MGR-01"
  | "MGR-02"
  | "ADM-01"
  | "ADM-02"
  | "ADM-03"
  | "ADM-04"
  | "ADM-05";

export interface SurfaceSpec {
  readonly id: SurfaceId;
  /** Static route key: the design route this surface is addressed at. */
  readonly routeKey: StaticRouteKey;
  /** Catalogue key for the designed page title (`surface.*`). */
  readonly titleKey: string;
  /**
   * Presentation-level role expectation copied from the design manifest.
   * `-LIMITED`, `-AUTHORIZED`, `-DELEGATED`, `PERMITTED` and `AUTHORIZED`
   * variants are recorded as their base role: the qualifier describes what the
   * surface shows, while server records and permissions stay authoritative.
   */
  readonly roles: readonly RoleCode[];
  /** Pre-authentication surfaces are never part of the application shell. */
  readonly preAuth?: true;
}

export const SURFACES: readonly SurfaceSpec[] = [
  {
    id: "APP-01",
    routeKey: "login",
    titleKey: "page.signin.title",
    roles: ALL_ROLES,
    preAuth: true,
  },
  {
    id: "APP-02",
    routeKey: "home",
    titleKey: "surface.APP-02",
    roles: ALL_ROLES,
  },
  {
    id: "APP-03",
    routeKey: "search",
    titleKey: "surface.APP-03",
    roles: ALL_ROLES,
  },
  {
    id: "APP-04",
    routeKey: "system",
    titleKey: "surface.APP-04",
    roles: ALL_ROLES,
  },
  {
    id: "PAT-01",
    routeKey: "patients",
    titleKey: "surface.PAT-01",
    roles: ["REC", "DOC", "NUR", "ADM"],
  },
  {
    id: "PAT-02",
    routeKey: "patientNew",
    titleKey: "surface.PAT-02",
    roles: ["REC", "ADM"],
  },
  {
    id: "PAT-03",
    routeKey: "patientPattern",
    titleKey: "surface.PAT-03",
    roles: ALL_ROLES,
  },
  {
    id: "PAT-04",
    routeKey: "photoPattern",
    titleKey: "surface.PAT-04",
    roles: ["REC", "DOC", "NUR"],
  },
  {
    id: "PAT-05",
    routeKey: "photoComparePattern",
    titleKey: "surface.PAT-05",
    roles: ["DOC", "NUR"],
  },
  {
    id: "REC-01",
    routeKey: "reception",
    titleKey: "surface.REC-01",
    roles: ["REC"],
  },
  {
    id: "REC-02",
    routeKey: "queue",
    titleKey: "surface.REC-02",
    roles: ["REC", "NUR"],
  },
  {
    id: "REC-03",
    routeKey: "appointments",
    titleKey: "surface.REC-03",
    roles: ["REC", "DOC"],
  },
  {
    id: "REC-04",
    routeKey: "appointmentPattern",
    titleKey: "surface.REC-04",
    roles: ["REC", "DOC"],
  },
  {
    id: "REC-05",
    routeKey: "checkout",
    titleKey: "surface.REC-05",
    roles: ["REC", "CAS"],
  },
  {
    id: "DOC-01",
    routeKey: "doctor",
    titleKey: "surface.DOC-01",
    roles: ["DOC"],
  },
  {
    id: "DOC-02",
    routeKey: "visitPattern",
    titleKey: "surface.DOC-02",
    roles: ["DOC", "NUR"],
  },
  {
    id: "DOC-03",
    routeKey: "procedurePattern",
    titleKey: "surface.DOC-03",
    roles: ["DOC", "NUR", "PHA"],
  },
  {
    id: "DOC-04",
    routeKey: "labPattern",
    titleKey: "surface.DOC-04",
    roles: ["DOC", "NUR"],
  },
  {
    id: "NUR-01",
    routeKey: "nurse",
    titleKey: "surface.NUR-01",
    roles: ["NUR"],
  },
  {
    id: "NUR-02",
    routeKey: "nurseTasks",
    titleKey: "surface.NUR-02",
    roles: ["NUR", "DOC"],
  },
  {
    id: "PHA-01",
    routeKey: "pharmacy",
    titleKey: "surface.PHA-01",
    roles: ["PHA"],
  },
  {
    id: "PHA-02",
    routeKey: "pharmacyItems",
    titleKey: "surface.PHA-02",
    roles: ["PHA"],
  },
  {
    id: "PHA-03",
    routeKey: "pharmacyBatches",
    titleKey: "surface.PHA-03",
    roles: ["PHA", "MGR"],
  },
  {
    id: "PHA-04",
    routeKey: "pharmacyBalances",
    titleKey: "surface.PHA-04",
    roles: ["PHA", "MGR"],
  },
  {
    id: "PHA-05",
    routeKey: "pharmacyReceive",
    titleKey: "surface.PHA-05",
    roles: ["PHA"],
  },
  {
    id: "PHA-06",
    routeKey: "pharmacyMovements",
    titleKey: "surface.PHA-06",
    roles: ["PHA", "DOC", "ADM"],
  },
  {
    id: "PHA-07",
    routeKey: "pharmacyPurchasing",
    titleKey: "surface.PHA-07",
    roles: ["PHA", "MGR"],
  },
  {
    id: "CAS-01",
    routeKey: "cashier",
    titleKey: "surface.CAS-01",
    roles: ["CAS", "REC"],
  },
  {
    id: "CAS-02",
    routeKey: "invoices",
    titleKey: "surface.CAS-02",
    roles: ["CAS", "MGR"],
  },
  {
    id: "CAS-03",
    routeKey: "payments",
    titleKey: "surface.CAS-03",
    roles: ["CAS", "MGR"],
  },
  {
    id: "MGR-01",
    routeKey: "management",
    titleKey: "surface.MGR-01",
    roles: ["MGR"],
  },
  {
    id: "MGR-02",
    routeKey: "managementReports",
    titleKey: "surface.MGR-02",
    roles: ["MGR"],
  },
  {
    id: "ADM-01",
    routeKey: "admin",
    titleKey: "surface.ADM-01",
    roles: ["ADM"],
  },
  {
    id: "ADM-02",
    routeKey: "adminUsers",
    titleKey: "surface.ADM-02",
    roles: ["ADM"],
  },
  {
    id: "ADM-03",
    routeKey: "adminClinical",
    titleKey: "surface.ADM-03",
    roles: ["ADM"],
  },
  {
    id: "ADM-04",
    routeKey: "adminSettings",
    titleKey: "surface.ADM-04",
    roles: ["ADM", "MGR"],
  },
  {
    id: "ADM-05",
    routeKey: "adminSystem",
    titleKey: "surface.ADM-05",
    roles: ["ADM"],
  },
];

const BY_ID = new Map<SurfaceId, SurfaceSpec>(
  SURFACES.map((surface) => [surface.id, surface]),
);

export function isSurfaceId(value: string): value is SurfaceId {
  return BY_ID.has(value as SurfaceId);
}

export function surfaceFor(id: SurfaceId): SurfaceSpec {
  const spec = BY_ID.get(id);
  if (!spec) throw new Error(`Unknown surface: ${id}`);
  return spec;
}

export function surfacePath(id: SurfaceId): string {
  const { routeKey } = surfaceFor(id);
  return ROUTES[routeKey];
}

/**
 * Presentation-only role check for a surface. A user with several roles sees a
 * surface if any of their roles is on the design's list; the server remains the
 * only authority for records and actions.
 */
export function canAccessSurface(
  id: SurfaceId,
  roles: readonly RoleCode[],
): boolean {
  const spec = BY_ID.get(id);
  if (!spec) return false;
  const held: readonly RoleCode[] = Array.isArray(roles) ? roles : [];
  return spec.roles.some((role) => held.includes(role));
}

/** The designed home surface for each role (used by the APP-02 dispatch). */
export const ROLE_HOME: Readonly<Record<RoleCode, SurfaceId>> = {
  REC: "REC-01",
  DOC: "DOC-01",
  NUR: "NUR-01",
  PHA: "PHA-01",
  CAS: "CAS-01",
  MGR: "MGR-01",
  ADM: "ADM-01",
};

/**
 * First held role that has a designed home wins, in the session's own role
 * order, so a multi-role user keeps the identity they signed in with.
 */
export function roleHomeSurface(
  roles: readonly RoleCode[],
): SurfaceId | undefined {
  const held: readonly RoleCode[] = Array.isArray(roles) ? roles : [];
  for (const role of held) {
    const home = ROLE_HOME[role];
    if (home) return home;
  }
  return undefined;
}
