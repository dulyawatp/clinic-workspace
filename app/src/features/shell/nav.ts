import access from "@clinic/contracts/fixtures/access.json";
import type { StaticRouteKey } from "../../app/routes";
import type { SurfaceId } from "../../design/surfaces";
import {
  type Capability,
  type CapabilityMap,
  getCapability,
  isRoleCode,
  type RoleCode,
} from "../../domain/capability";

/**
 * Nav visibility for a surface is read from the same interim matrix the proxy
 * and MSW enforce, so a role cannot be advertised a destination its read
 * resource denies. Still presentation only: never authorization.
 */
function resourceRoles(resource: keyof typeof access.resources): RoleCode[] {
  return access.resources[resource].filter(isRoleCode);
}

export interface NavItem {
  id: string;
  labelKey: string;
  /** Static path key only: a route builder is never a nav destination. */
  routeKey: StaticRouteKey;
  icon: string;
  /**
   * Nav gate, never authorization. It can be narrower than the surface's role
   * list: a role may legitimately reach a surface by deep link (for example a
   * delegated cashier link) without advertising it in its own rail.
   */
  roles?: readonly RoleCode[];
  requiredCapability?: Capability;
  /** Design surface this destination belongs to. */
  surface?: SurfaceId;
  /** Match this path exactly: the item owns a subtree (its own children). */
  exact?: true;
  owner: "02" | "03" | "04" | "05";
}

/**
 * Every designed destination, in design-manifest order. Record destinations
 * carry `roles: []`: they exist for coverage and deep-linking, but global
 * navigation never points at a record that does not exist yet.
 */
export const NAV_MANIFEST: readonly NavItem[] = [
  {
    id: "home",
    labelKey: "nav.today",
    routeKey: "home",
    icon: "home",
    surface: "APP-02",
    exact: true,
    owner: "02",
  },
  {
    id: "search",
    labelKey: "nav.search",
    routeKey: "search",
    icon: "search",
    surface: "APP-03",
    owner: "02",
  },
  {
    id: "profile",
    labelKey: "nav.profile",
    routeKey: "profile",
    icon: "profile",
    surface: "APP-02",
    owner: "02",
  },
  {
    id: "system",
    labelKey: "nav.system",
    routeKey: "system",
    icon: "system",
    roles: ["ADM", "MGR"],
    requiredCapability: "read.admin",
    surface: "APP-04",
    owner: "02",
  },

  {
    id: "patients",
    labelKey: "nav.patients",
    routeKey: "patients",
    icon: "patients",
    roles: resourceRoles("frontdesk-patients"),
    requiredCapability: "read.patient",
    surface: "PAT-01",
    owner: "03",
  },
  {
    id: "patient-new",
    labelKey: "surface.PAT-02",
    routeKey: "patientNew",
    icon: "patients",
    roles: [],
    surface: "PAT-02",
    owner: "03",
  },
  {
    id: "patient",
    labelKey: "surface.PAT-03",
    routeKey: "patientPattern",
    icon: "patients",
    roles: [],
    surface: "PAT-03",
    owner: "03",
  },
  {
    id: "photos",
    labelKey: "surface.PAT-04",
    routeKey: "photoPattern",
    icon: "patients",
    roles: [],
    surface: "PAT-04",
    owner: "03",
  },
  {
    id: "photo-compare",
    labelKey: "surface.PAT-05",
    routeKey: "photoComparePattern",
    icon: "patients",
    roles: [],
    surface: "PAT-05",
    owner: "03",
  },

  {
    id: "reception",
    labelKey: "nav.reception",
    routeKey: "reception",
    icon: "reception",
    roles: ["REC"],
    requiredCapability: "read.appointment",
    surface: "REC-01",
    exact: true,
    owner: "03",
  },
  {
    id: "queue",
    labelKey: "nav.queue",
    routeKey: "queue",
    icon: "queue",
    roles: resourceRoles("frontdesk-queue"),
    requiredCapability: "read.appointment",
    surface: "REC-02",
    owner: "03",
  },
  {
    id: "appointments",
    labelKey: "nav.appointments",
    routeKey: "appointments",
    icon: "appointments",
    roles: ["REC", "DOC"],
    requiredCapability: "read.appointment",
    surface: "REC-03",
    owner: "03",
  },
  {
    id: "appointment",
    labelKey: "surface.REC-04",
    routeKey: "appointmentPattern",
    icon: "appointments",
    roles: [],
    surface: "REC-04",
    owner: "03",
  },
  {
    id: "checkout",
    labelKey: "nav.checkout",
    routeKey: "checkout",
    icon: "checkout",
    roles: resourceRoles("frontdesk-checkouts"),
    requiredCapability: "read.billing",
    surface: "REC-05",
    owner: "03",
  },

  {
    id: "doctor",
    labelKey: "nav.myday",
    routeKey: "doctor",
    icon: "appointments",
    roles: ["DOC"],
    surface: "DOC-01",
    exact: true,
    owner: "04",
  },
  {
    id: "visit",
    labelKey: "surface.DOC-02",
    routeKey: "visitPattern",
    icon: "clipboard",
    roles: [],
    surface: "DOC-02",
    owner: "04",
  },
  {
    id: "procedure",
    labelKey: "surface.DOC-03",
    routeKey: "procedurePattern",
    icon: "clipboard",
    roles: [],
    surface: "DOC-03",
    owner: "04",
  },
  {
    id: "lab",
    labelKey: "nav.labs",
    routeKey: "labPattern",
    icon: "flask",
    roles: [],
    surface: "DOC-04",
    owner: "04",
  },

  {
    id: "nurse",
    labelKey: "nav.assigned",
    routeKey: "nurse",
    icon: "patients",
    roles: ["NUR"],
    surface: "NUR-01",
    exact: true,
    owner: "04",
  },
  {
    id: "nurse-tasks",
    labelKey: "nav.tasks",
    routeKey: "nurseTasks",
    icon: "clipboard",
    roles: ["NUR"],
    surface: "NUR-02",
    owner: "04",
  },

  {
    id: "pharmacy",
    labelKey: "nav.pharmacy",
    routeKey: "pharmacy",
    icon: "pharmacy",
    roles: ["PHA"],
    surface: "PHA-01",
    exact: true,
    owner: "05",
  },
  {
    id: "pharmacy-items",
    labelKey: "nav.items",
    routeKey: "pharmacyItems",
    icon: "box",
    roles: ["PHA"],
    surface: "PHA-02",
    owner: "05",
  },
  {
    id: "pharmacy-batches",
    labelKey: "nav.batches",
    routeKey: "pharmacyBatches",
    icon: "layers",
    roles: ["PHA", "MGR"],
    surface: "PHA-03",
    owner: "05",
  },
  {
    id: "pharmacy-receive",
    labelKey: "nav.receive",
    routeKey: "pharmacyReceive",
    icon: "truck",
    roles: ["PHA"],
    surface: "PHA-05",
    owner: "05",
  },
  {
    id: "pharmacy-balances",
    labelKey: "nav.balances",
    routeKey: "pharmacyBalances",
    icon: "chart",
    roles: ["PHA", "MGR"],
    surface: "PHA-04",
    owner: "05",
  },
  {
    id: "pharmacy-movements",
    labelKey: "nav.movements",
    routeKey: "pharmacyMovements",
    icon: "transfer",
    roles: ["PHA"],
    surface: "PHA-06",
    owner: "05",
  },
  {
    id: "pharmacy-purchasing",
    labelKey: "nav.purchasing",
    routeKey: "pharmacyPurchasing",
    icon: "truck",
    roles: ["PHA"],
    surface: "PHA-07",
    owner: "05",
  },

  {
    id: "cashier",
    labelKey: "nav.pending",
    routeKey: "cashier",
    icon: "receipt",
    roles: ["CAS"],
    surface: "CAS-01",
    exact: true,
    owner: "05",
  },
  {
    id: "invoices",
    labelKey: "nav.invoices",
    routeKey: "invoices",
    icon: "receipt",
    roles: ["CAS", "MGR"],
    surface: "CAS-02",
    owner: "05",
  },
  {
    id: "payments",
    labelKey: "nav.payments",
    routeKey: "payments",
    icon: "cash",
    roles: ["CAS", "MGR"],
    surface: "CAS-03",
    owner: "05",
  },

  {
    id: "management",
    labelKey: "nav.overview",
    routeKey: "management",
    icon: "chart",
    roles: ["MGR"],
    surface: "MGR-01",
    exact: true,
    owner: "05",
  },
  {
    id: "management-reports",
    labelKey: "nav.reports",
    routeKey: "managementReports",
    icon: "chart",
    roles: ["MGR"],
    surface: "MGR-02",
    owner: "05",
  },

  {
    id: "admin",
    labelKey: "nav.setup",
    routeKey: "admin",
    icon: "settings",
    roles: ["ADM"],
    surface: "ADM-01",
    exact: true,
    owner: "05",
  },
  {
    id: "admin-users",
    labelKey: "nav.staff",
    routeKey: "adminUsers",
    icon: "shield",
    roles: ["ADM"],
    surface: "ADM-02",
    owner: "05",
  },
  {
    id: "admin-clinical",
    labelKey: "nav.clinical",
    routeKey: "adminClinical",
    icon: "clipboard",
    roles: ["ADM"],
    surface: "ADM-03",
    owner: "05",
  },
  {
    id: "admin-settings",
    labelKey: "nav.settings",
    routeKey: "adminSettings",
    icon: "settings",
    roles: ["ADM"],
    surface: "ADM-04",
    owner: "05",
  },
  {
    id: "admin-system",
    labelKey: "surface.ADM-05",
    routeKey: "adminSystem",
    icon: "activity",
    roles: ["ADM"],
    surface: "ADM-05",
    owner: "05",
  },
];

/**
 * Designed navigation order per role. The manifest is one list; each role reads
 * it in the order the design gives that role, so the first six links are the
 * role's own workspace rather than a global sequence. Only shared surfaces
 * (home, search, profile) reach every role.
 */
const ROLE_NAV_ORDER: Readonly<Record<RoleCode, readonly string[]>> = {
  REC: [
    "home",
    "reception",
    "queue",
    "appointments",
    "patients",
    "checkout",
    "search",
    "profile",
  ],
  DOC: ["home", "doctor", "appointments", "patients", "search", "profile"],
  NUR: [
    "home",
    "nurse",
    "nurse-tasks",
    "queue",
    "patients",
    "search",
    "profile",
  ],
  PHA: [
    "home",
    "pharmacy",
    "pharmacy-items",
    "pharmacy-batches",
    "pharmacy-receive",
    "pharmacy-balances",
    "pharmacy-movements",
    "pharmacy-purchasing",
    "search",
    "profile",
  ],
  CAS: [
    "home",
    "cashier",
    "invoices",
    "payments",
    "checkout",
    "search",
    "profile",
  ],
  MGR: [
    "home",
    "management",
    "management-reports",
    "invoices",
    "payments",
    "pharmacy-batches",
    "pharmacy-balances",
    "system",
    "search",
    "profile",
  ],
  ADM: [
    "home",
    "admin",
    "admin-users",
    "admin-clinical",
    "admin-settings",
    "admin-system",
    "system",
    "patients",
    "search",
    "profile",
  ],
};

function navRank(roleList: readonly RoleCode[], id: string): number {
  let rank = Number.POSITIVE_INFINITY;
  for (const role of roleList) {
    const index = ROLE_NAV_ORDER[role]?.indexOf(id) ?? -1;
    if (index >= 0 && index < rank) rank = index;
  }
  return rank;
}

/**
 * Visible destinations for the held roles: role gate, then the interim read
 * matrix's capability state, then the role's designed order.
 */
export function navFor(
  roles: readonly RoleCode[],
  caps: CapabilityMap,
): NavItem[] {
  const roleList = Array.isArray(roles) ? roles : [];
  return NAV_MANIFEST.filter((item) => {
    if (item.roles && !item.roles.some((role) => roleList.includes(role)))
      return false;
    const state = item.requiredCapability
      ? getCapability(caps, item.requiredCapability)
      : "read_only";
    return state !== "denied" && state !== "blocked_unverified";
  })
    .map((item, index) => ({ item, index }))
    .sort((a, b) => {
      const rank = navRank(roleList, a.item.id) - navRank(roleList, b.item.id);
      // A tie, or two items the role's order does not list at all
      // (Infinity − Infinity = NaN, which is falsy), keeps manifest order.
      return rank || a.index - b.index;
    })
    .map(({ item }) => item);
}
