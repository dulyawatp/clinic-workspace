import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";
import { PageHeader } from "../design/components";
import {
  canAccessSurface,
  type SurfaceId,
  surfaceFor,
} from "../design/surfaces";
import type { RoleCode } from "../domain/capability";
import { StatePanel } from "../ui/StatePanel";
import { ROUTES } from "./routes";
import { useSession } from "./SessionProvider";

/**
 * Presentation-only route guard: a surface renders only for the roles the
 * design manifest lists for it. It never grants or denies data — every request
 * is still authorized by the proxy/server — and a role that is not on the list
 * gets a stated permission-denied page with a way back, never a silent redirect
 * that looks like a broken link.
 */
export function RoleGuard({
  surface,
  roles,
  children,
}: {
  surface: SurfaceId;
  /**
   * Narrows the surface to the roles that own one particular entry. Used where
   * a legacy address only ever belonged to part of a surface's audience, so the
   * denial state stays identical to the surface guard's.
   */
  roles?: readonly RoleCode[];
  children: ReactNode;
}) {
  const { status, session } = useSession();
  // `RequireSession` owns the anonymous and loading branches above this route.
  if (status !== "authenticated" || !session) return null;
  const held = session.user.roles;
  const allowed =
    canAccessSurface(surface, held) &&
    (!roles || roles.some((role) => held.includes(role)));
  if (allowed) return <>{children}</>;
  return <SurfaceAccessDenied surface={surface} />;
}

function SurfaceAccessDenied({ surface }: { surface: SurfaceId }) {
  const { t } = useTranslation();
  const spec = surfaceFor(surface);
  const title = t(spec.titleKey);
  return (
    <div data-testid="role-guard-denied" data-surface={surface}>
      <PageHeader
        breadcrumb={
          <>
            <Link to={ROUTES.home}>{t("action.goHome")}</Link>
            <span aria-hidden="true">/</span>
            <span>{surface}</span>
          </>
        }
        title={title}
      />
      <div className="pc-page__body">
        <StatePanel
          kind="permission-denied"
          title={t("state.denied.title")}
          detail={t("state.denied.body", { resource: title })}
        />
        <p>
          <Link className="pc-button pc-button--secondary" to={ROUTES.home}>
            {t("action.goMyHome")}
          </Link>
        </p>
      </div>
    </div>
  );
}
