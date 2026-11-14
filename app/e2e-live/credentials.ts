import { readFileSync, statSync } from "node:fs";
import { isAbsolute } from "node:path";

/**
 * Live credential loading. Values live only in a private JSON file named by an
 * environment variable; they are never logged, attached, asserted on, or copied
 * into a test title. Every error below names the variable or the schema rule and
 * never the path contents.
 */
export const LIVE_SITE = "clinic-photo-accept.local";

export const PRIMARY_CREDENTIAL_ENV = "CLINIC_LIVE_CREDENTIAL_FILE";
export const SECONDARY_CREDENTIAL_ENV = "CLINIC_LIVE_SECONDARY_CREDENTIAL_FILE";

export interface LiveCredential {
  site: string;
  user: string;
  password: string;
}

function fail(message: string): never {
  throw new Error(message);
}

function parseCredential(envName: string, raw: string): LiveCredential {
  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch {
    return fail(`${envName} is not valid JSON`);
  }
  if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed))
    return fail(`${envName} must contain a JSON object`);
  const { site, user, password } = parsed as Record<string, unknown>;
  if (typeof site !== "string" || site.trim() === "")
    return fail(`${envName} is missing a site string`);
  if (typeof user !== "string" || user.trim() === "")
    return fail(`${envName} is missing a user string`);
  if (typeof password !== "string" || password === "")
    return fail(`${envName} is missing a password string`);
  if (site !== LIVE_SITE)
    return fail(`${envName} must target the exact site ${LIVE_SITE}`);
  return { site, user, password };
}

/** Returns null when the variable is unset; throws on a malformed private file. */
function loadCredential(envName: string): LiveCredential | null {
  const path = process.env[envName];
  if (path === undefined || path.trim() === "") return null;
  if (!isAbsolute(path))
    return fail(`${envName} must be an absolute path to a private file`);
  const stats = statSync(path);
  if (!stats.isFile()) return fail(`${envName} must point at a regular file`);
  // Group/world readability would break the private-file requirement.
  if ((stats.mode & 0o077) !== 0)
    return fail(`${envName} must not be group or world accessible`);
  return parseCredential(envName, readFileSync(path, "utf8"));
}

let primary: LiveCredential | null | undefined;
let secondary: LiveCredential | null | undefined;

/** Synthetic physician (native clinician role) used for the browse scenario. */
export function primaryCredential(): LiveCredential | null {
  primary ??= loadCredential(PRIMARY_CREDENTIAL_ENV);
  return primary;
}

/**
 * Optional non-clinician account. Coverage is skipped outright when it is not
 * supplied: an absent credential is never reported as a passing denial test.
 */
export function secondaryCredential(): LiveCredential | null {
  secondary ??= loadCredential(SECONDARY_CREDENTIAL_ENV);
  return secondary;
}

/** Skip reasons carry variable names only, never paths or values. */
export const MISSING_PRIMARY_REASON = `${PRIMARY_CREDENTIAL_ENV} is not set; the live browse scenario did not run`;
export const MISSING_SECONDARY_REASON = `${SECONDARY_CREDENTIAL_ENV} is not set; non-clinician rejection is NOT covered by this run`;
