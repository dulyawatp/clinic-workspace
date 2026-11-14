import { afterEach, expect, it, vi } from "vitest";
import { photoStatus, savePhoto } from "./photo-save";

afterEach(() => vi.unstubAllGlobals());

it("gets a fresh CSRF token for every POST and sends the native contract", async () => {
  let token = 0;
  const fetcher = vi.fn(async (url: string, init?: RequestInit) => {
    if (url === "/api/auth/csrf")
      return Response.json({ token: `token-${++token}` });
    expect(
      ((init?.headers ?? {}) as Record<string, string>)["x-clinic-csrf"],
    ).toBe(`token-${token}`);
    return Response.json({ status: "pending", operation_id: "OP-1" });
  });
  vi.stubGlobal("fetch", fetcher);
  await savePhoto("PAT-1", "key-1", "YWJj");
  await photoStatus("OP-1");
  expect(JSON.parse(String(fetcher.mock.calls[1]?.[1]?.body))).toEqual({
    kind: "synthetic_original_create",
    request_key: "key-1",
    synthetic_patient: "PAT-1",
    image_base64: "YWJj",
  });
  expect(fetcher).toHaveBeenCalledTimes(4);
});
