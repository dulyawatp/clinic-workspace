import type { MarleyRuntime } from "./marley-env";

/**
 * The seven-role matrix: what each synthetic identity is allowed to see and
 * read. This is the spec the tests assert; it is presentation + proxy
 * allow-list only and never grants anything itself.
 */

export type ProxyRole = "DOC" | "NUR" | "PHA" | "CAS" | "MGR" | "REC" | "ADM";

export const API = {
  session: "/api/session",
  livePatients: "/api/live/synthetic-patients",
  liveStock: "/api/live/synthetic-stock",
  liveFinance: "/api/live/synthetic-finance",
  /** Live appointment list: readable by `DOC` and `REC` only, empty is valid. */
  liveAppointments: "/api/live/synthetic-appointments",
  /**
   * Cross-session encounter-draft recovery hints. Stays disabled while
   * `CLINIC_LIVE_ENCOUNTER_WRITES` is unset: `403 WRITE_DISABLED` proves only
   * that the gate is closed, never that draft recovery works.
   */
  liveDraftRecovery: "/api/live/encounter-drafts",
  /** Fixture read surface: must fail closed in live mode. */
  fixturePatients: "/api/read/patients",
  /** Fixture private bytes: must fail closed in live mode. */
  fixtureFile: "/api/files/clinic-photo-sample.png",
} as const;

export function livePhotos(reference: string): string {
  return `/api/live/synthetic-patients/${encodeURIComponent(reference)}/photos`;
}

/** Client routes asserted by the matrix (kept in sync with src/app/routes.ts). */
export const PAGE = {
  login: "/clinic/login",
  home: "/clinic",
  search: "/clinic/search",
  patients: "/clinic/patients",
  profile: "/clinic/profile",
  pharmacy: "/clinic/pharmacy",
  /** Billing-role destination of the folded finance link (CAS-01). */
  cashier: "/clinic/cashier",
  /** Owner/manager destination of the folded finance link (MGR-01). */
  management: "/clinic/management",
  /** Designed PAT-04 photo surface for one patient. */
  livePhotos: (id: string) =>
    `/clinic/patients/${encodeURIComponent(id)}/photos`,
  /** Designed PAT-04 live-photo workspace for one patient. */
  livePhotoWorkspace: (id: string) =>
    `/clinic/patients/${encodeURIComponent(id)}/live-photos`,
  /** Legacy lab-photo index: a role-guarded redirect, not a page. */
  livePhotosIndex: "/clinic/lab/photos",
  /** Legacy lab-photo detail: redirects to the live-photo workspace. */
  livePhoto: (id: string) => `/clinic/lab/photos/${encodeURIComponent(id)}`,
  /** React-era finance aggregate: a redirect-only address, never a page. */
  legacyFinance: "/clinic/finance",
  system: "/clinic/system",
} as const;

export interface RoleCase {
  readonly role: ProxyRole;
  readonly account: { user: string; password: string };
  /** Exact sidebar destinations: anything else is a nav leak, anything less a gap. */
  readonly nav: readonly string[];
  /** Live endpoints this role may read with `200`. */
  readonly liveReads: readonly string[];
  /** One-line description of the rendered synthetic data this role must show. */
  readonly dataCase: string;
}

/**
 * Sidebar sets are derived from `NAV_MANIFEST` + `contracts/fixtures/access.json`
 * for a fully `read_only` capability map in live mode. Hardcoded on purpose:
 * these sets are the assertion, not a re-derivation of the code under test.
 */
export function roleCases(runtime: MarleyRuntime): RoleCase[] {
  const photos = livePhotos(runtime.live.patientReference);
  return [
    {
      role: "DOC",
      account: runtime.accounts.doctor,
      nav: [
        PAGE.home,
        "/clinic/doctor",
        "/clinic/appointments",
        PAGE.patients,
        PAGE.search,
        PAGE.profile,
      ],
      liveReads: [API.livePatients, photos, API.liveAppointments],
      dataCase: "lab patient + saved photo bytes + disabled save controls",
    },
    {
      role: "NUR",
      account: runtime.accounts.nurse,
      nav: [
        PAGE.home,
        "/clinic/nurse",
        "/clinic/nurse/tasks",
        "/clinic/reception/queue",
        PAGE.patients,
        PAGE.search,
        PAGE.profile,
      ],
      liveReads: [API.livePatients, photos],
      dataCase: "lab patient + saved photo bytes, no save region",
    },
    {
      role: "REC",
      account: runtime.accounts.reception,
      nav: [
        PAGE.home,
        "/clinic/reception",
        "/clinic/reception/queue",
        "/clinic/appointments",
        PAGE.patients,
        "/clinic/reception/checkout",
        PAGE.search,
        PAGE.profile,
      ],
      liveReads: [API.livePatients, API.liveAppointments],
      dataCase: "live patient directory row",
    },
    {
      role: "PHA",
      account: runtime.accounts.pharmacy,
      nav: [
        PAGE.home,
        PAGE.pharmacy,
        "/clinic/pharmacy/items",
        "/clinic/pharmacy/batches",
        "/clinic/pharmacy/receive",
        "/clinic/pharmacy/balances",
        "/clinic/pharmacy/movements",
        "/clinic/pharmacy/purchasing",
        PAGE.search,
        PAGE.profile,
      ],
      liveReads: [API.liveStock],
      dataCase: "allow-listed synthetic item row",
    },
    {
      role: "CAS",
      account: runtime.accounts.cashier,
      nav: [
        PAGE.home,
        PAGE.cashier,
        "/clinic/invoices",
        "/clinic/payments",
        "/clinic/reception/checkout",
        PAGE.search,
        PAGE.profile,
      ],
      liveReads: [API.liveFinance],
      dataCase: "folded finance link lands on the cashier surface",
    },
    {
      role: "MGR",
      account: runtime.accounts.owner,
      nav: [
        PAGE.home,
        PAGE.management,
        "/clinic/management/reports",
        "/clinic/invoices",
        "/clinic/payments",
        "/clinic/pharmacy/batches",
        "/clinic/pharmacy/balances",
        PAGE.system,
        PAGE.search,
        PAGE.profile,
      ],
      liveReads: [API.liveFinance],
      dataCase: "folded finance link lands on the management surface + audit",
    },
    {
      role: "ADM",
      account: runtime.accounts.admin,
      nav: [
        PAGE.home,
        "/clinic/admin",
        "/clinic/admin/users",
        "/clinic/admin/clinical",
        "/clinic/admin/settings",
        "/clinic/admin/system",
        PAGE.system,
        PAGE.patients,
        PAGE.search,
        PAGE.profile,
      ],
      liveReads: [],
      dataCase: "audit/status surface only, no live clinic data",
    },
  ];
}

/** Live endpoints a role must be refused: every other role's live surface. */
export function deniedLiveReads(
  item: RoleCase,
  cases: readonly RoleCase[],
): string[] {
  const own = new Set(item.liveReads);
  const all = new Set<string>();
  for (const other of cases)
    for (const path of other.liveReads) if (!own.has(path)) all.add(path);
  return [...all];
}
