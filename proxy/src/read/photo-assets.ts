import { createHash } from "node:crypto";
import manifestJson from "@clinic/contracts/fixtures/photo-assets.json" with {
  type: "json",
};
import { z } from "zod";

/**
 * Fixture-only photo bytes. The manifest is the complete allow-list: there is
 * no filesystem path, query value or upstream URL in this module, so a caller
 * can never ask the proxy to read a different file.
 *
 * `dataBase64` is the exact synthetic raster. The committed `.png` files under
 * `contracts/fixtures/assets/` are generated from the same bytes and compared
 * by `photo-assets.test.ts`.
 */
const photoAssetSchema = z.object({
  fileId: z.string().regex(/^[A-Za-z0-9_-]+$/),
  photoId: z.string().min(1),
  patientId: z.string().min(1),
  bodyArea: z.string().min(1),
  // Explicit fixture frame label; not a claim about a real clinical viewpoint.
  viewpoint: z.literal("synthetic-frame-v1").nullable(),
  category: z.enum(["before", "after", "profile", "clinical"]),
  // A basename only: defence in depth even though nothing resolves a path.
  fileName: z.string().regex(/^[a-z0-9-]+\.png$/),
  contentType: z.literal("image/png"),
  bytes: z.number().int().positive(),
  sha256: z.string().regex(/^[0-9a-f]{64}$/),
  width: z.number().int().positive(),
  height: z.number().int().positive(),
  dataBase64: z.string().min(1),
});

const manifestSchema = z.object({
  fictional: z.literal(true),
  generatedBy: z.string().min(1),
  assets: z.array(photoAssetSchema).min(1),
});

export type PhotoAsset = z.infer<typeof photoAssetSchema>;

const PNG_SIGNATURE: readonly number[] = [
  0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a,
];

/**
 * Reads width/height from the IHDR chunk. Returns `null` for anything that is
 * not an 8-byte-signature PNG; the route never re-encodes or resizes a file.
 */
export function pngIntrinsicSize(
  bytes: Uint8Array,
): { width: number; height: number } | null {
  if (bytes.length < 24) return null;
  for (const [index, value] of PNG_SIGNATURE.entries()) {
    if (bytes[index] !== value) return null;
  }
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  if (view.getUint32(8) !== 13) return null;
  if (String.fromCharCode(...bytes.subarray(12, 16)) !== "IHDR") return null;
  return { width: view.getUint32(16), height: view.getUint32(20) };
}

function loadPhotoAssets(): {
  assets: ReadonlyMap<string, PhotoAsset>;
  bytes: ReadonlyMap<string, Buffer>;
} {
  const parsed = manifestSchema.safeParse(manifestJson);
  if (!parsed.success) {
    throw new Error("INVALID_PHOTO_MANIFEST");
  }

  const assets = new Map<string, PhotoAsset>();
  const bytes = new Map<string, Buffer>();
  for (const asset of parsed.data.assets) {
    if (assets.has(asset.fileId)) {
      throw new Error("DUPLICATE_PHOTO_ASSET");
    }
    const buffer = Buffer.from(asset.dataBase64, "base64");
    const sha256 = createHash("sha256").update(buffer).digest("hex");
    if (buffer.length !== asset.bytes || sha256 !== asset.sha256) {
      throw new Error("PHOTO_ASSET_INTEGRITY");
    }
    const size = pngIntrinsicSize(buffer);
    if (
      size === null ||
      size.width !== asset.width ||
      size.height !== asset.height
    ) {
      throw new Error("PHOTO_ASSET_DIMENSIONS");
    }
    assets.set(asset.fileId, asset);
    bytes.set(asset.fileId, buffer);
  }
  return { assets, bytes };
}

const loaded = loadPhotoAssets();

export const PHOTO_ASSETS: ReadonlyMap<string, PhotoAsset> = loaded.assets;

/** Exact allow-list lookup; the key is never treated as a path. */
export function photoAsset(fileId: string): PhotoAsset | undefined {
  return PHOTO_ASSETS.get(fileId);
}

export function photoAssetBytes(fileId: string): Buffer | undefined {
  return loaded.bytes.get(fileId);
}
