import contractNames from "@clinic/contracts/capabilities.json";
import { describe, expect, it } from "vitest";
import {
  CAPABILITY_NAMES,
  type Capability,
  type CapabilityMap,
  getCapability,
  isCapability,
} from "./capability";

describe("capability descriptor", () => {
  it("defaults missing capabilities to read_only", () => {
    expect(getCapability({}, "write.patient.create")).toBe("read_only");
  });

  it.each([
    "enabled",
    "read_only",
    "blocked_unverified",
    "denied",
    "error_upstream",
  ] as const)("preserves explicit %s state", (state) => {
    expect(
      getCapability({ "write.patient.create": state }, "write.patient.create"),
    ).toBe(state);
  });

  it("matches the shared ordered capability contract exactly", () => {
    expect(CAPABILITY_NAMES).toEqual(contractNames);
    expect(new Set(CAPABILITY_NAMES).size).toBe(contractNames.length);
    expect(CAPABILITY_NAMES.every(isCapability)).toBe(true);
    expect(isCapability("write.patient.delete")).toBe(false);
    expect(isCapability("toString")).toBe(false);
  });

  it("rejects unknown names at compile time", () => {
    // @ts-expect-error Closed capability union excludes unapproved operations.
    const invalid: Capability = "write.patient.delete";
    expect(isCapability(invalid)).toBe(false);
  });

  it.each([
    null,
    undefined,
    [],
    "enabled",
    42,
    { "write.patient.create": "unexpected" },
    { "write.patient.create": true },
    { "write.patient.create": {} },
    Object.create({ "write.patient.create": "enabled" }),
  ])("defaults malformed or inherited runtime maps to read_only: %j", (map) => {
    expect(getCapability(map as CapabilityMap, "write.patient.create")).toBe(
      "read_only",
    );
  });

  it("rejects unknown runtime names even when map says enabled", () => {
    const map = { "write.patient.delete": "enabled" } as CapabilityMap;
    expect(getCapability(map, "write.patient.delete" as Capability)).toBe(
      "read_only",
    );
  });
});
