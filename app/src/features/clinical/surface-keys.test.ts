// @vitest-environment node
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { initI18n } from "../../i18n";
import en from "../../i18n/catalog/en.json";
import th from "../../i18n/catalog/th.json";

const SURFACE_KEYS = Object.keys(th).filter((key) =>
  /^surface\.(DOC|NUR)-/.test(key),
);
const LAB_KEYS = Object.keys(th).filter((key) => key.startsWith("lab."));

describe("clinical surface catalogue", () => {
  it("stages all six doctor/nurse surface titles", () => {
    expect(SURFACE_KEYS.sort()).toEqual([
      "surface.DOC-01",
      "surface.DOC-02",
      "surface.DOC-03",
      "surface.DOC-04",
      "surface.NUR-01",
      "surface.NUR-02",
    ]);
  });

  it.each(["th", "en"] as const)(
    "resolves every surface and lab key in %s",
    (locale) => {
      const i18n = initI18n(locale);
      const dictionary: Record<string, string> = locale === "th" ? th : en;
      for (const key of [...SURFACE_KEYS, ...LAB_KEYS]) {
        expect(dictionary[key]?.trim().length, key).toBeGreaterThan(0);
        expect(i18n.t(key), key).not.toBe(key);
      }
    },
  );

  it("keeps the 18-field lab panel labels paired", () => {
    for (const key of [
      "lab.fieldPatient",
      "lab.fieldVisit",
      "lab.fieldRequest",
      "lab.fieldTest",
      "lab.provider",
      "lab.fieldRequestedBy",
      "lab.fieldPriority",
      "lab.fieldSentAt",
      "lab.fieldRef",
      "lab.fieldCategory",
      "lab.fieldSummary",
      "lab.fieldFiles",
      "lab.fieldUploadedBy",
      "lab.receivedAt",
      "lab.fieldReviewedBy",
      "lab.fieldReviewedAt",
      "lab.fieldInterpretation",
      "lab.fieldStatus",
    ]) {
      expect((th as Record<string, string>)[key], key).toBeTruthy();
      expect((en as Record<string, string>)[key], key).toBeTruthy();
    }
  });
});

describe("surface styling keeps the 44px and focus contracts", () => {
  const surfaces = readFileSync(
    new URL("../../design/components/surfaces.css", import.meta.url),
    "utf8",
  );
  const clinical = readFileSync(
    new URL("./clinical.css", import.meta.url),
    "utf8",
  );

  it("gives every field a 44px target and a visible focus ring", () => {
    expect(surfaces).toMatch(
      /\.pc-field input,[\s\S]*?min-height:\s*var\(--pc-tap\)/,
    );
    expect(surfaces).toMatch(/\.pc-field input:focus-visible/);
    expect(surfaces).toMatch(/\.pc-kpi:focus-visible/);
    expect(surfaces).toMatch(/\.pc-tile:focus-visible/);
  });

  it("never clips a dense table: the shared wrapper scrolls", () => {
    const components = readFileSync(
      new URL("../../design/components/components.css", import.meta.url),
      "utf8",
    );
    expect(components).toMatch(/\.pc-tablewrap\s*\{[^}]*overflow-x:\s*auto/s);
    expect(surfaces).toMatch(/\.pc-lab-fields/);
    expect(clinical).toMatch(/\.lab-toolbar select:focus-visible/);
  });
});
