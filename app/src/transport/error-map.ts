import { type AppError, type ErrorCode, fromWire } from "../domain/errors";

const RETRYABLE_CODES: ReadonlySet<ErrorCode> = new Set([
  "AUTH_EXPIRED",
  "CONFLICT_STALE",
  "UPSTREAM_UNAVAILABLE",
  "RATE_LIMITED",
]);

const STATUS_CODES: Readonly<Record<number, ErrorCode>> = {
  400: "VALIDATION",
  401: "AUTH_EXPIRED",
  403: "FORBIDDEN",
  405: "UNSUPPORTED",
  409: "CONFLICT_STALE",
  413: "FILE_TOO_LARGE",
  415: "UNSUPPORTED",
  422: "VALIDATION",
  429: "RATE_LIMITED",
};

function readWireCode(body: unknown): unknown {
  return typeof body === "object" && body !== null && "code" in body
    ? (body as { code: unknown }).code
    : undefined;
}

/**
 * Maps an HTTP response status and body to a client-safe `AppError`.
 *
 * Only the machine `code` is read from the body. Wire `fieldErrors`, messages,
 * and stacks are deliberately discarded: they are unverified free text and
 * could carry tracebacks or patient data. Field-level errors stay out of the
 * client error until a coded, safe contract exists. Unknown body shapes without
 * a recognised status degrade to `UNKNOWN`.
 */
export function mapHttpError(status: number, body: unknown): AppError {
  if (status >= 500) {
    return { code: "UPSTREAM_UNAVAILABLE", retryable: true };
  }

  const code = fromWire(readWireCode(body));
  if (code !== "UNKNOWN") {
    return { code, retryable: RETRYABLE_CODES.has(code) };
  }

  const statusCode = STATUS_CODES[status];
  if (statusCode) {
    return { code: statusCode, retryable: RETRYABLE_CODES.has(statusCode) };
  }

  return { code: "UNKNOWN", retryable: false };
}
