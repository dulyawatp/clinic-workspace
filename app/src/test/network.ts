import { TEST_APP_ORIGIN } from "./test-origin";

export { TEST_APP_ORIGIN };

const LOOPBACK_HOSTS = new Set(["localhost", "127.0.0.1", "[::1]", "::1"]);

/**
 * Exact origins an integration test has bound and explicitly registered.
 * Nothing is allowed through MSW by default — not even loopback — so a test
 * must opt in after its local server is listening and can revoke afterwards.
 */
const registeredOrigins = new Set<string>();

function isLoopback(url: URL): boolean {
  return LOOPBACK_HOSTS.has(url.hostname);
}

/**
 * Registers an exact loopback origin as test-owned. Returns a disposer the
 * caller must invoke once its local servers close.
 */
export function allowTestOrigin(origin: string): () => void {
  const url = new URL(origin);
  if (url.protocol !== "http:" && url.protocol !== "https:") {
    throw new Error(`Test origin must be HTTP(S): ${origin}`);
  }
  if (!isLoopback(url)) {
    throw new Error(`Test origin must be loopback: ${origin}`);
  }

  registeredOrigins.add(url.origin);
  return () => {
    registeredOrigins.delete(url.origin);
  };
}

/**
 * Any unhandled MSW frame is a defect: it could reach a real service. Only an
 * exact origin registered with `allowTestOrigin()` may pass through; every
 * other request fails the test loudly before a socket is opened.
 */
export function guardUnhandledFrame({
  frame,
}: {
  frame: { protocol: string; data: unknown };
}): void {
  if (frame.protocol === "http") {
    const request = (frame.data as { request?: Request }).request;
    if (request !== undefined) {
      const url = new URL(request.url);
      if (registeredOrigins.has(url.origin)) return;
      throw new Error(
        `Blocked unhandled network request during tests: ${url.origin}${url.pathname}`,
      );
    }
  }
  throw new Error(`Blocked unhandled ${frame.protocol} frame during tests`);
}
