import { expect, it } from "vitest";
import { createHandlers, type FixtureScenario } from "./msw/handlers";
import { server } from "./msw/server";

function scenario(options: FixtureScenario) {
  server.use(...createHandlers({ role: "DOC", ...options }));
}

async function read(resource: string, filters?: Record<string, string>) {
  const query = filters
    ? `?filters=${encodeURIComponent(JSON.stringify(filters))}`
    : "";
  const response = await fetch(`/api/read/${resource}${query}`);
  return { status: response.status, body: await response.json() };
}

it("selects existing encounter without renaming or mixing details", async () => {
  scenario({ encounterId: "ENC-2" });
  expect((await read("clinical-visits")).body.items).toMatchObject([
    { encounterId: "ENC-2" },
  ]);
  expect((await read("clinical-vitals")).body.items).toEqual([]);
  expect(
    (await read("clinical-visits", { encounterId: "ENC-1" })).body.items,
  ).toEqual([]);
  scenario({ encounterId: "ENC-UNKNOWN" });
  expect((await read("clinical-visits")).body.items).toEqual([]);
  expect((await read("clinical-sections")).body.items).toEqual([]);
});

it("filters diagnosis overrides and applies proxy schema projection", async () => {
  scenario({
    diagnoses: [
      {
        id: "DX-A",
        encounterId: "ENC-1",
        patientId: "PAT-1",
        code: "A",
        description: "A",
        fictional: true,
        hidden: "secret",
      },
      {
        id: "DX-B",
        encounterId: "ENC-2",
        patientId: "PAT-1",
        code: "B",
        description: "B",
        fictional: true,
      },
    ],
  });
  const result = await read("clinical-diagnoses", { encounterId: "ENC-1" });
  expect(result.body.total).toBe(1);
  expect(result.body.items[0]).toMatchObject({ id: "DX-A" });
  expect(result.body.items[0]).not.toHaveProperty("hidden");
  expect(
    (
      await read("clinical-diagnoses", {
        encounterId: "ENC-1",
        patientId: "other",
      })
    ).body.items,
  ).toEqual([]);
  scenario({
    encounterId: "ENC-2",
    diagnoses: [
      {
        id: "DX-A",
        encounterId: "ENC-1",
        patientId: "PAT-1",
        code: "A",
        description: "A",
        fictional: true,
      },
    ],
  });
  expect((await read("clinical-diagnoses")).body.items).toEqual([]);
  scenario({
    diagnoses: [
      { id: "bad", encounterId: "ENC-1", patientId: "PAT-1", fictional: true },
    ],
  });
  expect(await read("clinical-diagnoses")).toEqual({
    status: 502,
    body: { code: "UPSTREAM_UNAVAILABLE" },
  });
});

it("strips hidden photo fields and rejects non-private photo paths", async () => {
  scenario({
    photo: {
      url: "/api/files/fixture-photo-1",
      internal_notes: "secret",
      encounterId: "ENC-2",
      patientId: "OTHER",
    },
  });
  const result = await read("clinical-photos");
  expect(result.status).toBe(200);
  expect(result.body.items[0]).not.toHaveProperty("internal_notes");
  expect(result.body.items[0]).toMatchObject({
    encounterId: "ENC-1",
    patientId: "PAT-1",
  });
  scenario({ photo: { url: "/api/files/abc" } });
  expect(await read("clinical-photos")).toEqual({
    status: 502,
    body: { code: "UPSTREAM_UNAVAILABLE" },
  });
  scenario({ photo: { url: "https://example.com/photo.jpg" } });
  expect(await read("clinical-photos")).toEqual({
    status: 502,
    body: { code: "UPSTREAM_UNAVAILABLE" },
  });
});

it("applies clinical scenario overrides without changing fixture identities", async () => {
  scenario({
    encounterId: "ENC-1",
    weight: null,
    allergies: "none",
    state: "signed",
    savedAt: null,
    missing: ["exam"],
    photo: { version: 2 },
    consent: "recorded",
    reviewer: "Dr Example",
    files: ["result.pdf"],
    expired: true,
    draft: false,
    counts: { openDrafts: 3 },
  });
  expect((await read("clinical-vitals")).body.items[0]).toMatchObject({
    encounterId: "ENC-1",
    value: null,
  });
  expect((await read("clinical-sections")).body.items).toEqual(
    expect.arrayContaining([
      expect.objectContaining({ section: "history", allergies: "none" }),
      expect.objectContaining({
        section: "note",
        state: "signed",
        savedAt: null,
      }),
      expect.objectContaining({ section: "review", missing: ["exam"] }),
    ]),
  );
  expect((await read("clinical-photos")).body.items[0]).toMatchObject({
    version: 2,
    consentStatus: "recorded",
  });
  expect((await read("clinical-labs")).body.items[0]).toMatchObject({
    state: "signed",
    reviewer: "Dr Example",
    files: ["result.pdf"],
  });
  expect(
    (await read("clinical-procedures")).body.items[0].lines[0].expiry,
  ).toBe("2020-01-01");
  expect((await read("clinical-next-patients")).body.items[0].draft).toBe(
    false,
  );
  expect((await read("my-day-counts")).body.items[0].openDrafts).toBe(3);
  scenario({ missing: true });
  expect((await read("clinical-visits")).body.items).toEqual([]);
});
