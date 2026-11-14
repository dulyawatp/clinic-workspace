import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { App } from "./App";
import { ROUTES } from "./routes";
import { SessionProvider, useSession } from "./SessionProvider";

const okSession = {
  sessionEpoch: "test-session",
  user: { id: "u1", displayName: "Test", roles: ["REC"] },
  capabilities: {},
};

function jsonResponse(body: unknown, status: number): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  } as Response;
}

beforeEach(() => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => jsonResponse(okSession, 200)),
  );
});
afterEach(() => vi.unstubAllGlobals());

it("renders the authenticated shell placeholder route", async () => {
  window.history.pushState({}, "", ROUTES.home);
  render(<App />);
  expect(await screen.findByTestId("shell-placeholder")).toBeVisible();
});
it("uses ROUTES constants rather than hardcoded paths", () => {
  expect(ROUTES.login).toBe("/clinic/login");
  expect(ROUTES.home).toBe("/clinic");
});
function SessionProbe() {
  const { status } = useSession();
  return <span>{status}</span>;
}
it("exposes an anonymous session after a 401", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => jsonResponse({ code: "AUTH_EXPIRED" }, 401)),
  );
  render(
    <SessionProvider>
      <SessionProbe />
    </SessionProvider>,
  );
  await waitFor(() => expect(screen.getByText("anonymous")).toBeVisible());
});

it("requests the session without HTTP caching", async () => {
  const fetchMock = vi.fn(async () => jsonResponse(okSession, 200));
  vi.stubGlobal("fetch", fetchMock);

  render(
    <SessionProvider>
      <SessionProbe />
    </SessionProvider>,
  );

  await waitFor(() => expect(screen.getByText("authenticated")).toBeVisible());
  expect(fetchMock).toHaveBeenCalledWith(
    "/api/session",
    expect.objectContaining({
      cache: "no-store",
      credentials: "same-origin",
    }),
  );
});

function RefreshProbe() {
  const { status, refresh } = useSession();
  return (
    <div>
      <span>{status}</span>
      <button
        type="button"
        onClick={() => {
          void refresh();
        }}
      >
        refresh
      </button>
    </div>
  );
}

it("becomes anonymous after logout and refresh", async () => {
  const fetchMock = vi
    .fn()
    .mockResolvedValueOnce(jsonResponse(okSession, 200))
    .mockResolvedValueOnce(jsonResponse({ code: "AUTH_EXPIRED" }, 401));
  vi.stubGlobal("fetch", fetchMock);

  render(
    <SessionProvider>
      <RefreshProbe />
    </SessionProvider>,
  );

  await waitFor(() => expect(screen.getByText("authenticated")).toBeVisible());
  fireEvent.click(screen.getByRole("button", { name: "refresh" }));
  await waitFor(() => expect(screen.getByText("anonymous")).toBeVisible());
});
