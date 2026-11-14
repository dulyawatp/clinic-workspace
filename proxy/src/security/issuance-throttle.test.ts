import { describe, expect, it } from "vitest";
import { IssuanceThrottle } from "./issuance-throttle.js";

function clock(start = 0) {
  let now = start;
  return {
    now: () => now,
    advance: (ms: number) => {
      now += ms;
    },
  };
}

describe("IssuanceThrottle", () => {
  it("throttles one caller per window independently of others", () => {
    const throttle = new IssuanceThrottle({
      limit: 3,
      windowMs: 1000,
      maxClients: 10,
      now: () => 0,
    });

    expect(throttle.tryAcquire("a")).toBe(true);
    expect(throttle.tryAcquire("a")).toBe(true);
    expect(throttle.tryAcquire("a")).toBe(true);
    expect(throttle.tryAcquire("a")).toBe(false);
    expect(throttle.tryAcquire("b")).toBe(true);
  });

  it("recovers after the window expires", () => {
    const time = clock(1000);
    const throttle = new IssuanceThrottle({
      limit: 1,
      windowMs: 500,
      maxClients: 10,
      now: time.now,
    });

    expect(throttle.tryAcquire("a")).toBe(true);
    expect(throttle.tryAcquire("a")).toBe(false);
    time.advance(501);
    expect(throttle.tryAcquire("a")).toBe(true);
  });

  it("bounds tracked callers and fails closed at the bound", () => {
    const throttle = new IssuanceThrottle({
      limit: 1,
      windowMs: 1000,
      maxClients: 2,
      now: () => 0,
    });

    expect(throttle.tryAcquire("a")).toBe(true);
    expect(throttle.tryAcquire("b")).toBe(true);
    expect(throttle.tryAcquire("c")).toBe(false);
    expect(throttle.trackedClients).toBe(2);
  });

  it("reclaims expired buckets before checking the bound", () => {
    const time = clock(0);
    const throttle = new IssuanceThrottle({
      limit: 1,
      windowMs: 100,
      maxClients: 1,
      now: time.now,
    });

    expect(throttle.tryAcquire("a")).toBe(true);
    expect(throttle.tryAcquire("b")).toBe(false);
    time.advance(101);
    expect(throttle.tryAcquire("b")).toBe(true);
    expect(throttle.trackedClients).toBe(1);
  });

  it("keeps limiter state bounded under caller churn", () => {
    const throttle = new IssuanceThrottle({
      limit: 1,
      windowMs: 1000,
      maxClients: 50,
      now: () => 0,
    });

    for (let i = 0; i < 500; i += 1) {
      throttle.tryAcquire(`caller-${i}`);
    }

    expect(throttle.trackedClients).toBe(50);
  });
});
