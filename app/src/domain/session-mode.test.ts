import { expect, it } from "vitest";
import { isSessionDTO } from "./session";

it("accepts live, fixture and legacy sessions, but rejects invalid modes", () => {
  const session = {
    sessionEpoch: "epoch",
    user: { id: "doctor", displayName: "Doctor", roles: ["DOC"] },
    capabilities: {},
  };
  expect(isSessionDTO(session)).toBe(true);
  expect(isSessionDTO({ ...session, mode: "fixture" })).toBe(true);
  expect(isSessionDTO({ ...session, mode: "live" })).toBe(true);
  expect(isSessionDTO({ ...session, mode: "other" })).toBe(false);
  expect(isSessionDTO({ ...session, mode: null })).toBe(false);
});
