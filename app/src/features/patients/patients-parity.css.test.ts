// @vitest-environment node
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const patients = readFileSync(new URL("./patients.css", import.meta.url), "utf8");
const photos = readFileSync(
  new URL("../photos/photos.css", import.meta.url),
  "utf8",
);
const annotate = readFileSync(
  new URL("../photos/annotate/annotate.css", import.meta.url),
  "utf8",
);

describe("patient surface responsive rules", () => {
  it("collapses the create grid to one column on a phone", () => {
    expect(patients).toMatch(
      /@media \(max-width: 767px\)[\s\S]*?\.patient-create-grid[^{]*{[^}]*grid-template-columns: 1fr/s,
    );
  });

  it("keeps every create control on the 44px target", () => {
    expect(patients).toMatch(
      /\.patient-create-field (?:input|select|textarea)[\s\S]*?min-height: var\(--target-min\)/,
    );
    expect(patients).toMatch(
      /\.patient-create-field input:focus-visible[\s\S]*?box-shadow: var\(--pc-ring-cyan\)/,
    );
  });

  it("does not trap the form in a fixed pixel width", () => {
    const createBlock = patients.slice(patients.indexOf("PAT-02"));
    expect(createBlock).not.toMatch(/(?:^|[;{\s])min-width:\s*[3-9]\d{2}px/m);
    expect(createBlock).not.toMatch(/(?:^|[;{\s])width:\s*[3-9]\d{2}px/m);
  });

  it("stacks the three PAT-03 summary cards below the tablet band", () => {
    expect(patients).toMatch(
      /@media \(max-width: 767px\)[\s\S]*?\.patients-summary-grid[^{]*{[^}]*grid-template-columns: 1fr/s,
    );
    expect(patients).toMatch(
      /@media \(max-width: 1023px\)[\s\S]*?\.patients-summary-grid[^{]*{[^}]*grid-template-columns: repeat\(2/s,
    );
    expect(patients).toMatch(/\.patients-consent-facts dd[^{]*{[^}]*overflow-wrap: anywhere/s);
  });
});

describe("photo parity responsive rules", () => {
  it("stacks intake, consent and tag controls on a narrow screen", () => {
    expect(photos).toMatch(
      /@media \(max-width: 600px\)[\s\S]*?\.photos-consent-form[^{]*{[^}]*grid-template-columns: 1fr/s,
    );
    expect(photos).toMatch(
      /@media \(max-width: 600px\)[\s\S]*?\.photos-intake-step[^{]*{[^}]*flex-direction: column/s,
    );
    expect(photos).toMatch(
      /\.photos-field input[\s\S]*?min-height: var\(--target-min\)/,
    );
  });

  it("shortens the annotation stage on a phone and honours reduced motion", () => {
    expect(annotate).toMatch(
      /@media \(max-width: 767px\)[^{]*{[^}]*\.annotation-stage[^}]*height: 330px/s,
    );
    expect(annotate).toMatch(/prefers-reduced-motion: reduce/);
    expect(annotate).toMatch(/\.annotation-toolbar[\s\S]*?flex-wrap: wrap/);
  });
});
