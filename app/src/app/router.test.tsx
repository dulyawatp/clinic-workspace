import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { StrictMode } from "react";
import {
  createMemoryRouter,
  Link,
  type RouteObject,
  useBlocker,
  useLocation,
  useNavigate,
} from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { LocaleProvider } from "../i18n/LocaleProvider";
import { createHandlers } from "../test/msw/handlers";
import { server } from "../test/msw/server";
import { renderWithDataRouter } from "../test/render";
import { App } from "./App";
import { RETURN_TO_KEY } from "./return-to";
import { appRoutes, RouteErrorFallback } from "./router";
import { ROUTES } from "./routes";

beforeEach(() => sessionStorage.removeItem(RETURN_TO_KEY));
afterEach(() => {
  vi.restoreAllMocks();
  sessionStorage.removeItem(RETURN_TO_KEY);
});

describe("app data router", () => {
  it("resolves the preserved route tree", () => {
    const patient = createMemoryRouter(appRoutes, {
      initialEntries: [ROUTES.patient("PAT-1")],
    });
    expect(patient.state.matches.at(-1)?.route.path).toBe(
      `${ROUTES.patients}/:id`,
    );
    expect(patient.state.matches.at(-1)?.params.id).toBe("PAT-1");

    const login = createMemoryRouter(appRoutes, {
      initialEntries: [ROUTES.login],
    });
    expect(login.state.matches.at(-1)?.route.path).toBe(ROUTES.login);
  });

  it("renders the authenticated home route inside the shell", async () => {
    renderWithDataRouter(appRoutes, {
      initialEntries: [ROUTES.home],
      role: "REC",
    });
    expect(await screen.findByTestId("shell-placeholder")).toBeVisible();
  });

  it("renders the login route without the protected shell", async () => {
    renderWithDataRouter(appRoutes, {
      initialEntries: [ROUTES.login],
      authed: false,
    });
    expect(await screen.findByLabelText("ชื่อผู้ใช้")).toBeVisible();
    expect(screen.queryByTestId("shell-placeholder")).toBeNull();
  });

  it("redirects an anonymous protected route to login and keeps a safe target", async () => {
    const { router } = renderWithDataRouter(appRoutes, {
      initialEntries: [
        `${ROUTES.home}?q=สมชาย&status=waiting&phone=0812345678`,
      ],
      authed: false,
    });
    await screen.findByLabelText("ชื่อผู้ใช้");
    await waitFor(() =>
      expect(
        router.state.location.pathname + router.state.location.search,
      ).toBe(`${ROUTES.login}?reason=expired`),
    );
    expect(sessionStorage.getItem(RETURN_TO_KEY)).toBe(
      `${ROUTES.home}?status=waiting`,
    );
  });
});

const SENSITIVE_ERROR =
  "PAT-12345 สมชาย 0812345678 at /app/src/features/secret.tsx";

function ThrowSensitive(): never {
  throw new Error(SENSITIVE_ERROR);
}

function withThrowingElement(
  routes: RouteObject[],
  path: string,
): RouteObject[] {
  return routes.map((route) => {
    if (route.path === path) return { ...route, element: <ThrowSensitive /> };
    if ("children" in route && route.children) {
      return {
        ...route,
        children: withThrowingElement(route.children, path),
      };
    }
    return route;
  });
}

function withThrowingLoader(
  routes: RouteObject[],
  path: string,
): RouteObject[] {
  return routes.map((route) => {
    if (route.path === path) {
      return {
        ...route,
        loader: () => {
          throw new Error(SENSITIVE_ERROR);
        },
      };
    }
    if ("children" in route && route.children) {
      return {
        ...route,
        children: withThrowingLoader(route.children, path),
      };
    }
    return route;
  });
}

function expectSanitizedAlert(): void {
  expect(screen.getByRole("alert")).toHaveTextContent("เกิดข้อผิดพลาด กรุณาลองใหม่");
  expect(document.body.textContent ?? "").not.toMatch(
    /PAT-12345|สมชาย|0812345678|secret\.tsx|Error:/,
  );
}

describe("route error fallback", () => {
  it("sanitizes a protected-route render error", async () => {
    // React reports render errors through jsdom's window error event; keeping
    // it from printing does not affect the rendered fallback under test.
    const silence = (event: ErrorEvent) => event.preventDefault();
    window.addEventListener("error", silence);
    vi.spyOn(console, "error").mockImplementation(() => {});
    try {
      renderWithDataRouter(withThrowingElement(appRoutes, ROUTES.home), {
        initialEntries: [ROUTES.home],
      });
      await screen.findByRole("alert");
      expectSanitizedAlert();
    } finally {
      window.removeEventListener("error", silence);
    }
  });

  it("sanitizes a protected-route loader error", async () => {
    renderWithDataRouter(withThrowingLoader(appRoutes, ROUTES.home), {
      initialEntries: [ROUTES.home],
    });
    await screen.findByRole("alert");
    expectSanitizedAlert();
  });

  it("sanitizes a login-route loader error", async () => {
    renderWithDataRouter(withThrowingLoader(appRoutes, ROUTES.login), {
      initialEntries: [ROUTES.login],
      authed: false,
    });
    await screen.findByRole("alert");
    expectSanitizedAlert();
  });

  it("sanitizes an unmatched path instead of showing router internals", async () => {
    renderWithDataRouter(appRoutes, {
      initialEntries: ["/clinic/not-a-route"],
    });
    await screen.findByRole("alert");
    expectSanitizedAlert();
  });

  it("uses the active locale catalogue message", () => {
    render(
      <LocaleProvider initial="en">
        <RouteErrorFallback />
      </LocaleProvider>,
    );
    expect(screen.getByRole("alert")).toHaveTextContent(
      "Something went wrong. Try again.",
    );
  });
});

describe("browser router lifecycle", () => {
  it("creates one subscribed router under StrictMode and disposes it on unmount", async () => {
    window.history.pushState({}, "", ROUTES.home);
    server.use(...createHandlers());
    const add = vi.spyOn(window, "addEventListener");
    const remove = vi.spyOn(window, "removeEventListener");

    const { unmount } = render(
      <StrictMode>
        <App />
      </StrictMode>,
    );
    expect(await screen.findByTestId("shell-placeholder")).toBeVisible();

    const popstateAdds = add.mock.calls.filter(([type]) => type === "popstate");
    expect(popstateAdds).toHaveLength(1);
    const [, handler] = popstateAdds[0] ?? [];

    unmount();
    await waitFor(() =>
      expect(remove).toHaveBeenCalledWith("popstate", handler),
    );
  });
});

const BLOCK = { a: "/block/a", b: "/block/b" } as const;

function Blockable({ dirty }: { dirty: boolean }) {
  const location = useLocation();
  const navigate = useNavigate();
  const blocker = useBlocker(
    ({ currentLocation, nextLocation }) =>
      dirty && currentLocation.pathname !== nextLocation.pathname,
  );

  return (
    <div>
      <span data-testid="path">{location.pathname}</span>
      <Link to={BLOCK.b}>to b</Link>
      <button type="button" onClick={() => navigate(-1)}>
        back
      </button>
      {blocker.state === "blocked" ? (
        <>
          <button type="button" onClick={() => blocker.reset()}>
            stay
          </button>
          <button type="button" onClick={() => blocker.proceed()}>
            leave
          </button>
        </>
      ) : null}
    </div>
  );
}

function blockRoutes(dirty: boolean): RouteObject[] {
  return [
    { path: BLOCK.a, element: <Blockable dirty={dirty} /> },
    { path: BLOCK.b, element: <Blockable dirty={dirty} /> },
  ];
}

describe("dirty draft navigation blocking", () => {
  it("blocks a link navigation while dirty and stays on cancel", async () => {
    const user = userEvent.setup();
    const { router } = renderWithDataRouter(blockRoutes(true), {
      initialEntries: [BLOCK.a],
    });

    await user.click(screen.getByRole("link", { name: "to b" }));
    await screen.findByRole("button", { name: "stay" });
    expect(screen.getByTestId("path")).toHaveTextContent(BLOCK.a);

    await user.click(screen.getByRole("button", { name: "stay" }));
    await waitFor(() =>
      expect(screen.queryByRole("button", { name: "stay" })).toBeNull(),
    );
    expect(router.state.location.pathname).toBe(BLOCK.a);
    expect(screen.getByTestId("path")).toHaveTextContent(BLOCK.a);
  });

  it("proceeds to the link target after confirmation", async () => {
    const user = userEvent.setup();
    const { router } = renderWithDataRouter(blockRoutes(true), {
      initialEntries: [BLOCK.a],
    });

    await user.click(screen.getByRole("link", { name: "to b" }));
    await user.click(await screen.findByRole("button", { name: "leave" }));

    await waitFor(() => expect(router.state.location.pathname).toBe(BLOCK.b));
    expect(screen.getByTestId("path")).toHaveTextContent(BLOCK.b);
  });

  it("blocks browser back while dirty and proceeds after confirmation", async () => {
    const user = userEvent.setup();
    const { router } = renderWithDataRouter(blockRoutes(true), {
      initialEntries: [BLOCK.a, BLOCK.b],
      initialIndex: 1,
    });
    expect(window.location.pathname).toBe(BLOCK.b);

    await user.click(screen.getByRole("button", { name: "back" }));
    await screen.findByRole("button", { name: "stay" });
    expect(router.state.location.pathname).toBe(BLOCK.b);

    await user.click(screen.getByRole("button", { name: "leave" }));
    await waitFor(() => expect(router.state.location.pathname).toBe(BLOCK.a));
    expect(screen.getByTestId("path")).toHaveTextContent(BLOCK.a);
  });

  it("re-blocks back after a cancel and proceeds on the second attempt", async () => {
    const user = userEvent.setup();
    const { router } = renderWithDataRouter(blockRoutes(true), {
      initialEntries: [BLOCK.a, BLOCK.b],
      initialIndex: 1,
    });

    await user.click(screen.getByRole("button", { name: "back" }));
    await screen.findByRole("button", { name: "stay" });
    await user.click(screen.getByRole("button", { name: "stay" }));
    await waitFor(() =>
      expect(screen.queryByRole("button", { name: "stay" })).toBeNull(),
    );
    expect(router.state.location.pathname).toBe(BLOCK.b);

    await user.click(screen.getByRole("button", { name: "back" }));
    await screen.findByRole("button", { name: "stay" });
    expect(router.state.location.pathname).toBe(BLOCK.b);

    await user.click(screen.getByRole("button", { name: "leave" }));
    await waitFor(() => expect(router.state.location.pathname).toBe(BLOCK.a));
    expect(screen.getByTestId("path")).toHaveTextContent(BLOCK.a);
  });

  it("does not block navigation when the draft is clean", async () => {
    const user = userEvent.setup();
    const { router } = renderWithDataRouter(blockRoutes(false), {
      initialEntries: [BLOCK.a],
    });

    await user.click(screen.getByRole("link", { name: "to b" }));

    await waitFor(() => expect(router.state.location.pathname).toBe(BLOCK.b));
    expect(screen.queryByRole("button", { name: "stay" })).toBeNull();
  });
});
