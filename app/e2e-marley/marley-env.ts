import { readFileSync, statSync } from "node:fs";
import { isAbsolute, join } from "node:path";

/**
 * Private runtime input for the Marley role matrix.
 *
 * Every value comes from the operator's private `.runtime` directory named by
 * `MARLEY_RUNTIME_DIR`: synthetic credentials, the synthetic patient, the
 * allow-listed item and invoice, and the native Frappe role names that the
 * proxy maps onto UI roles. Nothing here is generated, guessed or defaulted:
 * a missing or malformed file stops the run before a browser opens.
 *
 * No secret or record value is ever logged, echoed or embedded in a test
 * title. Errors name the file or the schema field only.
 */

export const MARLEY_SITE = "marley.local";

/** The only upstream these tests may reach: an existing SSH loopback tunnel. */
export const MARLEY_UPSTREAM_ORIGIN = "http://127.0.0.1:18080";

export const RUNTIME_DIR_ENV = "MARLEY_RUNTIME_DIR";
export const RESULTS_DIR_ENV = "MARLEY_RESULTS_DIR";

export interface MarleyAccount {
  readonly user: string;
  readonly password: string;
}

/**
 * Native Frappe role names, one per account, chosen so that exactly one proxy
 * role category matches each login (the proxy refuses a login whose native
 * roles span more than one category).
 */
export interface NativeRoles {
  readonly reception: string;
  readonly pharmacy: string;
  readonly owner: string;
  readonly doctor: string;
  readonly nurse: string;
  readonly cashier: string;
  readonly admin: string;
}

export interface LiveExpectations {
  /** Workspace reference and native `Patient` name for the same synthetic row. */
  readonly patientReference: string;
  readonly itemCode: string;
  readonly invoiceName: string;
  readonly invoiceCount: number;
  readonly invoiced: number;
  readonly outstanding: number;
  readonly currency: string;
}

export interface MarleyRuntime {
  readonly accounts: {
    readonly reception: MarleyAccount;
    readonly pharmacy: MarleyAccount;
    readonly owner: MarleyAccount;
    readonly doctor: MarleyAccount;
    readonly nurse: MarleyAccount;
    readonly cashier: MarleyAccount;
    readonly admin: MarleyAccount;
  };
  readonly nativeRoles: NativeRoles;
  readonly live: LiveExpectations;
}

function fail(message: string): never {
  throw new Error(`MARLEY_PRIVATE_INPUT: ${message}`);
}

function privateFile(dir: string, relative: string): string {
  const path = join(dir, relative);
  let stats: ReturnType<typeof statSync>;
  try {
    stats = statSync(path);
  } catch {
    return fail(`${relative} is missing from the private runtime directory`);
  }
  if (!stats.isFile()) return fail(`${relative} is not a regular file`);
  // Group/world readable input would break the private-file requirement.
  if ((stats.mode & 0o077) !== 0)
    return fail(`${relative} must not be group or world accessible`);
  return path;
}

function readJson(dir: string, relative: string): Record<string, unknown> {
  const raw = readFileSync(privateFile(dir, relative), "utf8");
  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch {
    return fail(`${relative} is not valid JSON`);
  }
  if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed))
    return fail(`${relative} must contain a JSON object`);
  return parsed as Record<string, unknown>;
}

function record(value: unknown, what: string): Record<string, unknown> {
  if (typeof value !== "object" || value === null || Array.isArray(value))
    return fail(`${what} must be an object`);
  return value as Record<string, unknown>;
}

function text(value: unknown, what: string): string {
  if (typeof value !== "string" || value.trim() === "")
    return fail(`${what} must be a non-empty string`);
  return value;
}

function count(value: unknown, what: string): number {
  if (typeof value !== "number" || !Number.isFinite(value) || value < 0)
    return fail(`${what} must be a non-negative finite number`);
  return value;
}

function expectSite(value: unknown, what: string): void {
  if (value !== MARLEY_SITE) fail(`${what}.site must be ${MARLEY_SITE}`);
}

function runtimeDir(): string {
  const dir = process.env[RUNTIME_DIR_ENV];
  if (dir === undefined || dir.trim() === "")
    return fail(`${RUNTIME_DIR_ENV} must name the private .runtime directory`);
  if (!isAbsolute(dir))
    return fail(`${RUNTIME_DIR_ENV} must be an absolute path`);
  let stats: ReturnType<typeof statSync>;
  try {
    stats = statSync(dir);
  } catch {
    return fail(`${RUNTIME_DIR_ENV} does not exist`);
  }
  if (!stats.isDirectory())
    return fail(`${RUNTIME_DIR_ENV} must be a directory`);
  if ((stats.mode & 0o077) !== 0)
    return fail(`${RUNTIME_DIR_ENV} must be mode 700 or stricter`);
  return dir;
}

function account(raw: Record<string, unknown>, what: string): MarleyAccount {
  return {
    user: text(raw.user, `${what}.user`),
    password: text(raw.password, `${what}.password`),
  };
}

/**
 * Loads and cross-checks the private inputs. Deliberately strict: the matrix
 * claims to prove a specific site, patient, item and invoice, so a mismatch
 * must fail before any browser context exists.
 */
export function loadRuntime(): MarleyRuntime {
  const dir = runtimeDir();

  const credentials = readJson(dir, "roles/credentials.json");
  expectSite(credentials.site, "roles/credentials.json");
  const accounts = record(
    credentials.accounts,
    "roles/credentials.json accounts",
  );

  function dcAccount(name: string): MarleyAccount {
    const raw = accounts[name];
    if (raw === undefined)
      return fail(`roles/credentials.json has no ${name} account`);
    const entry = record(raw, `roles/credentials.json ${name}`);
    if (text(entry.name, `${name}.name`) !== name)
      return fail(`roles/credentials.json ${name}.name must equal the account`);
    return account(
      { user: name, password: entry.password },
      `roles/credentials.json ${name}`,
    );
  }

  const synthetic = readJson(dir, "roles/marley_remaining_synthetic.json");
  expectSite(synthetic.site, "roles/marley_remaining_synthetic.json");
  if (!Array.isArray(synthetic.accounts))
    return fail(
      "roles/marley_remaining_synthetic.json accounts must be an array",
    );

  const syntheticByKind = new Map<string, Record<string, unknown>>();
  for (const raw of synthetic.accounts) {
    const entry = record(raw, "roles/marley_remaining_synthetic.json account");
    syntheticByKind.set(text(entry.kind, "synthetic account kind"), entry);
  }

  function syntheticAccount(kind: string): {
    account: MarleyAccount;
    marker: string;
    extra: string | null;
  } {
    const entry = syntheticByKind.get(kind);
    if (entry === undefined)
      return fail(
        `roles/marley_remaining_synthetic.json has no ${kind} account`,
      );
    const extra = entry.extra;
    return {
      account: account(entry, `synthetic ${kind}`),
      marker: text(entry.marker, `synthetic ${kind}.marker`),
      extra: typeof extra === "string" && extra.trim() !== "" ? extra : null,
    };
  }

  const nurse = syntheticAccount("nur");
  const cashier = syntheticAccount("cas");
  const admin = syntheticAccount("adm");

  const item = readJson(dir, "qa/item_read_pharmacy.json");
  expectSite(item.site, "qa/item_read_pharmacy.json");
  // The probe file keeps field *shapes*, so the allow-listed item name is the
  // only concrete value it carries; the proxy allow-list must match it exactly.
  record(item.fields, "qa/item_read_pharmacy.json fields");
  const itemCode = text(
    item.item_name_allowed,
    "qa/item_read_pharmacy.json item_name_allowed",
  );
  if (!itemCode.startsWith("DEMO-"))
    return fail(
      "qa/item_read_pharmacy.json item_name_allowed must be a DEMO- item",
    );
  if (item.read_only !== true)
    return fail("qa/item_read_pharmacy.json must be a read-only verification");

  const invoice = readJson(dir, "qa/synth_invoice_verify_20261113.json");
  const observed = record(invoice.observed, "qa/synth_invoice_verify observed");
  const invoiceName = text(invoice.docname, "qa/synth_invoice_verify docname");
  if (invoice.read_only !== true)
    return fail("qa/synth_invoice_verify must be a read-only verification");
  if (observed.docstatus !== 1)
    return fail(
      "qa/synth_invoice_verify observed.docstatus must be 1 (submitted)",
    );

  const created = readJson(dir, "ui_e2e_20261113/SYNTHETIC-CREATED.json");
  if (created.synthetic !== true)
    return fail(
      "ui_e2e_20261113/SYNTHETIC-CREATED.json is not marked synthetic",
    );
  if (created.doctype !== "Patient")
    return fail("ui_e2e_20261113/SYNTHETIC-CREATED.json must be a Patient");
  const patientReference = text(created.name, "SYNTHETIC-CREATED.name");

  const live: LiveExpectations = {
    patientReference,
    itemCode,
    invoiceName,
    // The proxy allow-list below holds exactly one invoice name.
    invoiceCount: 1,
    invoiced: count(observed.grand_total, "synth_invoice grand_total"),
    outstanding: count(
      observed.outstanding_amount,
      "synth_invoice outstanding_amount",
    ),
    currency: text(observed.currency, "synth_invoice currency"),
  };
  if (live.currency !== "THB")
    return fail("synth_invoice currency must be THB");

  const nativeRoles: NativeRoles = {
    // Custom Marley roles assigned to the four DC accounts (one category each).
    reception: "DC Receptionist",
    pharmacy: "DC Pharmacy",
    owner: "DC Owner",
    doctor: "Physician",
    // qa.nur also holds standard `Nursing User`, which is shared with the
    // reception and owner accounts, so only its unique marker role is used.
    nurse: nurse.marker,
    // qa.cas holds `Accounts User`, held by no other account in the matrix.
    cashier: cashier.extra ?? cashier.marker,
    // qa.adm carries no standard role; its marker role is the only one.
    admin: admin.marker,
  };
  const roleValues = Object.values(nativeRoles);
  if (new Set(roleValues).size !== roleValues.length)
    return fail("native role allow-list entries must be distinct");
  if (nativeRoles.cashier !== "Accounts User")
    return fail("qa.cas must map through its Accounts User role");

  return {
    accounts: {
      reception: dcAccount("dc.reception@example.invalid"),
      pharmacy: dcAccount("dc.pharmacy@example.invalid"),
      owner: dcAccount("dc.owner@example.invalid"),
      doctor: dcAccount("dc.doctor@example.invalid"),
      nurse: nurse.account,
      cashier: cashier.account,
      admin: admin.account,
    },
    nativeRoles,
    live,
  };
}

/**
 * Proxy environment. Writes stay off on purpose: `CLINIC_LIVE_PHOTO_WRITES`
 * and `CLINIC_ACTIVE_WRITES` are never set, so every write capability is
 * `read_only` and publication stays disabled for the whole run.
 */
export function proxyEnv(
  runtime: MarleyRuntime,
  appOrigin: string,
  port: number,
  databasePath: string,
): Record<string, string> {
  const { live, nativeRoles } = runtime;
  return {
    HOST: "127.0.0.1",
    PORT: String(port),
    APP_ORIGIN: appOrigin,
    UPSTREAM_MODE: "live",
    CLINIC_LIVE_LAB: "1",
    FRAPPE_ORIGIN: MARLEY_UPSTREAM_ORIGIN,
    CLINIC_SYNTHETIC_PATIENTS: `${live.patientReference}=${live.patientReference}`,
    CLINIC_SYNTHETIC_ITEMS: live.itemCode,
    CLINIC_SYNTHETIC_INVOICES: live.invoiceName,
    CLINIC_LIVE_CLINICIAN_ROLES: nativeRoles.doctor,
    CLINIC_LIVE_RECEPTION_ROLES: nativeRoles.reception,
    CLINIC_LIVE_PHARMACY_ROLES: nativeRoles.pharmacy,
    CLINIC_LIVE_MANAGER_ROLES: nativeRoles.owner,
    CLINIC_LIVE_NURSE_ROLES: nativeRoles.nurse,
    CLINIC_LIVE_CASHIER_ROLES: nativeRoles.cashier,
    CLINIC_LIVE_ADMIN_ROLES: nativeRoles.admin,
    DATABASE_PATH: databasePath,
  };
}
