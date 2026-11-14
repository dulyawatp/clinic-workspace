import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { BrowserRouter, MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { App } from "../../app/App";
import { ROUTES } from "../../app/routes";
import { SessionProvider, useSession } from "../../app/SessionProvider";
import type { CapabilityMap, RoleCode } from "../../domain/capability";
import { initI18n, type Locale } from "../../i18n";
import { LocaleProvider } from "../../i18n/LocaleProvider";
import { AppShell } from "./AppShell";
import { CapabilityProvider } from "./CapabilityProvider";
import { NAV_MANIFEST, navFor } from "./nav";
import { SidebarNav } from "./SidebarNav";

let width = 1440;
const mediaListeners = new Set<() => void>();
const dialogMethods = ["showModal", "close"] as const;
const originalDialogMethods = dialogMethods.map((method) =>
  Object.getOwnPropertyDescriptor(HTMLDialogElement.prototype, method),
);

function RefreshFixture() {
  const { refresh } = useSession();
  return (
    <button type="button" onClick={() => void refresh()}>
      Refresh fixture
    </button>
  );
}

function response(body: unknown, status = 200) {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  } as Response;
}

function session(role: RoleCode = "REC", caps: CapabilityMap = {}) {
  return {
    sessionEpoch: "test-session",
    user: { id: "staff-1", displayName: "Staff", roles: [role] },
    capabilities: caps,
  };
}

beforeEach(() => {
  width = 1440;
  mediaListeners.clear();
  window.history.replaceState({}, "", ROUTES.home);
  vi.stubGlobal(
    "matchMedia",
    vi.fn((query: string) => ({
      media: query,
      matches: width >= Number(query.match(/\d+/)?.[0]),
      addEventListener: (_event: string, listener: () => void) =>
        mediaListeners.add(listener),
      removeEventListener: (_event: string, listener: () => void) =>
        mediaListeners.delete(listener),
    })),
  );
  Object.defineProperty(HTMLDialogElement.prototype, "showModal", {
    configurable: true,
    value: function (this: HTMLDialogElement) {
      this.setAttribute("open", "");
    },
  });
  Object.defineProperty(HTMLDialogElement.prototype, "close", {
    configurable: true,
    value: function (this: HTMLDialogElement) {
      this.removeAttribute("open");
    },
  });
});
afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  dialogMethods.forEach((method, index) => {
    const original = originalDialogMethods[index];
    if (original)
      Object.defineProperty(HTMLDialogElement.prototype, method, original);
    else Reflect.deleteProperty(HTMLDialogElement.prototype, method);
  });
});

function renderShell({
  role = "REC",
  caps = {},
  locale = "en",
  authed = true,
  mode,
  profile = response({ items: [{ company: "Fixture clinic" }], total: 1 }),
}: {
  role?: RoleCode;
  caps?: CapabilityMap;
  locale?: Locale;
  authed?: boolean;
  mode?: "live" | "fixture";
  profile?: Response;
} = {}) {
  const fetchMock = vi.fn(async (url: string) =>
    url === "/api/session"
      ? response(
          authed ? { ...session(role, caps), ...(mode ? { mode } : {}) } : {},
          authed ? 200 : 401,
        )
      : profile,
  );
  vi.stubGlobal("fetch", fetchMock);
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const view = render(
    <QueryClientProvider client={client}>
      <LocaleProvider initial={locale}>
        <SessionProvider>
          <CapabilityProvider value={caps}>
            <BrowserRouter>
              <AppShell>
                <h1>Page content</h1>
              </AppShell>
            </BrowserRouter>
          </CapabilityProvider>
        </SessionProvider>
      </LocaleProvider>
    </QueryClientProvider>,
  );
  return { ...view, fetchMock, client };
}

describe("shell navigation contract", () => {
  it("resolves only canonical route keys", () => {
    expect(NAV_MANIFEST.map((item) => item.id)).toEqual([
      "finance",
      "pharmacy",
      "live-photos",
      "home",
      "search",
      "patients",
      "reception",
      "queue",
      "checkout",
      "appointments",
      "profile",
      "system",
      "visit",
    ]);
    for (const item of NAV_MANIFEST) {
      expect(Object.values(ROUTES)).toContain(ROUTES[item.routeKey]);
      expect(["02", "03", "04"]).toContain(item.owner);
    }
    expect(navFor(["REC"], {}).map((item) => item.id)).toEqual([
      "home",
      "search",
      "patients",
      "reception",
      "queue",
      "checkout",
      "appointments",
      "profile",
    ]);
  });

  it.each(["ADM", "MGR"] as const)(
    "shows system to %s unless explicitly blocked",
    (role) => {
      expect(navFor([role], {}).some((item) => item.id === "system")).toBe(
        true,
      );
      for (const state of ["denied", "blocked_unverified"] as const) {
        expect(
          navFor([role], { "read.admin": state }).some(
            (item) => item.id === "system",
          ),
        ).toBe(false);
      }
      expect(
        navFor([role], { "read.admin": "error_upstream" }).some(
          (item) => item.id === "system",
        ),
      ).toBe(true);
      expect(
        navFor(["REC"], { "read.admin": "enabled" }).some(
          (item) => item.id === "system",
        ),
      ).toBe(false);
    },
  );

  it("advertises only destinations the interim read matrix allows", () => {
    // The directory reads `frontdesk-patients`, whose matrix has no MGR row:
    // the nav must not promise a manager a page its read resource denies.
    expect(navFor(["MGR"], {}).map((item) => item.id)).not.toContain(
      "patients",
    );
    expect(navFor(["ADM"], {}).map((item) => item.id)).toContain("patients");
  });

  it("limits appointment navigation to reception and doctors", () => {
    // `read.appointment` is not denied for NUR or ADM, so the capability gate
    // alone would advertise them a surface the live authorization refuses.
    expect(
      NAV_MANIFEST.find((item) => item.id === "appointments")?.roles,
    ).toEqual(["REC", "DOC"]);
    for (const role of ["REC", "DOC"] as const) {
      expect(navFor([role], {}).map((item) => item.id)).toContain(
        "appointments",
      );
    }
    for (const role of ["NUR", "ADM", "PHA", "CAS", "MGR"] as const) {
      expect(navFor([role], {}).map((item) => item.id)).not.toContain(
        "appointments",
      );
    }
  });

  it("never throws for malformed role/capability data", () => {
    expect(() =>
      navFor(null as unknown as RoleCode[], null as unknown as CapabilityMap),
    ).not.toThrow();
  });

  it("shows live lab navigation to live doctors even when photo writes are disabled", () => {
    expect(
      navFor(["DOC"], { "write.photo.upload": "read_only" }, "live").map(
        (item) => item.id,
      ),
    ).toContain("live-photos");
    expect(
      navFor(["DOC"], { "write.photo.upload": "enabled" }, "fixture").map(
        (item) => item.id,
      ),
    ).not.toContain("live-photos");
    expect(
      navFor(["DOC"], { "write.photo.upload": "enabled" }).map(
        (item) => item.id,
      ),
    ).not.toContain("live-photos");
    expect(navFor(["NUR"], {}, "live").map((item) => item.id)).toContain(
      "live-photos",
    );
    expect(navFor(["NUR"], {}, "fixture").map((item) => item.id)).not.toContain(
      "live-photos",
    );
    expect(navFor(["REC"], {}, "live").map((item) => item.id)).not.toContain(
      "live-photos",
    );
  });

  it("places additional visible destinations under More after six primary items", () => {
    const items = Array.from({ length: 8 }, (_, index) => ({
      ...NAV_MANIFEST.find((item) => item.id === "live-photos")!,
      id: `extra-${index}`,
    })) as typeof NAV_MANIFEST;
    render(
      <LocaleProvider initial="en">
        <MemoryRouter>
          <SidebarNav items={items} />
        </MemoryRouter>
      </LocaleProvider>,
    );
    const nav = screen.getByRole("navigation");
    expect(nav.querySelectorAll(".shell-nav-primary > li > a")).toHaveLength(6);
    const extras = within(
      screen.getByText("More").parentElement as HTMLElement,
    ).getAllByRole("link");
    for (const link of extras) expect(link).not.toBeVisible();
    fireEvent.click(screen.getByText("More"));
    for (const link of extras) expect(link).toBeVisible();
  });
});

describe("authenticated responsive shell", () => {
  it("offers lab navigation in a live doctor session without write permission", async () => {
    renderShell({
      role: "DOC",
      mode: "live",
      caps: { "write.photo.upload": "read_only" },
    });
    expect(
      await screen.findByRole("link", { name: "Synthetic lab photos" }),
    ).toHaveAttribute("href", ROUTES.livePhotos);
  });
  it("shows reception destinations, first-focus skip link and current page", async () => {
    renderShell();
    const nav = await screen.findByRole("navigation");
    expect(within(nav).getByRole("link", { name: "Search" })).toHaveAttribute(
      "href",
      ROUTES.search,
    );
    expect(within(nav).getByRole("link", { name: "Today" })).toHaveAttribute(
      "aria-current",
      "page",
    );
    expect(within(nav).queryByRole("link", { name: /audit/i })).toBeNull();
    expect(screen.getAllByRole("link")[0]).toHaveAttribute("href", "#main");
    const user = userEvent.setup();
    await user.tab();
    expect(screen.getAllByRole("link")[0]).toHaveFocus();
    expect(screen.getByRole("main")).toHaveAttribute("id", "main");
    expect(screen.getByRole("main")).toHaveAttribute("tabindex", "-1");
  });

  it("shows ADM system navigation from session and capability context", async () => {
    renderShell({ role: "ADM", caps: { "read.admin": "read_only" } });
    const nav = await screen.findByRole("navigation");
    expect(
      within(nav).getByRole("link", { name: "Audit & status" }),
    ).toHaveAttribute("href", ROUTES.system);
    expect(screen.getAllByText("Administrator").length).toBeGreaterThan(0);
  });

  it.each(["denied", "blocked_unverified"] as const)(
    "hides explicitly %s admin destination",
    async (state) => {
      renderShell({ role: "ADM", caps: { "read.admin": state } });
      const nav = await screen.findByRole("navigation");
      expect(
        within(nav).queryByRole("link", { name: "Audit & status" }),
      ).toBeNull();
    },
  );

  it("switches language without changing path, hash, query or mounted content", async () => {
    window.history.replaceState({}, "", `${ROUTES.home}?view=day#summary`);
    renderShell({ locale: "th" });
    await screen.findByRole("main");
    const main = screen.getByRole("main");
    fireEvent.click(screen.getByRole("button", { name: "EN" }));
    expect(
      window.location.pathname + window.location.search + window.location.hash,
    ).toBe(`${ROUTES.home}?view=day#summary`);
    expect(document.documentElement.lang).toBe("en");
    expect(screen.getByRole("main")).toBe(main);
    expect(screen.getByRole("button", { name: "EN" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
  });

  it("renders a loading skeleton without navigation, page content or profile read", () => {
    const fetchMock = vi.fn(() => new Promise<Response>(() => {}));
    vi.stubGlobal("fetch", fetchMock);
    render(
      <LocaleProvider initial="en">
        <SessionProvider>
          <MemoryRouter>
            <AppShell>
              <p>Private content</p>
            </AppShell>
          </MemoryRouter>
        </SessionProvider>
      </LocaleProvider>,
    );
    expect(screen.getByRole("status")).toHaveAttribute("aria-busy", "true");
    expect(screen.queryByRole("navigation")).toBeNull();
    expect(screen.queryByText("Private content")).toBeNull();
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("renders guest sign-in without nav, content or profile fetch", async () => {
    const { fetchMock } = renderShell({ authed: false });
    const link = await screen.findByRole("link", { name: "Sign in" });
    expect(link).toHaveAttribute("href", ROUTES.login);
    expect(screen.queryByRole("navigation")).toBeNull();
    expect(screen.queryByText("Page content")).toBeNull();
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("shows company only from authenticated profile query", async () => {
    const { fetchMock } = renderShell();
    expect(await screen.findByText("Fixture clinic")).toBeVisible();
    expect(fetchMock.mock.calls.map(([url]) => url)).toEqual([
      "/api/session",
      "/api/read/profile",
    ]);
    expect(screen.queryByText("Demo Clinic (DC)")).toBeNull();
  });

  it.each([
    response({ items: [], total: 0 }),
    response({ items: [{ company: "  " }], total: 1 }),
    response({ code: "FORBIDDEN", message: "private trace" }, 403),
    response({ code: "AUTH_EXPIRED", message: "private trace" }, 401),
  ])(
    "uses unavailable company instead of invented or upstream text",
    async (profile) => {
      const { client } = renderShell({ profile });
      await waitFor(() => expect(client.isFetching()).toBe(0));
      await screen.findByRole("main");
      expect(screen.getByTestId("shell-company")).toHaveTextContent(
        "Unavailable",
      );
      expect(screen.queryByText(/private trace/)).toBeNull();
    },
  );

  it("opens tablet drawer, traps focus, closes with Escape and restores trigger", async () => {
    width = 834;
    renderShell();
    const trigger = await screen.findByRole("button", { name: "Open menu" });
    expect(screen.queryByRole("navigation")).toBeNull();
    trigger.focus();
    fireEvent.click(trigger);
    const dialog = screen.getByRole("dialog");
    const close = within(dialog).getByRole("button", { name: "Close" });
    expect(close).toHaveFocus();
    const links = within(dialog).getAllByRole("link");
    links[links.length - 1]?.focus();
    fireEvent.keyDown(dialog, { key: "Tab" });
    expect(close).toHaveFocus();
    fireEvent.keyDown(dialog, { key: "Tab", shiftKey: true });
    expect(links[links.length - 1]).toHaveFocus();
    fireEvent.keyDown(dialog, { key: "Escape" });
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(trigger).toHaveFocus();
  });

  it("uses bottom navigation on mobile and switches to desktop without duplicate nav", async () => {
    width = 390;
    renderShell();
    const nav = await screen.findByRole("navigation");
    expect(nav).toHaveClass("shell-nav--bottom");
    expect(screen.queryByRole("button", { name: "Open menu" })).toBeNull();
    act(() => {
      width = 1440;
      for (const listener of mediaListeners) listener();
    });
    expect(screen.getAllByRole("navigation")).toHaveLength(1);
    expect(screen.getByRole("navigation")).toHaveClass("shell-nav--sidebar");
  });

  it("closes drawer on navigation and releases body scroll lock", async () => {
    width = 834;
    renderShell();
    fireEvent.click(await screen.findByRole("button", { name: "Open menu" }));
    expect(document.body.style.overflow).toBe("hidden");
    fireEvent.click(
      within(screen.getByRole("dialog")).getByRole("link", {
        name: "Profile & language",
      }),
    );
    expect(window.location.pathname).toBe(ROUTES.profile);
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(document.body.style.overflow).not.toBe("hidden");
    expect(screen.getByRole("button", { name: "Open menu" })).toHaveAttribute(
      "aria-expanded",
      "false",
    );
  });

  it("closes drawer on backdrop and on desktop resize without reopening", async () => {
    width = 834;
    renderShell();
    const trigger = await screen.findByRole("button", { name: "Open menu" });
    fireEvent.click(trigger);
    fireEvent.click(screen.getByRole("dialog"));
    expect(screen.queryByRole("dialog")).toBeNull();
    fireEvent.click(trigger);
    act(() => {
      width = 1440;
      for (const listener of mediaListeners) listener();
    });
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(document.body.style.overflow).not.toBe("hidden");
    act(() => {
      width = 834;
      for (const listener of mediaListeners) listener();
    });
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("notifications expose empty state, never fictional counts or feed", async () => {
    renderShell();
    fireEvent.click(
      await screen.findByRole("button", { name: "Notifications" }),
    );
    expect(await screen.findByText("No notifications")).toBeVisible();
    fireEvent.keyDown(screen.getByRole("dialog", { name: "Notifications" }), {
      key: "Escape",
    });
    expect(screen.queryByText("No notifications")).toBeNull();
  });

  it("mounts home placeholder within authenticated main via real router", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) =>
        response(url === "/api/session" ? session() : { items: [], total: 0 }),
      ),
    );
    render(<App />);
    expect(await screen.findByTestId("shell-placeholder")).toBeVisible();
    expect(screen.getByRole("main")).toContainElement(
      screen.getByTestId("shell-placeholder"),
    );
  });

  it("removes company and protected content when session becomes anonymous", async () => {
    const fetchMock = vi.fn(async (url: string) =>
      response(
        url === "/api/session"
          ? session()
          : { items: [{ company: "Private company" }], total: 1 },
      ),
    );
    vi.stubGlobal("fetch", fetchMock);
    render(
      <QueryClientProvider client={new QueryClient()}>
        <LocaleProvider initial="en">
          <SessionProvider>
            <BrowserRouter>
              <RefreshFixture />
              <AppShell>
                <p>Protected page</p>
              </AppShell>
            </BrowserRouter>
          </SessionProvider>
        </LocaleProvider>
      </QueryClientProvider>,
    );
    await screen.findByText("Private company");
    fetchMock.mockImplementation(async () => response({}, 401));
    fireEvent.click(screen.getByRole("button", { name: "Refresh fixture" }));
    await screen.findByRole("link", { name: "Sign in" });
    expect(screen.queryByText("Private company")).toBeNull();
    expect(screen.queryByText("Protected page")).toBeNull();
  });

  it.each(["th", "en"] as const)(
    "has non-demo paired shell copy in %s",
    (locale) => {
      const i18n = initI18n(locale);
      for (const key of [
        "common.loading",
        "common.unavailable",
        "shell.authRequired",
        "shell.authRequiredBody",
        "shell.notificationsUnavailable",
        "shell.homePending",
        "shell.homePendingBody",
        ...NAV_MANIFEST.map((item) => item.labelKey),
        "nav.more",
        "nav.primary",
        "lang.label",
      ]) {
        expect(i18n.getResource(locale, "translation", key)).toEqual(
          expect.any(String),
        );
        expect(i18n.t(key).trim()).not.toBe("");
        expect(i18n.t(key)).not.toMatch(
          /prototype|demo identity|server is connected|ต้นแบบ/,
        );
      }
    },
  );
});
