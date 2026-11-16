import { useSession } from "../../app/SessionProvider";
import type { RoleCode } from "../../domain/capability";

/**
 * Roles of the signed-in session, as the presentation layer sees them. Route
 * guards already limited the screen; the stock surfaces additionally need to
 * know whether the held roles include inventory or administrator rights for the
 * designed reconcile restriction.
 */
export function useOpsRoles(): readonly RoleCode[] {
  const { status, session } = useSession();
  if (status !== "authenticated" || session === null) return [];
  return session.user.roles;
}
