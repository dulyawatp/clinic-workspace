/**
 * Generates the synthetic raster fixtures used by the clinical photo read
 * foundation. Everything here is fictional: the pixels are procedural and the
 * bytes are deterministic, so the committed PNG files, the manifest and the
 * SHA-256 values in `contracts/fixtures/clinical.json` can be regenerated and
 * compared byte-for-byte.
 *
 * Run: node scripts/generate-photo-fixtures.mjs
 */
import { createHash } from "node:crypto";
import { mkdirSync, writeFileSync } from "node:fs";
import { deflateSync } from "node:zlib";

const ROOT = new URL("../", import.meta.url);
const ASSET_DIR = new URL("contracts/fixtures/assets/", ROOT);
const MANIFEST = new URL("contracts/fixtures/photo-assets.json", ROOT);

const PNG_SIGNATURE = Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]);

const CRC_TABLE = (() => {
  const table = new Uint32Array(256);
  for (let index = 0; index < 256; index += 1) {
    let value = index;
    for (let bit = 0; bit < 8; bit += 1) {
      value = value & 1 ? 0xedb88320 ^ (value >>> 1) : value >>> 1;
    }
    table[index] = value >>> 0;
  }
  return table;
})();

function crc32(bytes) {
  let value = 0xffffffff;
  for (const byte of bytes) {
    value = CRC_TABLE[(value ^ byte) & 0xff] ^ (value >>> 8);
  }
  return (value ^ 0xffffffff) >>> 0;
}

function chunk(type, data) {
  const length = Buffer.alloc(4);
  length.writeUInt32BE(data.length, 0);
  const body = Buffer.concat([Buffer.from(type, "ascii"), data]);
  const crc = Buffer.alloc(4);
  crc.writeUInt32BE(crc32(body), 0);
  return Buffer.concat([length, body, crc]);
}

function png(width, height, pixel) {
  const header = Buffer.alloc(13);
  header.writeUInt32BE(width, 0);
  header.writeUInt32BE(height, 4);
  header[8] = 8; // bit depth
  header[9] = 2; // truecolour RGB
  header[10] = 0; // deflate
  header[11] = 0; // adaptive filtering
  header[12] = 0; // no interlace

  const rows = [];
  for (let y = 0; y < height; y += 1) {
    const row = Buffer.alloc(1 + width * 3);
    for (let x = 0; x < width; x += 1) {
      const [red, green, blue] = pixel(x, y);
      row[1 + x * 3] = red;
      row[2 + x * 3] = green;
      row[3 + x * 3] = blue;
    }
    rows.push(row);
  }

  return Buffer.concat([
    PNG_SIGNATURE,
    chunk("IHDR", header),
    chunk("IDAT", deflateSync(Buffer.concat(rows), { level: 9 })),
    chunk("IEND", Buffer.alloc(0)),
  ]);
}

function beforePixel(x, y) {
  // Fictional "before" capture: flat skin tone with a vertical marker band.
  const band = x >= 8 && x < 12 ? 40 : 0;
  return [200 - Math.floor(y / 4) - band, 170 - Math.floor(y / 6) - band, 150 - band];
}

function afterPixel(x, y) {
  // Fictional "after" capture: same frame, lighter tone and a diagonal mark.
  const mark = (x + y) % 20 < 3 ? 30 : 0;
  return [206 - Math.floor(y / 5) - mark, 182 - Math.floor(x / 8) - mark, 164 - mark];
}

const WIDTH = 48;
const HEIGHT = 32;

const ASSETS = [
  {
    fileId: "fixture-photo-1",
    photoId: "PH-1",
    patientId: "PAT-1",
    bodyArea: "face",
    viewpoint: "synthetic-frame-v1",
    category: "before",
    fileName: "photo-before.png",
    bytes: png(WIDTH, HEIGHT, beforePixel),
  },
  {
    fileId: "fixture-photo-2",
    photoId: "PH-2",
    patientId: "PAT-1",
    bodyArea: "face",
    viewpoint: "synthetic-frame-v1",
    category: "after",
    fileName: "photo-after.png",
    bytes: png(WIDTH, HEIGHT, afterPixel),
  },
];

mkdirSync(ASSET_DIR, { recursive: true });

const manifest = {
  fictional: true,
  generatedBy: "scripts/generate-photo-fixtures.mjs",
  assets: ASSETS.map((asset) => {
    writeFileSync(new URL(asset.fileName, ASSET_DIR), asset.bytes);
    return {
      fileId: asset.fileId,
      photoId: asset.photoId,
      patientId: asset.patientId,
      bodyArea: asset.bodyArea,
      viewpoint: asset.viewpoint,
      category: asset.category,
      fileName: asset.fileName,
      contentType: "image/png",
      bytes: asset.bytes.length,
      sha256: createHash("sha256").update(asset.bytes).digest("hex"),
      width: WIDTH,
      height: HEIGHT,
      dataBase64: asset.bytes.toString("base64"),
    };
  }),
};

writeFileSync(MANIFEST, `${JSON.stringify(manifest, null, 2)}\n`);

for (const asset of manifest.assets) {
  console.log(
    `${asset.fileId} ${asset.fileName} ${asset.width}x${asset.height} ${asset.bytes}B ${asset.sha256}`,
  );
}
