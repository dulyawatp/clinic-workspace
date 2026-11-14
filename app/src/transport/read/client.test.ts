import { HttpResponse, http } from "msw";
import { expect, it } from "vitest";
import { server } from "../../test/msw/server";
import { readQuery } from "./client";

it("validates only the envelope and keeps proxy-provided DTO fields", async () => {
  server.use(
    http.get("/api/read/patients", () =>
      HttpResponse.json({ items: [{ id: "PAT-1" }], total: 1 }),
    ),
  );
  await expect(readQuery("patients", {})).resolves.toEqual({
    items: [{ id: "PAT-1" }],
    total: 1,
  });
});

it("rejects a malformed envelope instead of trusting it", async () => {
  server.use(
    http.get("/api/read/patients", () => HttpResponse.json({ items: "no" })),
  );
  await expect(readQuery("patients", {})).rejects.toMatchObject({
    code: "UPSTREAM_UNAVAILABLE",
  });
});

it("maps a timeout to UPSTREAM_UNAVAILABLE without a stack", async () => {
  server.use(http.get("/api/read/patients", () => HttpResponse.error()));
  await expect(readQuery("patients", {})).rejects.toMatchObject({
    code: "UPSTREAM_UNAVAILABLE",
  });
});

it("maps a proxy 401 to AUTH_EXPIRED instead of returning rows", async () => {
  server.use(
    http.get("/api/read/patients", () =>
      HttpResponse.json({ code: "AUTH_EXPIRED" }, { status: 401 }),
    ),
  );
  await expect(readQuery("patients", {})).rejects.toMatchObject({
    code: "AUTH_EXPIRED",
  });
});

it("sends the allow-listed resource and query string", async () => {
  let seen: URL | undefined;
  server.use(
    http.get("/api/read/patients", ({ request }) => {
      seen = new URL(request.url);
      return HttpResponse.json({ items: [], total: 0 });
    }),
  );

  await readQuery("patients", { limit: 5, offset: 10, q: "สมชาย" });

  expect(seen?.pathname).toBe("/api/read/patients");
  expect(seen?.searchParams.get("limit")).toBe("5");
  expect(seen?.searchParams.get("offset")).toBe("10");
  expect(seen?.searchParams.get("q")).toBe("สมชาย");
});
