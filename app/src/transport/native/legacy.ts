import type { AppError } from "../../domain/errors";
import { nativeTransportEnabled } from "./flag";

/**
 * `UNSUPPORTED` for a legacy proxy route the native build does not serve.
 * Non-retryable: no request was made and none will be.
 */
export function nativeRouteUnavailable(): AppError {
  return { code: "UNSUPPORTED", retryable: false };
}

/**
 * Refuses a legacy proxy route (`/api/read`, `/api/live`, `/api/write`,
 * `/api/auth`, `/api/files`) before any request in the native build.
 *
 * Call it outside a `try` that maps transport failures, so the safe
 * `UNSUPPORTED` error reaches the caller unchanged.
 */
export function assertLegacyRoute(): void {
  if (nativeTransportEnabled()) throw nativeRouteUnavailable();
}

/** Promise form for call sites that handle failures as rejections. */
export async function legacyFetch(
  path: string,
  init?: RequestInit,
): Promise<Response> {
  assertLegacyRoute();
  return fetch(path, init);
}
