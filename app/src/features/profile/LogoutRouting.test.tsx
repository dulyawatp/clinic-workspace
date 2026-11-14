import { fireEvent, screen, waitFor } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { expect, it, vi } from "vitest";
import { appRoutes } from "../../app/router";
import { ROUTES } from "../../app/routes";
import { server } from "../../test/msw/server";
import { renderWithDataRouter } from "../../test/render";

/**
 * Runs the real route tree, where `RequireSession` wraps the shell and the
 * outlet. Revocation therefore unmounts the whole profile route, so nothing
 * inside ProfilePage can hold logout feedback across the reconciliation.
 */
function renderProfile() {
  return renderWithDataRouter(appRoutes, {
    role: "REC",
    initialEntries: [ROUTES.profile],
    profileCompany: "Old company",
  });
}

function signOutButton() {
  return screen.getByRole("button", { name: "ออกจากระบบ" });
}

it("failed logout with same-session reconciliation still shows safe feedback in the real router", async () => {
  const { router } = renderProfile();
  await screen.findAllByText("Old company");
  // The proxy refuses the POST but the session still exists upstream, so the
  // reconciliation GET confirms the very same identity.
  let posts = 0;
  server.use(
    http.post("/api/auth/logout", () => {
      posts += 1;
      return HttpResponse.json(
        { code: "FORBIDDEN", message: "private trace" },
        { status: 403 },
      );
    }),
  );
  const requests = vi.spyOn(globalThis, "fetch");
  fireEvent.click(signOutButton());
  expect(await screen.findByRole("alert")).toHaveTextContent(
    "บัญชีนี้ไม่มีสิทธิ์ดำเนินการนี้",
  );
  expect(screen.queryByText("private trace")).toBeNull();
  expect(await screen.findByTestId("profile-name")).toBeVisible();
  expect(router.state.location.pathname).toBe(ROUTES.profile);
  expect(posts).toBe(1);
  expect(
    requests.mock.calls.filter(([url]) => url === "/api/auth/logout"),
  ).toHaveLength(1);
  expect(
    requests.mock.calls.filter(([url]) => url === "/api/session"),
  ).toHaveLength(1);
  // Still authenticated, so the sign-out stays available for a deliberate retry.
  await waitFor(() => expect(signOutButton()).toBeEnabled());
});

it("failed logout reconciliation that confirms another identity leaks no feedback", async () => {
  const { router } = renderProfile();
  await screen.findAllByText("Old company");
  server.use(
    http.post("/api/auth/logout", () =>
      HttpResponse.json(
        { code: "FORBIDDEN", message: "private trace" },
        { status: 403 },
      ),
    ),
    // The reconciliation GET itself confirms a replacement session.
    http.get("/api/session", () =>
      HttpResponse.json({
        sessionEpoch: "replacement-session",
        user: { id: "new-staff", displayName: "New staff", roles: ["REC"] },
        capabilities: {},
      }),
    ),
  );
  fireEvent.click(signOutButton());
  await waitFor(() =>
    expect(screen.getByTestId("profile-name")).toHaveTextContent("New staff"),
  );
  expect(screen.queryByRole("alert")).toBeNull();
  expect(screen.queryByText("private trace")).toBeNull();
  expect(router.state.location.pathname).toBe(ROUTES.profile);
});
