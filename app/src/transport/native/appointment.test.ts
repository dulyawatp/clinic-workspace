import { HttpResponse, http } from "msw";
import { afterEach, expect, it, vi } from "vitest";
import { server } from "../../test/msw/server";
import { fetchNativeAppointmentsBody, readNativeAppointmentItem } from "./appointment";
import {
  clearNativeCsrfToken,
  fetchNativeSession,
  nativeCsrfToken,
} from "./session";

const APPOINTMENTS =
  "/api/method/clinic_ui.api.appointments.synthetic_appointments";
const BOOTSTRAP = "/api/method/clinic_ui.api.session.bootstrap";

function liveSession() {
  return {
    mode: "live",
    sessionEpoch: "native-epoch-1",
    user: { id: "native|u1", displayName: "Native User", roles: ["REC"] },
    capabilities: { "read.appointment": "read_only" },
    csrfToken: "native-csrf-token",
  };
}

const ITEMS = {
  items: [
    {
      workspace_patient_id: "WS-1",
      appointment_id: "APPT-1",
      appointment_date: "2026-11-20",
      appointment_time: "09:30:00",
      status: "Open",
    },
  ],
};

it("accepts maximum-length opaque patient references", () => {
  const ref = `cpr1_${"a".repeat(803)}`;
  const item = { ...ITEMS.items[0], workspace_patient_id: ref };
  expect(readNativeAppointmentItem(item)).toEqual(item);
});

afterEach(() => {
  vi.restoreAllMocks();
  clearNativeCsrfToken();
});

it("reads the appointment list from the Frappe method", async () => {
  const fetchSpy = vi.spyOn(globalThis, "fetch");
  server.use(
    http.get(APPOINTMENTS, () => HttpResponse.json({ message: ITEMS })),
  );

  await expect(
    fetchNativeAppointmentsBody(new AbortController().signal),
  ).resolves.toEqual(ITEMS);

  const call = fetchSpy.mock.calls.find(([url]) =>
    String(url).startsWith(APPOINTMENTS),
  );
  expect(call).toBeDefined();
  expect(String(call?.[0])).toBe(APPOINTMENTS);
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
    http.get(APPOINTMENTS, () =>
      HttpResponse.json({ exc_type: "Error" }, { status }),
    ),
  );
  await expect(
    fetchNativeAppointmentsBody(new AbortController().signal),
  ).rejects.toEqual({ code, retryable });
});

it("maps a 403 from a guest/expired session to AUTH_EXPIRED", async () => {
  server.use(
    http.get(APPOINTMENTS, () =>
      HttpResponse.json({ exc_type: "PermissionError" }, { status: 403 }),
    ),
    http.get(BOOTSTRAP, () =>
      HttpResponse.json({ exc_type: "AuthenticationError" }, { status: 401 }),
    ),
  );

  await expect(
    fetchNativeAppointmentsBody(new AbortController().signal),
  ).rejects.toEqual({ code: "AUTH_EXPIRED", retryable: false });
});

it("keeps a 403 as FORBIDDEN when bootstrap returns an authenticated session", async () => {
  server.use(
    http.get(APPOINTMENTS, () =>
      HttpResponse.json({ exc_type: "PermissionError" }, { status: 403 }),
    ),
    http.get(BOOTSTRAP, () =>
      HttpResponse.json({ message: liveSession() }),
    ),
  );

  await expect(
    fetchNativeAppointmentsBody(new AbortController().signal),
  ).rejects.toEqual({ code: "FORBIDDEN", retryable: false });
});

it("never turns an uncertain bootstrap into FORBIDDEN", async () => {
  server.use(
    http.get(APPOINTMENTS, () =>
      HttpResponse.json({ exc_type: "PermissionError" }, { status: 403 }),
    ),
    http.get(BOOTSTRAP, () =>
      HttpResponse.json({ exc_type: "Error" }, { status: 500 }),
    ),
  );

  await expect(
    fetchNativeAppointmentsBody(new AbortController().signal),
  ).rejects.toEqual({ code: "UPSTREAM_UNAVAILABLE", retryable: true });
});

it("cancels session classification with the original read signal", async () => {
  server.use(
    http.get(BOOTSTRAP, () =>
      HttpResponse.json({ message: liveSession() }),
    ),
  );
  await fetchNativeSession();
  expect(nativeCsrfToken()).toBe("native-csrf-token");

  const controller = new AbortController();
  let classificationStarted = false;
  vi.spyOn(globalThis, "fetch").mockImplementation(
    (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.includes(APPOINTMENTS)) {
        return Promise.resolve(
          new Response(JSON.stringify({ exc_type: "PermissionError" }), {
            status: 403,
            headers: { "content-type": "application/json" },
          }),
        );
      }
      if (url.includes(BOOTSTRAP)) {
        classificationStarted = true;
        return new Promise<Response>((_resolve, reject) => {
          const abort = () => reject(new DOMException("Aborted", "AbortError"));
          if (init?.signal?.aborted) {
            abort();
            return;
          }
          init?.signal?.addEventListener("abort", abort, { once: true });
        });
      }
      return Promise.reject(new Error(`unexpected request ${url}`));
    },
  );

  const pending = fetchNativeAppointmentsBody(controller.signal);
  await vi.waitFor(() => expect(classificationStarted).toBe(true));
  controller.abort();

  await expect(pending).rejects.toMatchObject({ name: "AbortError" });
  expect(nativeCsrfToken()).toBe("native-csrf-token");
});

it.each([[{}], [{ message: "not an object" }], [{ message: null }]])(
  "rejects a body %j without a Frappe message mapping",
  async (body) => {
    server.use(http.get(APPOINTMENTS, () => HttpResponse.json(body)));
    await expect(
      fetchNativeAppointmentsBody(new AbortController().signal),
    ).rejects.toMatchObject({ code: "UPSTREAM_UNAVAILABLE" });
  },
);
