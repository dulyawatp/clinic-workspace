import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import access from "@clinic/contracts/fixtures/access.json" with {
  type: "json",
};
import clinical from "@clinic/contracts/fixtures/clinical.json" with {
  type: "json",
};
import manifest from "@clinic/contracts/fixtures/photo-assets.json" with {
  type: "json",
};
import { expect, it } from "vitest";
import {
  PHOTO_ASSETS,
  photoAssetBytes,
  pngIntrinsicSize,
} from "./photo-assets.js";
import { fixtureResourceRoles, READ_RESOURCES } from "./registry.js";

const photoSchema = READ_RESOURCES["clinical-photos"]?.schema;

const ASSET_DIR = new URL(
  "../../../contracts/fixtures/assets/",
  import.meta.url,
);

function firstPhoto() {
  const photo = clinical.photos[0];
  if (photo === undefined) throw new Error("missing fixture photo");
  return photo;
}

it("pairs synthetic before and after bytes for one patient and body area", () => {
  const photos = clinical.photos;
  const before = photos.filter((photo) => photo.category === "before");
  const after = photos.filter((photo) => photo.category === "after");
  expect(before).toHaveLength(1);
  expect(after).toHaveLength(1);
  expect(before[0]?.patientId).toBe(after[0]?.patientId);
  expect(before[0]?.bodyArea).toBe(after[0]?.bodyArea);
  expect(before[0]?.viewpoint).toBe("synthetic-frame-v1");
  expect(before[0]?.viewpoint).toBe(after[0]?.viewpoint);
  expect(before[0]?.sha256).not.toBe(after[0]?.sha256);
});

it("keeps the committed raster files byte-identical to the manifest", () => {
  for (const asset of manifest.assets) {
    const file = readFileSync(new URL(asset.fileName, ASSET_DIR));
    expect(
      file.equals(Buffer.from(asset.dataBase64, "base64")),
      asset.fileId,
    ).toBe(true);
    expect(file.length).toBe(asset.bytes);
  }
});

it("serves only bytes whose SHA-256 and intrinsic dimensions the manifest records", () => {
  expect(PHOTO_ASSETS.size).toBe(clinical.photos.length);
  for (const photo of clinical.photos) {
    const fileId = photo.url.replace("/api/files/", "");
    const asset = PHOTO_ASSETS.get(fileId);
    expect(asset, fileId).toBeDefined();
    if (asset === undefined) continue;

    expect(asset.photoId).toBe(photo.id);
    expect(asset.patientId).toBe(photo.patientId);
    expect(asset.bodyArea).toBe(photo.bodyArea);
    expect(asset.viewpoint).toBe(photo.viewpoint);
    expect(asset.category).toBe(photo.category);
    expect(asset.contentType).toBe(photo.contentType);
    expect(asset.bytes).toBe(photo.bytes);
    expect(asset.sha256).toBe(photo.sha256);
    expect(asset.width).toBe(photo.width);
    expect(asset.height).toBe(photo.height);

    const bytes = photoAssetBytes(fileId);
    expect(bytes).toBeDefined();
    if (bytes === undefined) continue;
    expect(bytes.length).toBe(photo.bytes);
    expect(createHash("sha256").update(bytes).digest("hex")).toBe(photo.sha256);
    expect(pngIntrinsicSize(bytes)).toEqual({
      width: photo.width,
      height: photo.height,
    });
  }
});

it("uses the clinical photo fixture roles for the bytes allow-list", () => {
  expect(fixtureResourceRoles("clinical-photos")).toEqual(
    access.resources["clinical-photos"],
  );
  expect(fixtureResourceRoles("clinical-photos")).toEqual(["DOC", "NUR"]);
});

it("narrows the photo DTO to validated transport metadata", () => {
  expect(photoSchema).toBeDefined();
  if (photoSchema === undefined) return;

  const parsed = photoSchema.parse({
    ...firstPhoto(),
    internal_notes: "fixture-only note",
  }) as Record<string, unknown>;
  for (const field of [
    "contentType",
    "bytes",
    "sha256",
    "width",
    "height",
    "url",
  ]) {
    expect(parsed).toHaveProperty(field);
  }
  expect(parsed).not.toHaveProperty("internal_notes");
});

it("accepts every fixture row only because it matches the manifest", () => {
  expect(photoSchema).toBeDefined();
  if (photoSchema === undefined) return;
  for (const photo of clinical.photos) {
    expect(photoSchema.safeParse(photo).success, photo.id).toBe(true);
  }
});

it("rejects photo metadata that does not match the trusted manifest asset", () => {
  expect(photoSchema).toBeDefined();
  if (photoSchema === undefined) return;

  const invalid = [
    { sha256: "not-a-sha256" },
    { sha256: "A".repeat(64) },
    { bytes: 0 },
    { bytes: 1.5 },
    { width: 0 },
    { height: -32 },
    { contentType: "image/svg+xml" },
    { url: "https://example.com/photo.png" },
    { url: "/api/files/../photo.png" },
    // Syntactically valid but unrelated to the trusted manifest: these must
    // still fail closed.
    { url: "/api/files/fixture-photo-unknown" },
    { url: "/api/files/fixture-photo-2" },
    { sha256: "b".repeat(64) },
    { width: 49 },
    { height: 33 },
    { bytes: 221 },
    { id: "PH-9" },
    { patientId: "PAT-9" },
    { bodyArea: "arm" },
    { viewpoint: null },
    { viewpoint: "other" },
    { category: "after" },
  ];
  for (const override of invalid) {
    expect(
      photoSchema.safeParse({ ...firstPhoto(), ...override }).success,
      JSON.stringify(override),
    ).toBe(false);
  }
});
