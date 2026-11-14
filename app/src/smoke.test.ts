import { describe, expect, it } from "vitest";

describe("workspace", () => {
  it("runs vitest with jsdom", () => {
    expect(typeof document).toBe("object");
  });
});
