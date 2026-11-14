// @vitest-environment node
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const css = readFileSync(new URL("./tokens.css", import.meta.url), "utf8");

describe("design tokens", () => {
  it.each([
    "--canvas",
    "--surface",
    "--ink",
    "--muted",
    "--border",
    "--primary",
    "--accent",
    "--success",
    "--warning",
    "--danger",
    "--info",
    "--neutral",
    "--radius-card",
    "--target-min",
    "--font-sans",
  ])("defines %s", (name) => {
    expect(css).toMatch(new RegExp(`${name}:\\s*[^;]+;`));
  });

  it("sets the minimum target to 44px", () => {
    expect(css).toMatch(/--target-min:\s*44px/);
  });

  it("includes a Thai-capable font fallback", () => {
    expect(css).toMatch(/Noto Sans Thai|IBM Plex Sans Thai|Sukhumvit Set/);
  });

  it("is imported after base styles by the single stylesheet entry", () => {
    const index = readFileSync(
      new URL("../styles/index.css", import.meta.url),
      "utf8",
    );
    expect(index).toMatch(
      /@import\s+"\.\/base\.css";\s*@import\s+"\.\.\/ui\/tokens\.css";/,
    );
  });
});
