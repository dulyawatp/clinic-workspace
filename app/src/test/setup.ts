import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterAll, afterEach, beforeAll } from "vitest";
import { LOCALE_KEY } from "../i18n/locale-storage";
import { resetFixtureHandlers, server } from "./msw/server";
import { guardUnhandledFrame } from "./network";

export { allowTestOrigin, TEST_APP_ORIGIN } from "./network";

function mediaQueryList(query: string, width: number): MediaQueryList {
  const min = /min-width:\s*(\d+)px/.exec(query);
  const max = /max-width:\s*(\d+)px/.exec(query);
  const matches =
    min !== null
      ? width >= Number(min[1])
      : max !== null
        ? width <= Number(max[1])
        : false;

  return {
    matches,
    media: query,
    onchange: null,
    addListener: () => {},
    removeListener: () => {},
    addEventListener: () => {},
    removeEventListener: () => {},
    dispatchEvent: () => false,
  } as unknown as MediaQueryList;
}

/**
 * jsdom has no layout engine. Tests only need `innerWidth` and a matching
 * `min-width`/`max-width` result; nothing here simulates real layout.
 */
export function installViewport(width: number): void {
  if (typeof window === "undefined") return;
  Object.defineProperty(window, "innerWidth", {
    configurable: true,
    writable: true,
    value: width,
  });
  Object.defineProperty(window, "matchMedia", {
    configurable: true,
    writable: true,
    value: (query: string) => mediaQueryList(query, width),
  });
}

const DEFAULT_VIEWPORT = 1024;

installViewport(DEFAULT_VIEWPORT);

beforeAll(() => {
  server.listen({ onUnhandledFrame: guardUnhandledFrame });
});

afterEach(() => {
  resetFixtureHandlers();
  cleanup();
  installViewport(DEFAULT_VIEWPORT);
  // A persisted language choice is a browser preference: each test starts as a
  // fresh browser so the default locale is the one the test asked for. Tests
  // that assert persistence set and read the key inside their own body.
  if (typeof localStorage !== "undefined") localStorage.removeItem(LOCALE_KEY);
});

afterAll(() => {
  server.close();
});
