// @vitest-environment node
import {
  mkdtempSync,
  readdirSync,
  readFileSync,
  rmSync,
  statSync,
  writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { pathToFileURL } from "node:url";
import { build } from "vite";
import { afterAll, describe, expect, it } from "vitest";

/**
 * Semantic build-mode regression: the *executed build artifact* must select the
 * native transport, not just an asset path or a flag string.
 *
 * `nativeTransportEnabled()` is a build-time constant (`import.meta.env`), so the
 * evidence is the bundle the real `vite.config.ts` produces for each mode:
 *
 * 1. the emitted chunk must contain the Frappe method constants — proof that the
 *    native transport modules survived this build;
 * 2. the chunk is imported and evaluated, and the real `assertLegacyRoute()`
 *    guard must throw `UNSUPPORTED` — proof that the native branch is live;
 * 3. the default build must do the exact opposite.
 *
 * A shell environment variable must not be able to change the outcome, so the
 * last case runs with `VITE_CLINIC_NATIVE=0` in `process.env`.
 */
const APP_ROOT = process.cwd();
const CONFIG = join(APP_ROOT, "vite.config.ts");
const SRC = join(APP_ROOT, "src");

/** Frappe method constants that only exist in the native transport modules. */
const NATIVE_METHODS = [
  "clinic_ui.api.session.bootstrap",
  "clinic_ui.api.directory.synthetic_patients",
];

type ProbeResult = {
  native: boolean;
  legacy: string;
  reachable: string;
};

const outDirs: string[] = [];

afterAll(() => {
  for (const dir of outDirs) rmSync(dir, { recursive: true, force: true });
});

function jsFiles(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const path = join(dir, name);
    if (statSync(path).isDirectory()) return jsFiles(path);
    return name.endsWith(".js") ? [path] : [];
  });
}

/**
 * Builds a fixture that exercises the real transport decision and returns the
 * emitted chunk text plus the result of evaluating it.
 */
async function probe(
  mode: string,
): Promise<{ code: string; result: ProbeResult }> {
  const dir = mkdtempSync(join(tmpdir(), `clinic-${mode}-`));
  outDirs.push(dir);

  const modulePath = (name: string) =>
    JSON.stringify(join(SRC, "transport", "native", name));
  const entry = join(dir, "probe.js");
  writeFileSync(
    entry,
    [
      `import { nativeTransportEnabled } from ${modulePath("flag.js")};`,
      `import { assertLegacyRoute } from ${modulePath("legacy.js")};`,
      `import { fetchNativeSession } from ${modulePath("session.js")};`,
      `import { fetchNativeDirectoryBody } from ${modulePath("directory.js")};`,
      "let legacy;",
      "try {",
      "  assertLegacyRoute();",
      '  legacy = "allowed";',
      "} catch (error) {",
      '  legacy = error && error.code ? error.code : "error";',
      "}",
      "globalThis.__clinicProbe = {",
      "  native: nativeTransportEnabled(),",
      "  legacy,",
      "  reachable: [typeof fetchNativeSession, typeof fetchNativeDirectoryBody].join(),",
      "};",
    ].join("\n"),
    "utf8",
  );

  const outDir = join(dir, "dist");
  await build({
    configFile: CONFIG,
    mode,
    logLevel: "silent",
    build: {
      outDir,
      emptyOutDir: true,
      minify: false,
      lib: { entry, formats: ["es"], fileName: "probe" },
    },
  });

  const files = jsFiles(outDir);
  expect(files.length).toBeGreaterThan(0);
  const code = files.map((file) => readFileSync(file, "utf8")).join("\n");
  const artifact = files.find(
    (file) => !file.includes(`${join(outDir, "assets")}/`),
  );

  const globals = globalThis as unknown as { __clinicProbe?: ProbeResult };
  delete globals.__clinicProbe;
  await import(`${pathToFileURL(artifact as string).href}?v=${Date.now()}`);
  const result: ProbeResult | undefined = globals.__clinicProbe;
  delete globals.__clinicProbe;
  if (!result) throw new Error("probe artifact did not run");

  return { code, result };
}

describe("native build transport", () => {
  it("enables the Frappe transport and blocks legacy routes", async () => {
    const { code, result } = await probe("native");

    expect(result.native).toBe(true);
    expect(result.legacy).toBe("UNSUPPORTED");
    for (const method of NATIVE_METHODS) expect(code).toContain(method);
  }, 120_000);

  it("stays on the proxy transport in the default build", async () => {
    const { result } = await probe("production");

    expect(result.native).toBe(false);
    expect(result.legacy).toBe("allowed");
  }, 120_000);

  it("cannot be changed by the shell environment", async () => {
    const previous = process.env.VITE_CLINIC_NATIVE;
    process.env.VITE_CLINIC_NATIVE = "0";
    try {
      expect((await probe("native")).result.native).toBe(true);
      expect((await probe("production")).result.native).toBe(false);
    } finally {
      if (previous === undefined) delete process.env.VITE_CLINIC_NATIVE;
      else process.env.VITE_CLINIC_NATIVE = previous;
    }
  }, 120_000);
});
