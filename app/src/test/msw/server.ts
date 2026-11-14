import { setupServer } from "msw/node";
import { createHandlers, defaultHandlers } from "./handlers";

/**
 * Shared MSW server for the app test suite. Defaults are anonymous; individual
 * tests prepend their scenario handlers with `server.use(...createHandlers())`.
 */
export const server = setupServer(...defaultHandlers);

/**
 * Restores a *fresh* anonymous baseline. `server.resetHandlers()` alone would
 * restore the same default closures, so a login performed through those
 * handlers would leak into the next test; this installs new state each time.
 */
export function resetFixtureHandlers(): void {
  server.resetHandlers(...createHandlers({ authed: false }));
}
