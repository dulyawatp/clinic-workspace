import { afterEach, expect, it, vi } from "vitest";
import { nativeVisitAnnotationCallbacks as api } from "./visit-annotations";

vi.mock("./session", () => ({ nativeCsrfToken: () => "csrf" }));
const signal = new AbortController().signal;
const target = { appointment: "APT-1", encounter: "ENC-1", slot: "Before" as const };
const drawing = { schema_version: 2 as const, coordinate_space: "normalized" as const, shapes: [] };
const key = "request-key-123456";
const response = (message: unknown) => new Response(JSON.stringify({ message }), { status: 200 });
afterEach(() => vi.restoreAllMocks());

it("posts CSRF-bound same-origin read and maps strict response", async () => {
  const fetch = vi.spyOn(globalThis, "fetch").mockResolvedValue(response({
    version: 1, annotation: drawing, can_write: true, pending_request_key: null,
  }));
  expect(await api.read({ appointment: target.appointment, slot: target.slot }, signal)).toEqual({
    version: 1, annotation: drawing, canWrite: true, pendingRequestKey: null,
  });
  const [url, init] = fetch.mock.calls[0]!;
  expect(url).toBe("/api/method/clinic_photos.api.visit_annotations.read");
  expect(init).toMatchObject({ method: "POST", mode: "same-origin", redirect: "error",
    headers: { "X-Frappe-CSRF-Token": "csrf" } });
  expect(JSON.parse(String(init?.body))).toEqual({ payload: { appointment: target.appointment, slot: target.slot } });
});

it("maps submission failures to uncertain without retry", async () => {
  const fetch = vi.spyOn(globalThis, "fetch").mockRejectedValue(new Error("network"));
  expect(await api.submit({ ...target, request_key: key, expected_version: 0, annotation: drawing }, signal)).toBe("uncertain");
  expect(fetch).toHaveBeenCalledTimes(1);
});

it("accepts committed status only for exact request key", async () => {
  vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(response({ state: "committed", request_key: key, version: 1 }))
    .mockResolvedValueOnce(response({ state: "committed", request_key: "other-key-123456", version: 1 }));
  expect(await api.status({ appointment: target.appointment, slot: target.slot, request_key: key }, signal))
    .toEqual({ state: "committed", request_key: key, version: 1 });
  expect(await api.status({ appointment: target.appointment, slot: target.slot, request_key: key }, signal))
    .toEqual({ state: "uncertain" });
});

it("rejects extra response fields and invalid drawing", async () => {
  vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(response({
    version: 0, annotation: drawing, can_write: true, pending_request_key: null, secret: "x",
  })).mockResolvedValueOnce(response({
    version: 1, can_write: true, pending_request_key: null,
    annotation: { ...drawing, shapes: [{ type: "text", id: "x", color: "#000000", x: 2, y: 0, text: "x", font_size: 0.1 }] },
  }));
  const input = { appointment: target.appointment, slot: target.slot };
  expect(await api.read(input, signal)).toBeNull();
  expect(await api.read(input, signal)).toBeNull();
});

it("fails closed for redirected or cross-origin responses", async () => {
  const redirected = response({ state: "pending" });
  Object.defineProperty(redirected, "redirected", { value: true });
  const crossOrigin = response({ state: "pending" });
  Object.defineProperty(crossOrigin, "url", { value: "https://other.invalid/api/method/clinic_photos.api.visit_annotations.submit" });
  vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(redirected).mockResolvedValueOnce(crossOrigin);
  const input = { ...target, request_key: key, expected_version: 0, annotation: drawing };
  expect(await api.submit(input, signal)).toBe("uncertain");
  expect(await api.submit(input, signal)).toBe("uncertain");
});

it("rejects duplicate shape ids and oversized geometry before submission", async () => {
  const fetch = vi.spyOn(globalThis, "fetch");
  const mark = { type: "text" as const, id: "same", color: "#000000", x: 0, y: 0, text: "x", font_size: 0.1 };
  expect(await api.submit({ ...target, request_key: key, expected_version: 0,
    annotation: { ...drawing, shapes: [mark, mark] } }, signal)).toBe("rejected");
  expect(fetch).not.toHaveBeenCalled();
});

it("posts submit and status payloads without accepting extra outcome fields", async () => {
  const fetch = vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(response({ state: "pending" }))
    .mockResolvedValueOnce(response({ state: "committed", request_key: key, version: 1, extra: true }));
  const input = { ...target, request_key: key, expected_version: 0, annotation: drawing };
  expect(await api.submit(input, signal)).toBe("pending");
  expect(JSON.parse(String(fetch.mock.calls[0]?.[1]?.body))).toEqual({ payload: input });
  const status = { appointment: target.appointment, slot: target.slot, request_key: key };
  expect(await api.status(status, signal)).toEqual({ state: "uncertain" });
  expect(JSON.parse(String(fetch.mock.calls[1]?.[1]?.body))).toEqual({ payload: status });
});
