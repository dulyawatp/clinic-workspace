import { type RenderResult, render } from "@testing-library/react";
import type { ReactElement, ReactNode } from "react";
import {
  createMemoryRouter,
  MemoryRouter,
  type RouteObject,
  RouterProvider,
} from "react-router-dom";
import { AppProviders } from "../app/providers";
import { ROUTES } from "../app/routes";
import type { CapabilityMap } from "../domain/capability";
import { CapabilityProvider } from "../features/shell/CapabilityProvider";
import type { Locale } from "../i18n";
import { LocaleProvider } from "../i18n/LocaleProvider";
import { createHandlers, type FixtureScenario } from "./msw/handlers";
import { server } from "./msw/server";
import { installViewport } from "./setup";

type RenderAtOptions = Omit<FixtureScenario, "path" | "locale">;

function scenarioPath(scenario: FixtureScenario): string {
  return scenario.path ?? ROUTES.home;
}

/**
 * Identity of a scenario. Included in the remount key so a
 * `rerender(withFixtures(...))` with different role/auth/route/override state
 * is visibly a different fixture scenario.
 */
function scenarioKey(scenario: FixtureScenario): string {
  return JSON.stringify(
    Object.entries(scenario).sort(([a], [b]) => a.localeCompare(b)),
  );
}

let fixtureCallId = 0;

function providers(ui: ReactNode): ReactElement {
  return <AppProviders>{ui}</AppProviders>;
}

/**
 * Installs the scenario handlers, then renders the UI behind the real
 * `AppProviders` at `scenario.path` (default `ROUTES.home`). Register per-test
 * `server.use(...)` overrides *after* this call: MSW prepends later handlers.
 */
export function renderWithFixtures(
  ui: ReactNode,
  scenario: FixtureScenario = {},
): RenderResult {
  const path = scenarioPath(scenario);
  server.use(...createHandlers(scenario));
  window.history.pushState({}, "", path);
  return render(
    <MemoryRouter initialEntries={[path]}>{providers(ui)}</MemoryRouter>,
  );
}

/**
 * Same wiring as `renderWithFixtures` but returns the element, so a
 * state-transition test can `rerender(withFixtures(next))` in the same
 * container.
 *
 * Every call is a fresh fixture scenario, whether the argument object changed
 * or not: it installs new handler closures (`server.use` prepends them, so the
 * latest scenario wins) and keys the provider tree with a per-call identity.
 * The router and `AppProviders` therefore remount, so provider state (session,
 * location) and server handler state (login/anonymous) always reset together.
 * No UI state is retained across a `rerender(withFixtures(...))`; tests that do
 * not want a remount should not use this helper.
 */
export function withFixtures(
  ui: ReactNode,
  scenario: FixtureScenario = {},
): ReactElement {
  const path = scenarioPath(scenario);
  fixtureCallId += 1;
  server.use(...createHandlers(scenario));
  window.history.pushState({}, "", path);
  return (
    <MemoryRouter
      key={`${scenarioKey(scenario)}#${fixtureCallId}`}
      initialEntries={[path]}
    >
      {providers(ui)}
    </MemoryRouter>
  );
}

/**
 * For components that never call `useSession()`: explicit capabilities, an
 * isolated locale and a router, without the session or query providers.
 */
export function renderWithCaps(
  caps: CapabilityMap,
  ui: ReactNode,
  opts: { locale?: Locale } = {},
): RenderResult {
  return render(
    <LocaleProvider initial={opts.locale ?? "th"}>
      <CapabilityProvider value={caps}>
        <MemoryRouter>{ui}</MemoryRouter>
      </CapabilityProvider>
    </LocaleProvider>,
  );
}

/**
 * Router-level rendering at an explicit path. `opts` is a `FixtureScenario`
 * without `path`/`locale`; the app starts in Thai and tests switch language
 * through `LanguageToggle`. The app locale is not forced from test code.
 */
export function renderAt(
  path: string,
  ui: ReactNode,
  opts: RenderAtOptions = {},
): RenderResult {
  server.use(...createHandlers({ ...opts, path }));
  window.history.pushState({}, "", path);
  return render(
    <MemoryRouter initialEntries={[path]}>{providers(ui)}</MemoryRouter>,
  );
}

/** Sets the simulated viewport width and the matching `matchMedia` results. */
export function setViewport(width: 390 | 834 | 1440): void {
  installViewport(width);
}

/** Memory data router over an explicit route tree (needed by `useBlocker`). */
export function createTestRouter(
  routes: RouteObject[],
  initialEntries: string[] = [ROUTES.home],
  initialIndex?: number,
) {
  return createMemoryRouter(routes, { initialEntries, initialIndex });
}

/**
 * Data-router counterpart of `renderAt`. Declarative `MemoryRouter` cannot run
 * blockers, so tests covering `useBlocker` pass their minimal route tree here.
 * `opts` is a `FixtureScenario` without `path`/`locale`; the initial entry is
 * the route path. Returns the router so tests can inspect navigation state.
 */
export function renderWithDataRouter(
  routes: RouteObject[],
  opts: {
    initialEntries?: string[];
    initialIndex?: number;
  } & RenderAtOptions = {},
): RenderResult & { router: ReturnType<typeof createMemoryRouter> } {
  const initialEntries = opts.initialEntries ?? [scenarioPath(opts)];
  const initialIndex = opts.initialIndex ?? initialEntries.length - 1;
  const router = createTestRouter(routes, initialEntries, initialIndex);
  server.use(
    ...createHandlers({ ...opts, path: initialEntries[initialIndex] }),
  );
  window.history.pushState({}, "", initialEntries[initialIndex]);
  const result = render(providers(<RouterProvider router={router} />));
  return { ...result, router };
}
