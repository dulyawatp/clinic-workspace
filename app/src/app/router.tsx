import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  createBrowserRouter,
  Outlet,
  type RouteObject,
  RouterProvider,
} from "react-router-dom";
import { AppointmentPage } from "../features/appointments/AppointmentPage";
import { CalendarPage } from "../features/appointments/CalendarPage";
import { SignInPage } from "../features/auth/SignInPage";
import { VisitWorkspacePage } from "../features/clinical/VisitWorkspacePage";
import { FinancePage } from "../features/finance/FinancePage";
import { PatientDirectoryPage } from "../features/patients/PatientDirectoryPage";
import { PatientProfilePage } from "../features/patients/PatientProfilePage";
import { PharmacyPage } from "../features/pharmacy/PharmacyPage";
import { LivePhotoWorkspace } from "../features/photos/LivePhotoWorkspace";
import {
  PhotoComparePage,
  PhotoGalleryPage,
} from "../features/photos/PhotoPages";
import { ProfilePage } from "../features/profile/ProfilePage";
import { CheckoutPage } from "../features/reception/CheckoutPage";
import { QueuePage } from "../features/reception/QueuePage";
import { ReceptionHomePage } from "../features/reception/ReceptionHomePage";
import { SearchPage } from "../features/search/SearchPage";
import { AppShell } from "../features/shell/AppShell";
import { SystemStatesPage } from "../features/system/SystemStatesPage";
import { PageHeader } from "../ui/PageHeader";
import { StatePanel } from "../ui/StatePanel";
import { RequireSession } from "./guards";
import { ROUTES } from "./routes";

function HomePlaceholder() {
  const { t } = useTranslation();
  return (
    <div data-testid="shell-placeholder">
      <PageHeader title={t("nav.today")} />
      <div className="shell-page-body">
        <StatePanel
          kind="empty"
          title={t("shell.homePending")}
          detail={t("shell.homePendingBody")}
        />
      </div>
    </div>
  );
}

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
          { path: ROUTES.home, element: <HomePlaceholder /> },
          { path: ROUTES.patients, element: <PatientDirectoryPage /> },
          { path: ROUTES.pharmacy, element: <PharmacyPage /> },
          { path: ROUTES.finance, element: <FinancePage /> },
          { path: `${ROUTES.patients}/:id`, element: <PatientProfilePage /> },
          { path: ROUTES.photoPattern, element: <PhotoGalleryPage /> },
          { path: ROUTES.photoComparePattern, element: <PhotoComparePage /> },
          { path: ROUTES.livePhotos, element: <LivePhotoWorkspace /> },
          { path: ROUTES.livePhotoPattern, element: <LivePhotoWorkspace /> },
          { path: ROUTES.reception, element: <ReceptionHomePage /> },
          { path: ROUTES.queue, element: <QueuePage /> },
          { path: ROUTES.checkout, element: <CheckoutPage /> },
          { path: ROUTES.appointments, element: <CalendarPage /> },
          { path: ROUTES.visitPattern, element: <VisitWorkspacePage /> },
          { path: `${ROUTES.appointments}/:id`, element: <AppointmentPage /> },
          { path: ROUTES.profile, element: <ProfilePage /> },
          { path: ROUTES.search, element: <SearchPage /> },
          { path: ROUTES.system, element: <SystemStatesPage /> },
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
