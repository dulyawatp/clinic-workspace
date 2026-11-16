// @vitest-environment node
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const ops = readFileSync(new URL("./ops.css", import.meta.url), "utf8");
const components = readFileSync(
  new URL("../../design/components/components.css", import.meta.url),
  "utf8",
);

describe("operations surface responsive rules", () => {
  it("keeps dense tables in a horizontal scroll wrapper", () => {
    expect(components).toMatch(
      /\.pc-tablewrap\s*{[^}]*width: 100%[^}]*overflow-x: auto/s,
    );
    expect(components).toMatch(/\.pc-data\s*{[^}]*min-width: 40rem/s);
  });

  it("wraps filter, tab and bucket strips on narrow screens", () => {
    for (const selector of [".ops-filters", ".ops-tabs", ".ops-buckets"]) {
      expect(ops).toMatch(
        new RegExp(
          `\\${selector}\\s*{[^}]*flex-wrap: wrap`,
          "s",
        ),
      );
    }
  });

  it("keeps form controls on the 44px target with a visible focus ring", () => {
    expect(ops).toMatch(
      /\.ops-control select,\n\.ops-control input\s*{[^}]*min-height: var\(--pc-tap\)/s,
    );
    expect(ops).toMatch(
      /\.ops-control select:focus-visible,\n\.ops-control input:focus-visible\s*{[^}]*box-shadow: var\(--pc-ring-cyan\)/s,
    );
  });

  it("stacks controls and actions below the phone breakpoint", () => {
    expect(ops).toMatch(
      /@media \(max-width: 600px\)[\s\S]*?\.ops-control\s*{[^}]*flex-basis: 100%/s,
    );
    expect(ops).toMatch(
      /@media \(max-width: 600px\)[\s\S]*?\.ops-form__actions \.pc-button\s*{[^}]*width: 100%/s,
    );
  });
});
