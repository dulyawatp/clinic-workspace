import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  createBrowserRouter,
  Navigate,
  Outlet,
  type RouteObject,
  RouterProvider,
} from "react-router-dom";
import { SignInPage } from "../features/auth/SignInPage";
import { AppShell } from "../features/shell/AppShell";
import { StatePanel } from "../ui/StatePanel";
import { RequireSession } from "./guards";
import { FinanceRedirect, LivePhotoRedirect } from "./legacy-redirects";
import { RoleGuard } from "./role-guard";
import { ROUTES } from "./routes";
import { SURFACE_ROUTES } from "./surface-routes";

/**
 * Route-level failure fallback. Deliberately never reads `useRouteError()`:
 * route errors can carry identifiers and stack traces, so the user sees only
 * the localized catalogue message.
 */
export function RouteErrorFallback() {
  const { t } = useTranslation();
  return (
    <div className="shell-page-body" data-testid="route-error">
      <StatePanel kind="error" title={t("errors.UNKNOWN")} />
    </div>
  );
}

/**
 * Route tree for the data router. Exported so tests can build a
 * `createMemoryRouter` over the real routes without mounting the browser
 * router. The pathless root owns the shared sanitized `errorElement`, so both
 * the login route and the protected shell routes are covered.
 *
 * Designed surfaces come from `SURFACE_ROUTES` and retain their `RoleGuard`.
 * DOC-02 applies it internally to its legacy branches; Frappe owns native
 * encounter reads and writes. Legacy React-era addresses stay as redirects only.
 */
export const appRoutes: RouteObject[] = [
  {
    element: <Outlet />,
    errorElement: <RouteErrorFallback />,
    children: [
      { path: ROUTES.login, element: <SignInPage /> },
      {
        element: (
          <RequireSession>
            <AppShell>
              <Outlet />
            </AppShell>
          </RequireSession>
        ),
        children: [
          ...SURFACE_ROUTES.map(({ surface, path, element }) => ({
            path,
            element: surface === "DOC-02" ? element : <RoleGuard surface={surface}>{element}</RoleGuard>,
          })),
          { path: ROUTES.legacyFinance, element: <FinanceRedirect /> },
          {
            // Legacy patient picker: gated as the PAT-04 clinician entry and
            // forwarded to the role home. The workspace itself stays reachable
            // from PAT-04/05, which carry the patient context it needs.
            path: ROUTES.livePhotos,
            element: (
              <RoleGuard surface="PAT-04" roles={["DOC", "NUR"]}>
                <Navigate to={ROUTES.home} replace />
              </RoleGuard>
            ),
          },
          { path: ROUTES.livePhotoPattern, element: <LivePhotoRedirect /> },
        ],
      },
    ],
  },
];

type BrowserRouterInstance = ReturnType<typeof createBrowserRouter>;

/**
 * `createBrowserRouter` subscribes to `popstate` at creation and has no React
 * cleanup hook, so ownership lives here instead of in the render body:
 * - at most one subscribed browser router exists (`ownedBrowserRouter`);
 * - a mount at a different window URL replaces it, which keeps tests that
 *   `pushState` before `render(<App />)` faithful to the previous
 *   `BrowserRouter` behavior;
 * - unmount releases it after a task boundary so StrictMode's setup →
 *   cleanup → setup cycle does not dispose the router it immediately reuses.
 */
let ownedBrowserRouter: BrowserRouterInstance | undefined;
let browserRouterReleased = false;
let disposeTimer: ReturnType<typeof setTimeout> | undefined;

function windowUrl(): string {
  const { pathname, search, hash } = window.location;
  return pathname + search + hash;
}

function routerUrl(router: BrowserRouterInstance): string {
  const { pathname, search, hash } = router.state.location;
  return pathname + search + hash;
}

function acquireBrowserRouter(): BrowserRouterInstance {
  if (
    !browserRouterReleased &&
    ownedBrowserRouter !== undefined &&
    routerUrl(ownedBrowserRouter) === windowUrl()
  ) {
    return ownedBrowserRouter;
  }
  if (disposeTimer !== undefined) {
    clearTimeout(disposeTimer);
    disposeTimer = undefined;
  }
  ownedBrowserRouter?.dispose();
  ownedBrowserRouter = createBrowserRouter(appRoutes);
  browserRouterReleased = false;
  return ownedBrowserRouter;
}

export function AppRouter() {
  const [router] = useState(acquireBrowserRouter);

  useEffect(() => {
    browserRouterReleased = false;
    if (disposeTimer !== undefined) {
      clearTimeout(disposeTimer);
      disposeTimer = undefined;
    }
    return () => {
      browserRouterReleased = true;
      const owned = router;
      disposeTimer = setTimeout(() => {
        disposeTimer = undefined;
        if (ownedBrowserRouter !== owned) return;
        ownedBrowserRouter = undefined;
        owned.dispose();
      }, 0);
    };
  }, [router]);

  return <RouterProvider router={router} />;
}
