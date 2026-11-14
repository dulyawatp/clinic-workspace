export type ErrorCode =
  | "AUTH_EXPIRED"
  | "FORBIDDEN"
  | "VALIDATION"
  | "CONFLICT_STALE"
  | "CONFLICT_DUPLICATE"
  | "UPSTREAM_UNAVAILABLE"
  | "RATE_LIMITED"
  | "FILE_TOO_LARGE"
  | "UNSUPPORTED"
  | "UNKNOWN";

export type AppError = {
  code: ErrorCode;
  retryable: boolean;
  fieldErrors?: Record<string, string>;
};

export const ERROR_CODES = [
  "AUTH_EXPIRED",
  "FORBIDDEN",
  "VALIDATION",
  "CONFLICT_STALE",
  "CONFLICT_DUPLICATE",
  "UPSTREAM_UNAVAILABLE",
  "RATE_LIMITED",
  "FILE_TOO_LARGE",
  "UNSUPPORTED",
  "UNKNOWN",
] as const satisfies readonly ErrorCode[];

const ERROR_CODE_SET: ReadonlySet<string> = new Set(ERROR_CODES);

export const UNKNOWN_FALLBACK_KEY = "errors.UNKNOWN";

export function fromWire(code: unknown): ErrorCode {
  return typeof code === "string" && ERROR_CODE_SET.has(code)
    ? (code as ErrorCode)
    : "UNKNOWN";
}

export function toI18nKey(error: AppError): string {
  return ERROR_CODE_SET.has(error.code)
    ? `errors.${error.code}`
    : UNKNOWN_FALLBACK_KEY;
}
