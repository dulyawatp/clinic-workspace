import { screen, waitFor } from "@testing-library/react";
import type { RouteObject } from "react-router-dom";
import { describe, expect, it } from "vitest";
import { canAccessSurface, SURFACES, type SurfaceId } from "../design/surfaces";
import type { RoleCode } from "../domain/capability";
import { renderWithDataRouter } from "../test/render";
import { appRoutes } from "./router";
import { RoleGuard } from "./role-guard";
import { ROUTES } from "./routes";
import { SURFACE_ROUTES } from "./surface-routes";

const ROLES: readonly RoleCode[] = [
  "REC",
  "DOC",
  "NUR",
  "PHA",
  "CAS",
  "MGR",
  "ADM",
];

/**
 * The design manifest's per-surface role list, copied by hand from
 * `handoff/clinic-surface-manifest.json`. It is the assertion, not a
 * re-derivation of the code under test.
 */
const DESIGN_ROLES: Readonly<Record<SurfaceId, readonly RoleCode[]>> = {
  "APP-01": ROLES,
  "APP-02": ROLES,
  "APP-03": ROLES,
  "APP-04": ROLES,
  "PAT-01": ["REC", "DOC", "NUR", "ADM"],
  "PAT-02": ["REC", "ADM"],
  "PAT-03": ROLES,
  "PAT-04": ["REC", "DOC", "NUR"],
  "PAT-05": ["DOC", "NUR"],
  "REC-01": ["REC"],
  "REC-02": ["REC", "NUR"],
  "REC-03": ["REC", "DOC"],
  "REC-04": ["REC", "DOC"],
  "REC-05": ["REC", "CAS"],
  "DOC-01": ["DOC"],
  "DOC-02": ["DOC", "NUR"],
  "DOC-03": ["DOC", "NUR", "PHA"],
  "DOC-04": ["DOC", "NUR"],
  "NUR-01": ["NUR"],
  "NUR-02": ["NUR", "DOC"],
  "PHA-01": ["PHA"],
  "PHA-02": ["PHA"],
  "PHA-03": ["PHA", "MGR"],
  "PHA-04": ["PHA", "MGR"],
  "PHA-05": ["PHA"],
  "PHA-06": ["PHA", "DOC", "ADM"],
  "PHA-07": ["PHA", "MGR"],
  "CAS-01": ["CAS", "REC"],
  "CAS-02": ["CAS", "MGR"],
  "CAS-03": ["CAS", "MGR"],
  "MGR-01": ["MGR"],
  "MGR-02": ["MGR"],
  "ADM-01": ["ADM"],
  "ADM-02": ["ADM"],
  "ADM-03": ["ADM"],
  "ADM-04": ["ADM", "MGR"],
  "ADM-05": ["ADM"],
};

function protectedChildren(): RouteObject[] {
  return appRoutes[0]?.children?.[1]?.children ?? [];
}

describe("designed surface matrix", () => {
  it("covers all 37 designed surfaces", () => {
    expect(SURFACES.map((surface) => surface.id).sort()).toEqual(
      Object.keys(DESIGN_ROLES).sort(),
    );
  });

  it("matches the design role list for every surface and role", () => {
    for (const surface of SURFACES) {
      for (const role of ROLES) {
        expect(
          canAccessSurface(surface.id, [role]),
          `${surface.id} × ${role}`,
        ).toBe(DESIGN_ROLES[surface.id].includes(role));
      }
    }
  });

  it("lets a multi-role user through when any role is designed for the surface", () => {
    expect(canAccessSurface("CAS-02", ["DOC", "MGR"])).toBe(true);
    expect(canAccessSurface("CAS-02", ["DOC", "NUR"])).toBe(false);
    expect(canAccessSurface("DOC-01", [])).toBe(false);
  });

  it("routes every authenticated surface behind a role guard", () => {
    const guarded = protectedChildren()
      .filter((route) => {
        const element = route.element as { type?: unknown } | null;
        return element?.type === RoleGuard;
      })
      .map(
        (route) =>
          (route.element as { props: { surface: SurfaceId } }).props.surface,
      );
    const designed = SURFACE_ROUTES.map((route) => route.surface);
    expect(guarded.slice(0, designed.length)).toEqual(designed);
    // Legacy addresses are guarded too, against the designed surface they alias.
    for (const extra of guarded.slice(designed.length))
      expect(designed, extra).toContain(extra);

    const routed = new Set(SURFACE_ROUTES.map((route) => route.surface));
    for (const surface of SURFACES) {
      if (surface.preAuth) continue;
      expect(routed, surface.id).toContain(surface.id);
    }
  });

  it("keeps the verified native flows open to the roles that run them", () => {
    for (const [surface, roles] of [
      ["PAT-01", ["REC", "DOC", "NUR", "ADM"]],
      ["PAT-03", ["REC", "DOC", "NUR"]],
      ["PAT-04", ["REC", "DOC", "NUR"]],
      ["REC-03", ["REC", "DOC"]],
      ["REC-04", ["REC", "DOC"]],
      ["PHA-01", ["PHA"]],
    ] as const)
      for (const role of roles)
        expect(canAccessSurface(surface, [role]), `${surface} ${role}`).toBe(
          true,
        );
  });
});

describe("role guard presentation", () => {
  it("renders the surface for a designed role", () => {
    renderWithDataRouter(appRoutes, {
      initialEntries: [ROUTES.management],
      role: "MGR",
    });
    expect(screen.queryByTestId("role-guard-denied")).toBeNull();
  });

  it("states permission denied with a recovery path instead of redirecting", async () => {
    const { router } = renderWithDataRouter(appRoutes, {
      initialEntries: [ROUTES.management],
      role: "DOC",
    });
    const denied = await screen.findByTestId("role-guard-denied");
    expect(denied).toHaveAttribute("data-surface", "MGR-01");
    expect(router.state.location.pathname).toBe(ROUTES.management);
    expect(
      screen.getByRole("heading", { name: "ภาพรวมคลินิก" }),
    ).toBeVisible();
    expect(
      screen.getByRole("link", { name: "ไปหน้าหลักของฉัน" }),
    ).toHaveAttribute("href", ROUTES.home);
  });

  it("narrows a legacy entry to the roles that own it while keeping the surface's denial state", async () => {
    // PAT-04 lists REC, DOC and NUR; the legacy lab-photo entry only ever
    // belonged to the clinicians, so REC is refused with the same state.
    const { router } = renderWithDataRouter(appRoutes, {
      initialEntries: [ROUTES.livePhotos],
      role: "REC",
      sessionMode: "live",
    });
    expect(await screen.findByTestId("role-guard-denied")).toHaveAttribute(
      "data-surface",
      "PAT-04",
    );
    expect(router.state.location.pathname).toBe(ROUTES.livePhotos);
  });

  it("lets the owning role through a narrowed entry", async () => {
    const { router } = renderWithDataRouter(appRoutes, {
      initialEntries: [ROUTES.livePhotos],
      role: "NUR",
      sessionMode: "live",
    });
    await waitFor(() =>
      expect(router.state.location.pathname).toBe(ROUTES.home),
    );
    expect(screen.queryByTestId("role-guard-denied")).toBeNull();
  });
});
