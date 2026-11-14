export const CSRF_HEADER = "x-clinic-csrf";
export const CSRF_COOKIE = "dc_csrf";
export const SESSION_COOKIE = "dc_session";

/** Login pre-session and its CSRF token both expire after 30 minutes. */
export const PRE_SESSION_TTL_MS = 30 * 60 * 1000;
/** Session CSRF tokens expire after 8 hours, matching the session TTL. */
export const SESSION_TTL_MS = 8 * 60 * 60 * 1000;

/**
 * Bounded issuance for anonymous handshakes. Evaluators can exhaust this with
 * repeated `GET /api/auth/csrf`, so the proxy fails closed with `RATE_LIMITED`
 * instead of growing persisted rows without limit. Expired pre-sessions are
 * purged before the count is taken, so the cap always measures live rows.
 */
export const MAX_PENDING_PRE_SESSIONS = 100;

/**
 * Per-owner cap on unused CSRF tokens. Repeated `GET /api/auth/csrf` for the
 * same pre-session/session rotates out the oldest token instead of appending.
 */
export const MAX_UNUSED_CSRF_TOKENS = 5;

/**
 * Per-caller cap on new anonymous handshakes per window. One caller cannot
 * exhaust the global pre-session cap: after this many fresh handshakes (or
 * `MAX_ISSUANCE_BUCKETS` tracked callers) issuance fails closed with
 * `RATE_LIMITED`. Repeated requests that reuse an existing pre-session cookie
 * are not throttled.
 */
export const CSRF_ISSUANCE_LIMIT_PER_CLIENT = 10;
export const CSRF_ISSUANCE_WINDOW_MS = 10 * 60 * 1000;

/** Hard bound on throttle state so hostile caller churn cannot grow memory. */
export const MAX_ISSUANCE_BUCKETS = 1000;
