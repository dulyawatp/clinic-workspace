import { expect, it, vi } from "vitest";
import { readLivePhotos } from "./live-photos";

const item = {
  photo_id: "photo-1",
  patient_reference: "native-1",
  sha256: "a".repeat(64),
  width: 100,
  height: 200,
  created_at: "2026-01-01",
  content_url:
    "/api/live/synthetic-patients/workspace-1/photos/photo-1/content",
};

it("accepts only the expected patient and content path", async () => {
  const fetcher = vi.fn().mockResolvedValue(Response.json({ items: [item] }));
  vi.stubGlobal("fetch", fetcher);
  try {
    expect(
      await readLivePhotos(
        "workspace-1",
        "native-1",
        0,
        new AbortController().signal,
      ),
    ).toHaveLength(1);
    for (const url of [
      "https://evil.test/api/live/synthetic-patients/workspace-1/photos/photo-1/content",
      "/api/live/synthetic-patients/other/photos/photo-1/content",
      "/api/live/synthetic-patients/workspace-1/photos/other/content",
      `${item.content_url}?x=1`,
      `//evil.test${item.content_url}`,
    ]) {
      fetcher.mockResolvedValueOnce(
        Response.json({ items: [{ ...item, content_url: url }] }),
      );
      await expect(
        readLivePhotos(
          "workspace-1",
          "native-1",
          0,
          new AbortController().signal,
        ),
      ).rejects.toThrow();
    }
    fetcher.mockResolvedValueOnce(
      Response.json({
        items: [{ ...item, patient_reference: "native-other" }],
      }),
    );
    await expect(
      readLivePhotos(
        "workspace-1",
        "native-1",
        0,
        new AbortController().signal,
      ),
    ).rejects.toThrow();
  } finally {
    vi.unstubAllGlobals();
  }
});
