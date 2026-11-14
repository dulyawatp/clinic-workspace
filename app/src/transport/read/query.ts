import { useQuery } from "@tanstack/react-query";
import { useSession } from "../../app/SessionProvider";
import type { AppError } from "../../domain/errors";
import { type ReadPage, type ReadParams, readQuery } from "./client";

/**
 * Read hook shared by the shell and pages.
 *
 * Isolation: the cache key includes the session's `authGeneration`, and the
 * query is disabled unless the session is authenticated. `SessionProvider`
 * additionally cancels in-flight reads and clears the cache on every identity
 * transition, so a login, logout or user switch can never surface cached rows
 * or a late response from the previous session. A 401/403 stays an error state:
 * no retry, no placeholder rows and no fabricated data.
 */
export function useReadQuery<T>(resource: string, params: ReadParams = {}) {
  const { status, authGeneration } = useSession();

  return useQuery<ReadPage<T>, AppError>({
    queryKey: [resource, params, authGeneration],
    enabled: status === "authenticated",
    queryFn: ({ signal }) => readQuery<T>(resource, params, signal),
    retry: false,
  });
}
