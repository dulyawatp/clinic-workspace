import { normalizeHttpOrigin } from "./security/origin.js";

export type UpstreamMode = "fixture" | "live";

export interface LiveUpstream {
  /** Canonical credential-free HTTP(S) origin of the approved lab site. */
  origin: string;
  timeoutMs: number;
  /**
   * Server-owned allow-list from a workspace patient reference to the native
   * Frappe `Patient` name. A client value is only ever a lookup key here; the
   * forwarded native name always comes from this map and is re-checked against
   * the live site with the clinician's own session.
   */
  syntheticPatients: ReadonlyMap<string, string>;
  /**
   * Server-owned allow-list of native `Patient Appointment` docnames. A row is
   * only ever returned when its native patient also appears in
   * `syntheticPatients`; the list is never derived from client input and never
   * used to look up anything else.
   */
  syntheticAppointments?: ReadonlySet<string>;
  /**
   * Native Frappe roles that qualify as an authorized live clinician for this
   * lab. A login with no configured role is refused; role names never come
   * from the browser.
   */
  clinicianRoles: ReadonlySet<string>;
  /** Explicit native role names mapped to the reception UI role. */
  receptionRoles?: ReadonlySet<string>;
  pharmacyRoles?: ReadonlySet<string>;
  syntheticItems?: ReadonlySet<string>;
  managerRoles?: ReadonlySet<string>;
  nurseRoles?: ReadonlySet<string>;
  cashierRoles?: ReadonlySet<string>;
  adminRoles?: ReadonlySet<string>;
  syntheticInvoices?: ReadonlySet<string>;
  /**
   * Server-owned native `Appointment Type` used for every synthetic encounter
   * draft. Never supplied by the browser.
   */
  encounterAppointmentType?: string;
  /**
   * Server-owned allow-list from the calling Frappe user identity to the
   * operator-controlled native `Practitioner` docname for that clinician. A
   * request is refused when the caller has no entry or the native read does not
   * return that exact, active record. No alternate practitioner is tried.
   */
  encounterPractitioners?: ReadonlyMap<string, string>;
}

export interface AppConfig {
  appOrigin: string;
  databasePath: string;
  activeWrites: ReadonlySet<string>;
  upstreamMode: UpstreamMode;
  /**
   * Explicit lab opt-in for native photo writes. Absent or false keeps even a
   * live lab browse-only: the capability stays read_only and the write routes
   * refuse before any body is parsed.
   */
  livePhotoWrites?: boolean;
  /**
   * Explicit lab opt-in for native patient registration. Absent or false keeps
   * the capability read_only and the create route refuses before any body is
   * parsed. Separate from the photo opt-in on purpose.
   */
  livePatientWrites?: boolean;
  /**
   * Explicit lab opt-in for synthetic encounter draft creation. Absent or false
   * keeps the capability read_only and the draft route refuses before any body
   * is parsed. Separate from the patient and photo opt-ins on purpose.
   */
  liveEncounterWrites?: boolean;
  live?: LiveUpstream;
}

const DEFAULT_APP_ORIGIN = "http://localhost:5173";
const DEFAULT_DATABASE_PATH = "data/clinic.sqlite";
const DEFAULT_UPSTREAM_TIMEOUT_MS = 15_000;

const WORKSPACE_REFERENCE = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,139}$/;
// Native Patient docnames may be human names with single spaces. They are never
// used as a path without percent-encoding and never accepted from a client.
const NATIVE_PATIENT_NAME = /^[A-Za-z0-9][A-Za-z0-9 ._:-]{0,139}$/;

/** A workspace reference must be one safe path segment, never traversal. */
export function isSafePatientReference(value: string): boolean {
  return WORKSPACE_REFERENCE.test(value) && !value.includes("..");
}

export function isSafeNativePatientName(value: string): boolean {
  return (
    NATIVE_PATIENT_NAME.test(value) &&
    !value.includes("..") &&
    !value.includes("  ")
  );
}

function isLoopbackHost(hostname: string): boolean {
  const host = hostname.replace(/^\[|\]$/g, "").toLowerCase();
  return host === "localhost" || host === "127.0.0.1" || host === "::1";
}

/** Exact HTTP(S) origin; plain HTTP is accepted only for loopback labs. */
export function isSafeLiveOrigin(origin: string): boolean {
  const normalized = normalizeHttpOrigin(origin);
  if (normalized === null || normalized !== origin) {
    return false;
  }
  const parsed = new URL(origin);
  return parsed.protocol === "https:" || isLoopbackHost(parsed.hostname);
}

function parseActiveWrites(raw: string | undefined): ReadonlySet<string> {
  if (raw === undefined) {
    return new Set();
  }

  return new Set(
    raw
      .split(",")
      .map((entry) => entry.trim())
      .filter((entry) => entry.length > 0),
  );
}

/**
 * `CLINIC_LIVE_PHOTO_WRITES=1` / `CLINIC_LIVE_PATIENT_WRITES=1` (or `true`) are
 * the only ways to enable those native writes. Everything else - including a
 * missing value - stays read-only, and each opt-in only applies in live mode.
 */
function parseLiveOptIn(raw: string | undefined, mode: UpstreamMode): boolean {
  if (mode !== "live") {
    return false;
  }
  const value = raw?.trim().toLowerCase();
  return value === "1" || value === "true";
}

function parseUpstreamMode(raw: string | undefined): UpstreamMode {
  const value = raw?.trim() || "fixture";

  if (value === "fixture" || value === "live") {
    return value;
  }

  throw new Error(`Invalid UPSTREAM_MODE: expected "fixture" or "live"`);
}

function parseSyntheticPatients(
  raw: string | undefined,
): ReadonlyMap<string, string> {
  const mapping = new Map<string, string>();
  if (raw === undefined || raw.trim() === "") {
    return mapping;
  }

  for (const entry of raw.split(",")) {
    const [reference, nativeName, ...rest] = entry
      .split("=")
      .map((part) => part.trim());
    if (
      reference === undefined ||
      nativeName === undefined ||
      rest.length > 0 ||
      !isSafePatientReference(reference) ||
      !isSafeNativePatientName(nativeName) ||
      mapping.has(reference)
    ) {
      throw new Error(
        "INVALID_SYNTHETIC_PATIENTS: expected unique reference=nativePatient entries",
      );
    }
    mapping.set(reference, nativeName);
  }

  return mapping;
}

function parseSyntheticAppointments(
  raw: string | undefined,
): ReadonlySet<string> {
  const names = new Set<string>();
  for (const entry of (raw ?? "").split(",")) {
    const name = entry.trim();
    if (name === "") {
      continue;
    }
    // Native `Patient Appointment` docnames use the same safe character set as
    // native Patient names and are only ever a percent-encoded path segment.
    if (!isSafeNativePatientName(name)) {
      throw new Error("INVALID_SYNTHETIC_APPOINTMENTS");
    }
    names.add(name);
  }
  return names;
}

const NATIVE_USER_NAME = /^[A-Za-z0-9][A-Za-z0-9._:@-]{0,139}$/;

/** Native Frappe user identity (usually an email); only ever a mapping key. */
function isSafeNativeUserName(value: string): boolean {
  return NATIVE_USER_NAME.test(value) && !value.includes("..");
}

/**
 * A native `Appointment Type` docname. Never a client value: it is read once
 * from configuration and forwarded verbatim, or the draft route fails closed.
 */
function parseEncounterAppointmentType(
  raw: string | undefined,
): string | undefined {
  const value = raw?.trim();
  if (value === undefined || value === "") {
    return undefined;
  }
  if (!isSafeNativePatientName(value)) {
    throw new Error("INVALID_SYNTHETIC_ENCOUNTER_APPOINTMENT_TYPE");
  }
  return value;
}

function parseEncounterPractitioners(
  raw: string | undefined,
): ReadonlyMap<string, string> {
  const mapping = new Map<string, string>();
  if (raw === undefined || raw.trim() === "") {
    return mapping;
  }

  for (const entry of raw.split(",")) {
    const [user, practitioner, ...rest] = entry
      .split("=")
      .map((part) => part.trim());
    if (
      user === undefined ||
      practitioner === undefined ||
      rest.length > 0 ||
      !isSafeNativeUserName(user) ||
      !isSafeNativePatientName(practitioner) ||
      mapping.has(user)
    ) {
      throw new Error(
        "INVALID_SYNTHETIC_ENCOUNTER_PRACTITIONERS: expected unique user=practitioner entries",
      );
    }
    mapping.set(user, practitioner);
  }

  return mapping;
}

function parseRoleNames(raw: string | undefined): ReadonlySet<string> {
  const roles = new Set<string>();
  if (raw === undefined) {
    return roles;
  }
  for (const entry of raw.split(",")) {
    const role = entry.trim();
    if (role === "") {
      continue;
    }
    if (role.length > 140 || /[\r\n]/.test(role)) {
      throw new Error("INVALID_LIVE_CLINICIAN_ROLES");
    }
    roles.add(role);
  }
  return roles;
}

function parseLiveUpstream(
  env: NodeJS.ProcessEnv,
  mode: UpstreamMode,
): LiveUpstream | undefined {
  if (mode !== "live") {
    return undefined;
  }

  // Explicit lab opt-in: `UPSTREAM_MODE=live` alone must never reach a real site.
  const optIn = env.CLINIC_LIVE_LAB?.trim().toLowerCase();
  if (optIn !== "1" && optIn !== "true") {
    throw new Error(
      "LIVE_LAB_NOT_ENABLED: live mode requires CLINIC_LIVE_LAB=1 for the approved lab",
    );
  }

  const rawOrigin = env.FRAPPE_ORIGIN?.trim();
  if (rawOrigin === undefined || rawOrigin === "") {
    throw new Error("MISSING_FRAPPE_ORIGIN: live mode requires FRAPPE_ORIGIN");
  }

  const origin = normalizeHttpOrigin(rawOrigin);
  if (origin === null || origin !== rawOrigin || !isSafeLiveOrigin(origin)) {
    throw new Error(
      "INSECURE_FRAPPE_ORIGIN: FRAPPE_ORIGIN must be an exact https origin, or loopback http",
    );
  }

  const syntheticPatients = parseSyntheticPatients(
    env.CLINIC_SYNTHETIC_PATIENTS,
  );
  if (syntheticPatients.size === 0) {
    throw new Error(
      "MISSING_SYNTHETIC_PATIENTS: live mode requires an explicit synthetic patient allow-list",
    );
  }

  const clinicianRoles = parseRoleNames(env.CLINIC_LIVE_CLINICIAN_ROLES);
  const syntheticAppointments = parseSyntheticAppointments(
    env.CLINIC_SYNTHETIC_APPOINTMENTS,
  );
  const receptionRoles = parseRoleNames(env.CLINIC_LIVE_RECEPTION_ROLES);
  const pharmacyRoles = parseRoleNames(env.CLINIC_LIVE_PHARMACY_ROLES);
  const managerRoles = parseRoleNames(env.CLINIC_LIVE_MANAGER_ROLES);
  const nurseRoles = parseRoleNames(env.CLINIC_LIVE_NURSE_ROLES);
  const cashierRoles = parseRoleNames(env.CLINIC_LIVE_CASHIER_ROLES);
  const adminRoles = parseRoleNames(env.CLINIC_LIVE_ADMIN_ROLES);
  const syntheticInvoices = new Set(
    (env.CLINIC_SYNTHETIC_INVOICES ?? "")
      .split(",")
      .map((name) => name.trim())
      .filter(Boolean),
  );
  if ([...syntheticInvoices].some((name) => !isSafeNativePatientName(name)))
    throw new Error("INVALID_SYNTHETIC_INVOICES");
  if (
    (managerRoles.size > 0 || cashierRoles.size > 0) &&
    syntheticInvoices.size === 0
  )
    throw new Error("MISSING_SYNTHETIC_INVOICES");
  const syntheticItems = new Set(
    (env.CLINIC_SYNTHETIC_ITEMS ?? "")
      .split(",")
      .map((item) => item.trim())
      .filter(Boolean),
  );
  if (
    [...syntheticItems].some(
      (item) => !item.startsWith("DEMO-") || !isSafeNativePatientName(item),
    )
  ) {
    throw new Error("INVALID_SYNTHETIC_ITEMS");
  }
  if (pharmacyRoles.size > 0 && syntheticItems.size === 0) {
    throw new Error("MISSING_SYNTHETIC_ITEMS");
  }
  const encounterAppointmentType = parseEncounterAppointmentType(
    env.CLINIC_SYNTHETIC_ENCOUNTER_APPOINTMENT_TYPE,
  );
  const encounterPractitioners = parseEncounterPractitioners(
    env.CLINIC_SYNTHETIC_ENCOUNTER_PRACTITIONERS,
  );
  // The draft route cannot build a valid native encounter without both, so the
  // opt-in is refused at startup rather than failing every request later.
  if (
    parseLiveOptIn(env.CLINIC_LIVE_ENCOUNTER_WRITES, mode) &&
    (encounterAppointmentType === undefined ||
      encounterPractitioners.size === 0)
  ) {
    throw new Error("MISSING_SYNTHETIC_ENCOUNTER_CONFIG");
  }
  const broadRoles = new Set([
    "administrator",
    "system manager",
    "all",
    "guest",
    "desk user",
  ]);
  if (
    [
      ...clinicianRoles,
      ...receptionRoles,
      ...pharmacyRoles,
      ...managerRoles,
      ...nurseRoles,
      ...cashierRoles,
      ...adminRoles,
    ].some((role) => broadRoles.has(role.toLowerCase())) ||
    [...receptionRoles].some(
      (role) => clinicianRoles.has(role) || pharmacyRoles.has(role),
    ) ||
    [...pharmacyRoles].some(
      (role) => clinicianRoles.has(role) || managerRoles.has(role),
    ) ||
    [...managerRoles].some(
      (role) => clinicianRoles.has(role) || receptionRoles.has(role),
    ) ||
    [...nurseRoles].some(
      (role) =>
        clinicianRoles.has(role) ||
        receptionRoles.has(role) ||
        pharmacyRoles.has(role) ||
        managerRoles.has(role),
    ) ||
    [...cashierRoles].some(
      (role) =>
        clinicianRoles.has(role) ||
        receptionRoles.has(role) ||
        pharmacyRoles.has(role) ||
        managerRoles.has(role) ||
        nurseRoles.has(role),
    ) ||
    [...adminRoles].some(
      (role) =>
        clinicianRoles.has(role) ||
        receptionRoles.has(role) ||
        pharmacyRoles.has(role) ||
        managerRoles.has(role) ||
        nurseRoles.has(role) ||
        cashierRoles.has(role),
    )
  ) {
    throw new Error("INVALID_LIVE_ROLE_MAP");
  }
  if (clinicianRoles.size === 0) {
    throw new Error(
      "MISSING_LIVE_CLINICIAN_ROLES: live mode requires explicit Frappe clinician roles",
    );
  }

  return {
    origin,
    timeoutMs: DEFAULT_UPSTREAM_TIMEOUT_MS,
    syntheticPatients,
    syntheticAppointments,
    clinicianRoles,
    receptionRoles,
    pharmacyRoles,
    syntheticItems,
    managerRoles,
    nurseRoles,
    cashierRoles,
    adminRoles,
    syntheticInvoices,
    encounterAppointmentType,
    encounterPractitioners,
  };
}

export function loadConfig(env: NodeJS.ProcessEnv = process.env): AppConfig {
  const upstreamMode = parseUpstreamMode(env.UPSTREAM_MODE);
  const live = parseLiveUpstream(env, upstreamMode);

  return {
    appOrigin: env.APP_ORIGIN?.trim() || DEFAULT_APP_ORIGIN,
    databasePath: env.DATABASE_PATH?.trim() || DEFAULT_DATABASE_PATH,
    activeWrites: parseActiveWrites(env.CLINIC_ACTIVE_WRITES),
    upstreamMode,
    livePhotoWrites: parseLiveOptIn(env.CLINIC_LIVE_PHOTO_WRITES, upstreamMode),
    livePatientWrites: parseLiveOptIn(
      env.CLINIC_LIVE_PATIENT_WRITES,
      upstreamMode,
    ),
    liveEncounterWrites: parseLiveOptIn(
      env.CLINIC_LIVE_ENCOUNTER_WRITES,
      upstreamMode,
    ),
    ...(live === undefined ? {} : { live }),
  };
}
