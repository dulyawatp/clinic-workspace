import { HttpResponse } from "msw";
import type { CapabilityMap, RoleCode } from "../domain/capability";
import { server } from "./msw/server";

export const NATIVE_BOOTSTRAP = "/api/method/clinic_ui.api.session.bootstrap";

/** Minimal reviewed native bootstrap payload for a native-mode render. */
export function nativeSession(
  role: RoleCode,
  caps: CapabilityMap = { "read.patient": "enabled" },
) {
  return HttpResponse.json({
    message: {
      mode: "live",
      sessionEpoch: "native-epoch",
      user: {
        id: `native|${role.toLowerCase()}`,
        displayName: `Native ${role}`,
        roles: [role],
      },
      capabilities: caps,
      csrfToken: "native-csrf",
    },
  });
}

/**
 * Records every non-GET request the app makes. Fixture-only surfaces must make
 * none: a write has no transport in this build.
 */
export function postSpy() {
  const posts: string[] = [];
  const listener = (event: { request: Request }) => {
    if (event.request.method !== "GET") posts.push(event.request.url);
  };
  server.events.on("request:start", listener);
  return {
    posts,
    stop: () => server.events.removeListener("request:start", listener),
  };
}
