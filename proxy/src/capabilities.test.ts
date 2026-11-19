import contractNames from "@clinic/contracts/capabilities.json" with {
  type: "json",
};
import { describe, expect, it } from "vitest";
import {
  buildCapabilityMap,
  CAPABILITY_NAMES,
  type Capability,
  type CapabilityMap,
  getCapability,
  isCapability,
} from "./capabilities.js";

describe("capability contract", () => {
  it("matches the shared capability contract exactly", () => {
    expect([...CAPABILITY_NAMES].sort()).toEqual([...contractNames].sort());
    expect(new Set(CAPABILITY_NAMES).size).toBe(CAPABILITY_NAMES.length);
  });

  it("rejects an unknown capability at compile time", () => {
    // @ts-expect-error - not part of the closed Capability union
    const invalid: Capability = "write.patient.delete";
    expect(invalid).toBe("write.patient.delete");
  });

  it("emits every contract capability and defaults to read_only", () => {
    const map = buildCapabilityMap(new Set());
    expect(Object.keys(map)).toEqual(contractNames);
    expect(Object.values(map).every((state) => state === "read_only")).toBe(
      true,
    );
  });

  it("keeps visit invoice writes read-only even when flags are active", () => {
    const names = ["write.visit_invoice.submit", "write.visit_invoice.discount"];
    const map = buildCapabilityMap(new Set(contractNames));
    for (const name of names) {
      expect(contractNames).toContain(name);
      expect(isCapability(name)).toBe(true);
      expect(map[name as Capability]).toBe("read_only");
    }
  });

  it("does not treat active flags as verified contracts", () => {
    const map = buildCapabilityMap(
      new Set([...contractNames, "write.patient.delete"]),
    );
    expect(Object.values(map).every((state) => state === "read_only")).toBe(
      true,
    );
    expect(Object.keys(map)).toEqual(contractNames);
    expect(map["write.patient.create"]).toBe("read_only");
    expect(isCapability("write.patient.delete")).toBe(false);
    expect(CAPABILITY_NAMES.every(isCapability)).toBe(true);
  });

  it("fails closed for malformed runtime names, states and maps", () => {
    for (const map of [
      null,
      [],
      { "write.patient.create": "invalid" },
      Object.create({ "write.patient.create": "enabled" }),
    ]) {
      expect(getCapability(map as CapabilityMap, "write.patient.create")).toBe(
        "read_only",
      );
    }
    expect(
      getCapability(
        { "write.patient.delete": "enabled" } as CapabilityMap,
        "write.patient.delete" as Capability,
      ),
    ).toBe("read_only");
  });
});
