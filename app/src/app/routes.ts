/**
 * Canonical client route paths, aligned to the design surface manifest
 * (`handoff/clinic-surface-manifest.json`). Later plans extend this object;
 * tests and features must read these constants instead of hardcoding paths.
 *
 * Builders (`patient`, `appointment`, …) are not static paths: a record
 * destination always carries the record id the server returned and is never
 * assembled by string concatenation at a call site. `*Pattern` entries are the
 * matching dynamic route paths.
 *
 * `legacy*` entries are React-era addresses with no design counterpart. They
 * stay only as redirects for existing deep links.
 */
export const ROUTES = {
  login: "/clinic/login",
  /** APP-02: application shell, role-home dispatch and profile. */
  home: "/clinic",
  search: "/clinic/search",
  system: "/clinic/system",
  profile: "/clinic/profile",

  patients: "/clinic/patients",
  patientNew: "/clinic/patients/new",
  patientPattern: "/clinic/patients/:id",
  patient: (id: string) => `/clinic/patients/${encodeURIComponent(id)}`,
  patientHistory: (id: string): string => `${ROUTES.patient(id)}?view=history`,

  photoPattern: "/clinic/patients/:id/photos",
  photoComparePattern: "/clinic/patients/:id/photos/compare",
  photos: (id: string) => `/clinic/patients/${encodeURIComponent(id)}/photos`,
  photoCompare: (id: string) =>
    `/clinic/patients/${encodeURIComponent(id)}/photos/compare`,
  /** PAT-04/05 entry to the synthetic lab-photo workspace. */
  livePhotosPatientPattern: "/clinic/patients/:id/live-photos",
  livePhotosPatient: (id: string) =>
    `/clinic/patients/${encodeURIComponent(id)}/live-photos`,

  reception: "/clinic/reception",
  queue: "/clinic/reception/queue",
  checkout: "/clinic/reception/checkout",

  appointments: "/clinic/appointments",
  appointmentPattern: "/clinic/appointments/:id",
  appointment: (id: string) =>
    `/clinic/appointments/${encodeURIComponent(id)}`,

  doctor: "/clinic/doctor",
  visitPattern: "/clinic/visits/:id",
  visit: (id: string) => `/clinic/visits/${encodeURIComponent(id)}`,
  linkedVisit: (appointmentRef: string, encounterId?: string): string => {
    const params = new URLSearchParams({ appointment_ref: appointmentRef });
    if (encounterId === undefined) params.set("launch", "1");
    return `${ROUTES.visit(encounterId ?? "appointment")}?${params}`;
  },
  procedurePattern: "/clinic/procedures/:id",
  procedure: (id: string) => `/clinic/procedures/${encodeURIComponent(id)}`,
  labPattern: "/clinic/labs/:id",
  lab: (id: string) => `/clinic/labs/${encodeURIComponent(id)}`,

  nurse: "/clinic/nurse",
  nurseTasks: "/clinic/nurse/tasks",

  pharmacy: "/clinic/pharmacy",
  pharmacyItems: "/clinic/pharmacy/items",
  pharmacyBatches: "/clinic/pharmacy/batches",
  pharmacyBalances: "/clinic/pharmacy/balances",
  pharmacyReceive: "/clinic/pharmacy/receive",
  pharmacyMovements: "/clinic/pharmacy/movements",
  pharmacyPurchasing: "/clinic/pharmacy/purchasing",

  cashier: "/clinic/cashier",
  invoices: "/clinic/invoices",
  payments: "/clinic/payments",

  management: "/clinic/management",
  managementReports: "/clinic/management/reports",

  admin: "/clinic/admin",
  adminUsers: "/clinic/admin/users",
  adminClinical: "/clinic/admin/clinical",
  adminSettings: "/clinic/admin/settings",
  adminSystem: "/clinic/admin/system",

  /** React-era finance surface: folded into cashier/management. */
  legacyFinance: "/clinic/finance",
  /** React-era synthetic lab-photo workspace entry points. */
  livePhotos: "/clinic/lab/photos",
  livePhotoPattern: "/clinic/lab/photos/:id",
  livePhoto: (id: string) => `/clinic/lab/photos/${encodeURIComponent(id)}`,
} as const;

/** Static route keys only: a builder such as `patient` is never a nav target. */
export type StaticRouteKey = {
  [Key in keyof typeof ROUTES]: (typeof ROUTES)[Key] extends string
    ? Key
    : never;
}[keyof typeof ROUTES];
