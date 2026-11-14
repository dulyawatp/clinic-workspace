import { z } from "zod";
import { readQuery } from "../../transport/read/client";

const photoSchema = z.object({
  id: z.string().min(1),
  patientId: z.string().min(1),
  encounterId: z.string().min(1),
  fictional: z.literal(true),
  category: z.enum(["before", "after", "profile", "clinical"]),
  bodyArea: z.string().min(1),
  viewpoint: z.string().min(1).nullable(),
  takenAt: z.string().refine((value) => !Number.isNaN(Date.parse(value))),
  consentStatus: z.enum(["recorded", "missing", "unknown"]),
  url: z.string().regex(/^\/api\/files\/[A-Za-z0-9_-]+$/),
  version: z.number().int().positive(),
  contentType: z.literal("image/png"),
  bytes: z.number().int().positive(),
  sha256: z.string().regex(/^[a-f0-9]{64}$/),
  width: z.number().int().positive(),
  height: z.number().int().positive(),
});
export type Photo = z.infer<typeof photoSchema>;

// Only this explicitly declared synthetic frame is comparable. This label says
// nothing about alignment or clinical equivalence of real photographs.
export function compatible(before: Photo, after: Photo): boolean {
  return (
    before.id !== after.id &&
    before.category === "before" &&
    after.category === "after" &&
    before.patientId === after.patientId &&
    before.bodyArea === after.bodyArea &&
    before.viewpoint === "synthetic-frame-v1" &&
    after.viewpoint === "synthetic-frame-v1"
  );
}

const invalid = () => ({
  code: "UPSTREAM_UNAVAILABLE" as const,
  retryable: true,
});

/** Read every page; a wrong-patient row or incomplete/inconsistent page fails closed. */
export async function readPatientPhotos(
  patientId: string,
  signal: AbortSignal,
): Promise<Photo[]> {
  const result: Photo[] = [];
  const ids = new Set<string>();
  let total: number | undefined;
  do {
    const page = await readQuery<unknown>(
      "clinical-photos",
      { filters: { patientId }, limit: 100, offset: result.length },
      signal,
    );
    if (
      !Number.isSafeInteger(page.total) ||
      page.total < 0 ||
      page.total > 10000 ||
      (total !== undefined && total !== page.total) ||
      page.items.length > 100 ||
      (page.items.length === 0 && result.length < page.total)
    )
      throw invalid();
    total = page.total;
    for (const item of page.items) {
      const parsed = photoSchema.safeParse(item);
      if (
        !parsed.success ||
        parsed.data.patientId !== patientId ||
        ids.has(parsed.data.id)
      )
        throw invalid();
      ids.add(parsed.data.id);
      result.push(parsed.data);
    }
    if (result.length > total) throw invalid();
  } while (result.length < total);
  return result;
}
