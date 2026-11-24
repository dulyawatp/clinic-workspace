import {
  type CapabilityMap,
  getCapability,
  type RoleCode,
} from "../../domain/capability";
import type { SessionDTO } from "../../domain/session";
import { nativeTransportEnabled } from "../../transport/native/flag";

/** Presentation gate only; native Frappe authorizes the directory read. */
export function canViewOwnerStaff(
  mode: SessionDTO["mode"],
  activeRoles: readonly RoleCode[],
  capabilities: CapabilityMap,
): boolean {
  return (
    nativeTransportEnabled() &&
    mode === "live" &&
    activeRoles.length === 1 &&
    activeRoles[0] === "MGR" &&
    getCapability(capabilities, "write.owner.staff") === "enabled"
  );
}
