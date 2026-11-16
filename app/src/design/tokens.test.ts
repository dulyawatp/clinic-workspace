// @vitest-environment node
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const tokens = readFileSync(
  new URL("./tokens.css", import.meta.url),
  "utf8",
);
const aliases = readFileSync(
  new URL("../ui/tokens.css", import.meta.url),
  "utf8",
);
const components = readFileSync(
  new URL("./components/components.css", import.meta.url),
  "utf8",
);
const index = readFileSync(
  new URL("../styles/index.css", import.meta.url),
  "utf8",
);

function declares(css: string, name: string): boolean {
  return new RegExp(`${name}:\\s*[^;]+;`).test(css);
}

describe("Preclinic design tokens", () => {
  it.each([
    // DESIGN.md core palette
    "--pc-bg",
    "--pc-surface",
    "--pc-fg",
    "--pc-muted",
    "--pc-border",
    "--pc-accent",
    "--pc-primary",
    "--pc-primary-hover",
    "--pc-primary-active",
    // functional status
    "--pc-status-success",
    "--pc-status-warning",
    "--pc-status-danger",
    "--pc-status-info",
    "--pc-ok",
    "--pc-warn",
    "--pc-danger",
    "--pc-info",
    "--pc-demo",
    // typography
    "--pc-font-sans",
    "--pc-font-mono",
    "--pc-fs-display",
    "--pc-fs-h1",
    "--pc-fs-h2",
    "--pc-fs-h3",
    "--pc-fs-body",
    "--pc-fs-small",
    "--pc-fs-micro",
    "--pc-fs-table",
    // spacing, radii, targets, motion
    "--pc-sp-1",
    "--pc-sp-16",
    "--pc-radius-s",
    "--pc-radius-m",
    "--pc-radius-l",
    "--pc-radius-pill",
    "--pc-tap",
    "--pc-ease",
    "--pc-dur-hover",
    "--pc-ring",
  ])("defines %s", (name) => {
    expect(declares(tokens, name)).toBe(true);
  });

  it("keeps the 4px spacing rhythm and 44px target from the brand spec", () => {
    expect(tokens).toMatch(/--pc-sp-4:\s*1rem/);
    expect(tokens).toMatch(/--pc-tap:\s*44px/);
    expect(tokens).toMatch(/--pc-radius-l:\s*10px/);
  });

  it("keeps a Thai-capable font stack", () => {
    expect(tokens).toMatch(/IBM Plex Sans Thai|Sukhumvit Set|Noto Sans Thai/);
  });

  it("keeps medical cyan off white text", () => {
    expect(components).toMatch(
      /\.pc-button--cyan\s*{[^}]*color:\s*var\(--pc-fg\)/s,
    );
    expect(components).not.toMatch(/\.pc-button--cyan\s*{[^}]*#fff/s);
  });

  it("maps the existing app variables onto the design tokens", () => {
    for (const [legacy, token] of [
      ["--canvas", "--pc-bg"],
      ["--surface", "--pc-surface"],
      ["--ink", "--pc-fg"],
      ["--muted", "--pc-muted"],
      ["--border", "--pc-border"],
      ["--primary", "--pc-primary"],
      ["--accent", "--pc-accent"],
      ["--font-sans", "--pc-font-sans"],
    ])
      expect(aliases).toMatch(
        new RegExp(`${legacy}:\\s*var\\(${token}\\)`, "i"),
      );
    // The 44px target stays literal for the accessibility checks that read it.
    expect(aliases).toMatch(/--target-min:\s*44px/);
  });

  it("imports tokens first and shared components once", () => {
    expect(index).toMatch(
      /@import "\.\.\/design\/tokens\.css";\s*@import "\.\/base\.css";/,
    );
    expect(index.match(/design\/components\/components\.css/g)).toHaveLength(1);
  });

  it("honours reduced motion", () => {
    expect(tokens).toMatch(/prefers-reduced-motion: reduce/);
    expect(components).toMatch(/prefers-reduced-motion: reduce/);
  });

  it("keeps tables in a horizontal scroll wrapper", () => {
    expect(components).toMatch(/\.pc-tablewrap\s*{[^}]*overflow-x:\s*auto/s);
  });
});
