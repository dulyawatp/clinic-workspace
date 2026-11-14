import type { AppError } from "../../domain/errors";
import { mapHttpError } from "../error-map";
import { assertLegacyRoute } from "../native/legacy";

export interface ReadParams {
  limit?: number;
  offset?: number;
  q?: string;
  filters?: Record<string, string>;
}

export interface ReadPage<T> {
  items: T[];
  total: number;
}

function upstreamUnavailable(): AppError {
  return { code: "UPSTREAM_UNAVAILABLE", retryable: true };
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function buildQuery(params: ReadParams): string {
  const search = new URLSearchParams();
  if (params.limit !== undefined) search.set("limit", String(params.limit));
  if (params.offset !== undefined) search.set("offset", String(params.offset));
  if (params.q !== undefined && params.q !== "") search.set("q", params.q);
  if (params.filters !== undefined) {
    const entries = Object.entries(params.filters);
    if (entries.length > 0) {
      search.set("filters", JSON.stringify(Object.fromEntries(entries)));
    }
  }

  const query = search.toString();
  return query === "" ? "" : `?${query}`;
}

/**
 * Only the envelope is validated here: `items` must be an array and `total`
 * a finite number. The resource DTO is narrowed at the proxy, so adding a
 * field to a proxy schema never requires a client change. A 401/403 becomes
 * a query error, never fabricated rows.
 */
function parseEnvelope<T>(body: unknown): ReadPage<T> {
  if (!isRecord(body)) throw upstreamUnavailable();

  const items = body.items;
  const total = body.total;
  if (!Array.isArray(items) || typeof total !== "number") {
    throw upstreamUnavailable();
  }
  if (!Number.isFinite(total)) throw upstreamUnavailable();

  return { items: items as T[], total };
}

export async function readQuery<T>(
  resource: string,
  params: ReadParams = {},
  signal?: AbortSignal,
): Promise<ReadPage<T>> {
  // The generic read surface only exists behind the proxy transport.
  assertLegacyRoute();
  let response: Response;
  try {
    response = await fetch(
      `/api/read/${encodeURIComponent(resource)}${buildQuery(params)}`,
      {
        credentials: "same-origin",
        cache: "no-store",
        signal,
      },
    );
  } catch {
    // Network failure or abort: no stack or transport text leaves this layer.
    throw upstreamUnavailable();
  }

  if (!response.ok) {
    // Only the machine `code` is read; upstream messages and stacks are dropped.
    const body: unknown = await response.json().catch(() => null);
    throw mapHttpError(response.status, body);
  }

  const body: unknown = await response.json().catch(() => null);
  return parseEnvelope<T>(body);
}
