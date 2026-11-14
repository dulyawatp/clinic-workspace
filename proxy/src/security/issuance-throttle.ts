import {
  CSRF_ISSUANCE_LIMIT_PER_CLIENT,
  CSRF_ISSUANCE_WINDOW_MS,
  MAX_ISSUANCE_BUCKETS,
} from "./constants.js";

export interface IssuanceThrottleOptions {
  limit?: number;
  windowMs?: number;
  maxClients?: number;
  now?: () => number;
}

interface Bucket {
  count: number;
  resetAt: number;
}

/**
 * Per-caller throttle for opening new anonymous CSRF handshakes.
 *
 * The caller key must be a trusted, server-observed value (the socket address
 * hashed by the caller); forwarded headers are never used. State is an
 * in-memory map bounded by `maxClients`; expired buckets are reclaimed on every
 * acquisition. At the bound a new caller is rejected (fail closed) rather than
 * growing memory, so repeated abusive traffic cannot evict or exhaust the map.
 */
export class IssuanceThrottle {
  private readonly buckets = new Map<string, Bucket>();
  private readonly limit: number;
  private readonly windowMs: number;
  private readonly maxClients: number;
  private readonly now: () => number;

  constructor(options: IssuanceThrottleOptions = {}) {
    this.limit = options.limit ?? CSRF_ISSUANCE_LIMIT_PER_CLIENT;
    this.windowMs = options.windowMs ?? CSRF_ISSUANCE_WINDOW_MS;
    this.maxClients = options.maxClients ?? MAX_ISSUANCE_BUCKETS;
    this.now = options.now ?? (() => Date.now());
  }

  /**
   * Returns true when the caller may open one more handshake in this window.
   * Consumes a slot on success; callers should not consume a slot for requests
   * that reuse an existing pre-session.
   */
  tryAcquire(clientKey: string): boolean {
    const now = this.now();
    this.purgeExpired(now);

    const existing = this.buckets.get(clientKey);
    if (existing !== undefined) {
      if (existing.count >= this.limit) {
        return false;
      }
      existing.count += 1;
      return true;
    }

    if (this.buckets.size >= this.maxClients) {
      return false;
    }

    this.buckets.set(clientKey, { count: 1, resetAt: now + this.windowMs });
    return true;
  }

  /** Number of tracked callers; used to prove the limiter state stays bounded. */
  get trackedClients(): number {
    return this.buckets.size;
  }

  private purgeExpired(now: number): void {
    for (const [key, bucket] of this.buckets) {
      if (bucket.resetAt <= now) {
        this.buckets.delete(key);
      }
    }
  }
}
