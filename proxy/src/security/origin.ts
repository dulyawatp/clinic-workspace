import type { FastifyRequest } from "fastify";

/**
 * Parses an origin, but only for real HTTP(S) origins. `file:`, `data:` and
 * other opaque schemes serialise their origin as the string "null", which must
 * never become an allow-list entry.
 */
function parseHttpOrigin(value: string): string | null {
  let url: URL;
  try {
    url = new URL(value);
  } catch {
    return null;
  }

  if (url.protocol !== "http:" && url.protocol !== "https:") {
    return null;
  }

  if (url.origin === "null" || url.username !== "" || url.password !== "") {
    return null;
  }

  return url.origin;
}

/**
 * Canonical serialised origin, or `null` for anything that is not a concrete
 * credential-free HTTP(S) origin.
 */
export function normalizeHttpOrigin(value: string): string | null {
  return parseHttpOrigin(value);
}

/** Configured `APP_ORIGIN` must be a concrete HTTP(S) origin. */
export function isValidAppOrigin(appOrigin: string): boolean {
  return normalizeHttpOrigin(appOrigin) !== null;
}

function firstHeader(value: string | string[] | undefined): string | undefined {
  return Array.isArray(value) ? value[0] : value;
}

/**
 * Browser-origin allow-list for state-changing requests.
 *
 * The configured origin must parse to a concrete HTTP(S) origin. A present
 * `Origin` must equal it exactly (no path, trailing slash, casing, `null` or
 * default-port rewriting). `Referer` is only consulted when `Origin` is
 * absent, and then its parsed origin must match exactly. Anything else fails
 * closed.
 */
export function assertTrustedOrigin(
  req: FastifyRequest,
  appOrigin: string,
): boolean {
  const expected = parseHttpOrigin(appOrigin);
  if (expected === null) {
    return false;
  }

  const origin = firstHeader(req.headers.origin);
  if (origin !== undefined) {
    return origin === expected;
  }

  const referer = firstHeader(req.headers.referer);
  if (referer === undefined) {
    return false;
  }

  return parseHttpOrigin(referer) === expected;
}
