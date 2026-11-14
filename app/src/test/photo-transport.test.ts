import { createHash } from "node:crypto";
import { expect, it } from "vitest";
import { pngIntrinsicSize } from "../../../proxy/src/read/photo-assets";
import { createHandlers, type FixtureScenario } from "./msw/handlers";
import { server } from "./msw/server";

interface PhotoDTO {
  id: string;
  url: string;
  category: string;
  patientId: string;
  bodyArea: string;
  consentStatus: string;
  contentType: string;
  bytes: number;
  sha256: string;
  width: number;
  height: number;
}

function scenario(options: FixtureScenario) {
  server.use(...createHandlers({ role: "DOC", ...options }));
}

async function readPhotos(): Promise<PhotoDTO[]> {
  const response = await fetch("/api/read/clinical-photos");
  const body = (await response.json()) as { items: PhotoDTO[] };
  return body.items;
}

async function fetchFile(
  url: string,
): Promise<{ status: number; headers: Headers; bytes: Buffer }> {
  const response = await fetch(url);
  return {
    status: response.status,
    headers: response.headers,
    bytes: Buffer.from(await response.arrayBuffer()),
  };
}

it("mirrors the exact before/after fixture bytes with verified metadata", async () => {
  scenario({});
  const photos = await readPhotos();
  expect(photos.map((photo) => photo.category)).toEqual(["before", "after"]);

  const [before, after] = photos;
  if (before === undefined || after === undefined) {
    throw new Error("missing before/after fixture pair");
  }
  expect(before.patientId).toBe(after.patientId);
  expect(before.bodyArea).toBe(after.bodyArea);
  expect(before.sha256).not.toBe(after.sha256);

  for (const photo of photos) {
    const file = await fetchFile(photo.url);
    expect(file.status, photo.id).toBe(200);
    expect(file.headers.get("content-type")).toBe("image/png");
    expect(file.headers.get("x-content-type-options")).toBe("nosniff");
    expect(file.headers.get("cache-control")).toBe("no-store");
    expect(file.bytes.length).toBe(photo.bytes);
    expect(createHash("sha256").update(file.bytes).digest("hex")).toBe(
      photo.sha256,
    );
    expect(pngIntrinsicSize(file.bytes)).toEqual({
      width: photo.width,
      height: photo.height,
    });
  }
});

it("denies anonymous, disallowed and unknown fixture file ids", async () => {
  server.use(...createHandlers({ authed: false }));
  const anonymous = await fetchFile("/api/files/fixture-photo-1");
  expect(anonymous.status).toBe(401);
  expect(anonymous.headers.get("cache-control")).toBe("no-store");

  scenario({ role: "REC" });
  const denied = await fetchFile("/api/files/fixture-photo-1");
  expect(denied.status).toBe(403);
  expect(denied.headers.get("cache-control")).toBe("no-store");

  scenario({});
  for (const path of [
    "/api/files/missing-photo",
    "/api/files/fixture-photo-1.png",
    "/api/files/fixture-photo-1/extra",
    "/api/files/a/b/c",
    "/api/files/..%2F..%2Fpackage.json",
    "/api/files/%2e%2e%2f%2e%2e%2fpackage.json",
    "/api/files/https:%2F%2Fexample.com%2Fphoto.png",
  ]) {
    const file = await fetchFile(path);
    expect(file.status, path).toBe(404);
    expect(file.headers.get("content-type")).toContain("application/json");
    expect(file.headers.get("cache-control"), path).toBe("no-store");
  }
});

it("mirrors the manifest cross-check for photo DTO metadata", async () => {
  scenario({});
  expect((await readPhotos()).map((photo) => photo.id)).toEqual([
    "PH-1",
    "PH-2",
  ]);

  const mismatches: Array<Record<string, unknown>> = [
    { url: "/api/files/fixture-photo-unknown" },
    { url: "/api/files/fixture-photo-2" },
    { sha256: "b".repeat(64) },
    { width: 49 },
    { height: 33 },
    { bytes: 221 },
    { category: "after" },
    { bodyArea: "arm" },
    { viewpoint: "other" },
    { viewpoint: null },
  ];
  for (const override of mismatches) {
    scenario({ photo: override });
    const response = await fetch("/api/read/clinical-photos");
    expect(response.status, JSON.stringify(override)).toBe(502);
    expect(await response.json()).toEqual({ code: "UPSTREAM_UNAVAILABLE" });
  }
});

it("narrows the mirror DTO to transport metadata and rejects bad hashes", async () => {
  scenario({ photo: { internal_notes: "secret", hidden: 1 } });
  const [photo] = await readPhotos();
  expect(photo).toMatchObject({
    contentType: "image/png",
    bytes: expect.any(Number),
    sha256: expect.stringMatching(/^[0-9a-f]{64}$/),
    width: 48,
    height: 32,
  });
  expect(photo).not.toHaveProperty("internal_notes");
  expect(photo).not.toHaveProperty("hidden");

  scenario({ photo: { sha256: "not-a-hash" } });
  const response = await fetch("/api/read/clinical-photos");
  expect(response.status).toBe(502);
  expect(await response.json()).toEqual({ code: "UPSTREAM_UNAVAILABLE" });
});
