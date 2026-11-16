import { HttpResponse, http } from "msw";
import { afterEach, expect, it, vi } from "vitest";
import { server } from "../../test/msw/server";
import { fetchNativePatientDetailBody } from "./patient";

const VIEW = "/api/method/clinic_ui.api.patients.view";
const BOOTSTRAP = "/api/method/clinic_ui.api.session.bootstrap";

function liveSession() {
  return {
    mode: "live",
    sessionEpoch: "native-epoch-1",
    user: { id: "native|u1", displayName: "Native User", roles: ["DOC"] },
    capabilities: { "read.patient": "read_only" },
    csrfToken: "native-csrf-token",
  };
}

const DETAIL = {
  workspace_patient_id: "WS-1",
  display_name: "Patient One",
  first_name: "Patient",
  last_name: "One",
  sex: "Female",
  dob: "1990-01-02",
  status: "Active",
};

afterEach(() => {
  vi.restoreAllMocks();
});

it("reads the allowlisted alias from the Frappe view method", async () => {
  const fetchSpy = vi.spyOn(globalThis, "fetch");
  server.use(
    http.get(VIEW, ({ request }) => {
      const url = new URL(request.url);
      expect(url.searchParams.get("workspace_patient_id")).toBe("WS 1");
      return HttpResponse.json({ message: DETAIL });
    }),
  );

  await expect(
    fetchNativePatientDetailBody("WS 1", new AbortController().signal),
  ).resolves.toEqual(DETAIL);

  const call = fetchSpy.mock.calls.find(([url]) =>
    String(url).startsWith(VIEW),
  );
  expect(call).toBeDefined();
  expect(String(call?.[0])).toBe(`${VIEW}?workspace_patient_id=WS+1`);
  expect(call?.[1]).toEqual(
    expect.objectContaining({
      method: "GET",
      credentials: "same-origin",
      cache: "no-store",
    }),
  );
});

it.each([
  [401, "AUTH_EXPIRED", false],
  [404, "UNSUPPORTED", false],
  [500, "UPSTREAM_UNAVAILABLE", true],
] as const)("maps HTTP %s to %s", async (status, code, retryable) => {
  server.use(
    http.get(VIEW, () =>
      HttpResponse.json({ exc_type: "Error" }, { status }),
    ),
  );
  await expect(
    fetchNativePatientDetailBody("WS-1", new AbortController().signal),
  ).rejects.toEqual({ code, retryable });
});

it("maps a 403 from a guest/expired session to AUTH_EXPIRED", async () => {
  server.use(
    http.get(VIEW, () =>
      HttpResponse.json({ exc_type: "PermissionError" }, { status: 403 }),
    ),
    http.get(BOOTSTRAP, () =>
      HttpResponse.json({ exc_type: "AuthenticationError" }, { status: 401 }),
    ),
  );

  await expect(
    fetchNativePatientDetailBody("WS-1", new AbortController().signal),
  ).rejects.toEqual({ code: "AUTH_EXPIRED", retryable: false });
});

it("keeps a 403 as FORBIDDEN when bootstrap returns an authenticated session", async () => {
  server.use(
    http.get(VIEW, () =>
      HttpResponse.json({ exc_type: "PermissionError" }, { status: 403 }),
    ),
    http.get(BOOTSTRAP, () =>
      HttpResponse.json({ message: liveSession() }),
    ),
  );

  await expect(
    fetchNativePatientDetailBody("WS-1", new AbortController().signal),
  ).rejects.toEqual({ code: "FORBIDDEN", retryable: false });
});

it.each([[{}], [{ message: "not an object" }], [{ message: null }]])(
  "rejects a body %j without a Frappe message mapping",
  async (body) => {
    server.use(http.get(VIEW, () => HttpResponse.json(body)));
    await expect(
      fetchNativePatientDetailBody("WS-1", new AbortController().signal),
    ).rejects.toMatchObject({ code: "UPSTREAM_UNAVAILABLE" });
  },
);

it.each([[""], ["   "], [undefined], [null], [5]])(
  "refuses a malformed alias %j without any request",
  async (alias) => {
    const fetchSpy = vi.spyOn(globalThis, "fetch");
    await expect(
      fetchNativePatientDetailBody(
        alias as unknown as string,
        new AbortController().signal,
      ),
    ).rejects.toEqual({ code: "VALIDATION", retryable: false });
    expect(fetchSpy).not.toHaveBeenCalled();
  },
);
