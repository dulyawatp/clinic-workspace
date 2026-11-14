import access from "@clinic/contracts/fixtures/access.json";
import type { StaticRouteKey } from "../../app/routes";
import {
  type Capability,
  type CapabilityMap,
  getCapability,
  isRoleCode,
  type RoleCode,
} from "../../domain/capability";

/**
 * Nav visibility for a surface is read from the same interim matrix the proxy
 * and MSW enforce, so a role cannot be advertised a destination its read
 * resource denies. Still presentation only: never authorization.
 */
function resourceRoles(resource: keyof typeof access.resources): RoleCode[] {
  return access.resources[resource].filter(isRoleCode);
}

export interface NavItem {
  id: string;
  labelKey: string;
  /** Static path key only: a route builder is never a nav destination. */
  routeKey: StaticRouteKey;
  icon: string;
  /** Provisional presentation only, never backend authorization. */
  roles?: readonly RoleCode[];
  requiredCapability?: Capability;
  owner: "02" | "03" | "04" | "05";
}

export const NAV_MANIFEST: readonly NavItem[] = [
  {
    id: "finance",
    labelKey: "finance.title",
    routeKey: "finance",
    icon: "checkout",
    roles: ["MGR", "CAS"],
    owner: "04",
  },
  {
    id: "pharmacy",
    labelKey: "pharmacy.title",
    routeKey: "pharmacy",
    icon: "pharmacy",
    roles: ["PHA"],
    owner: "04",
  },
  {
    id: "live-photos",
    labelKey: "livePhotos.title",
    routeKey: "livePhotos",
    icon: "patients",
    roles: ["DOC", "NUR"],
    owner: "04",
  },
  {
    id: "home",
    labelKey: "nav.today",
    routeKey: "home",
    icon: "home",
    owner: "02",
  },
  {
    id: "search",
    labelKey: "nav.search",
    routeKey: "search",
    icon: "search",
    owner: "02",
  },
  {
    id: "patients",
    labelKey: "nav.patients",
    routeKey: "patients",
    icon: "patients",
    roles: resourceRoles("frontdesk-patients"),
    requiredCapability: "read.patient",
    owner: "03",
  },
  {
    id: "reception",
    labelKey: "nav.reception",
    routeKey: "reception",
    icon: "reception",
    // REC-01 is a reception-only surface (§2), not a read-matrix decision.
    roles: ["REC"],
    requiredCapability: "read.appointment",
    owner: "03",
  },
  {
    id: "queue",
    labelKey: "queue.title",
    routeKey: "queue",
    icon: "queue",
    roles: resourceRoles("frontdesk-queue"),
    requiredCapability: "read.appointment",
    owner: "03",
  },
  {
    id: "checkout",
    labelKey: "checkout.title",
    routeKey: "checkout",
    icon: "checkout",
    roles: resourceRoles("frontdesk-checkouts"),
    requiredCapability: "read.billing",
    owner: "03",
  },
  {
    id: "appointments",
    labelKey: "appointment.calendar",
    routeKey: "appointments",
    icon: "appointments",
    roles: ["REC", "DOC"],
    requiredCapability: "read.appointment",
    owner: "03",
  },
  {
    id: "profile",
    labelKey: "nav.profile",
    routeKey: "profile",
    icon: "profile",
    owner: "02",
  },
  {
    id: "system",
    labelKey: "nav.system",
    routeKey: "system",
    icon: "system",
    roles: ["ADM", "MGR"],
    requiredCapability: "read.admin",
    owner: "02",
  },
  {
    id: "visit",
    labelKey: "clinical.title",
    routeKey: "visitPattern",
    icon: "patients",
    // Addressed route only: no encounter exists for a global navigation link.
    roles: [],
    owner: "04",
  },
];

export function navFor(
  roles: readonly RoleCode[],
  caps: CapabilityMap,
  mode: "live" | "fixture" = "fixture",
): NavItem[] {
  const roleList = Array.isArray(roles) ? roles : [];
  return NAV_MANIFEST.filter((item) => {
    if (item.roles && !item.roles.some((role) => roleList.includes(role)))
      return false;
    const state = item.requiredCapability
      ? getCapability(caps, item.requiredCapability)
      : "read_only";
    if (
      item.id === "live-photos" ||
      item.id === "pharmacy" ||
      item.id === "finance"
    )
      return mode === "live";
    return state !== "denied" && state !== "blocked_unverified";
  });
}
