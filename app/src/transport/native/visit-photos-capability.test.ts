import { afterEach, expect, it, vi } from "vitest";
import { readVisitPhotoPair } from "./visit-photos";

vi.mock("./session", () => ({ nativeCsrfToken: () => "csrf" }));
afterEach(() => vi.restoreAllMocks());

it.each([[undefined, false], [true, true]] as const)("decodes capability %s", async (flag, expected) => {
  vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify({ message: {
    appointment: "APT", before: null, after: null, ...(flag && { drawings_enabled: flag }),
  } }), { status: 200 }));
  expect(await readVisitPhotoPair("APT", new AbortController().signal)).toMatchObject({ drawingsEnabled: expected });
});

it("rejects non-true capability", async () => {
  vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify({ message: {
    appointment: "APT", before: null, after: null, drawings_enabled: false,
  } }), { status: 200 }));
  expect(await readVisitPhotoPair("APT", new AbortController().signal)).toBeNull();
});
