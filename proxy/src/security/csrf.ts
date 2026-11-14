import { createHash, randomBytes } from "node:crypto";

export type CsrfPurpose = "login" | "session";

/** Returns a single-use plaintext token; only its hash is ever stored. */
export function issueToken(): string {
  return randomBytes(32).toString("base64url");
}

export function hashToken(token: string): string {
  return createHash("sha256").update(token).digest("hex");
}

/** Stable, non-reversible client identifier for logs. */
export function hashIp(ip: string): string {
  return hashToken(ip).slice(0, 16);
}
