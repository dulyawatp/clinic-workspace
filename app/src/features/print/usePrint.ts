import type { CapabilityMap } from "../../domain/capability";

/** No verified print/export capability exists in this plan's contract. */
export function canPrint(_caps: CapabilityMap): boolean {
  return false;
}

export function usePrint() {
  return { print: (): false => false };
}
