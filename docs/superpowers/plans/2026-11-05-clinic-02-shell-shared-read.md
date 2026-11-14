# Clinic Platform — 02 Shell and Shared Read Delivery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver the authenticated shell, sign-in, profile/notifications, global search, print/system states and the read-only transport layer — every surface reading fixture or contract-approved data with writes absent.

**Architecture:** All reads go through one proxy path `/api/read/:resource` with a server-side allow-list; the SPA never sees an upstream URL. **Zod DTO schemas are enforced only at the proxy**: the handler parses and strips upstream payloads, and the client validates only the generic `{items, total}` envelope — it never re-parses resource DTOs. The fixture upstream is the default until a read contract is recorded per resource (spec §6.6).

**Tech Stack:** React 18, React Router, TanStack Query, Zod, MSW, Fastify, Vitest, RTL, Playwright, axe-core.

**Spec:** `docs/superpowers/specs/2026-11-05-clinic-application-design.md`

**Parallelism:** Depends on Plan 01. Blocks Plans 03–05 (they consume `readQuery`, shell layout, fixtures, MSW handlers and nav manifest).

## Global Constraints

**Routing contract:** product navigation uses `ROUTES` from Plan 01 Task 10. Plan 02 adds no new keys (Plan 01 already publishes `login`, `home`, `search`, `system`, `profile`); downstream plans add their own keys and their nav items **in the same task**. Tests may assert literal URLs, product code may not.

**Nav contract:** `NAV_MANIFEST` in `app/src/features/shell/nav.ts` is the only source of navigation. Items reference `routeKey`, never a literal path. `roles` on an item is a **provisional presentation allow-list** for an unverified role matrix (spec `ROLE-MATRIX = DECISION_REQUIRED`); it is never authorization. Server/proxy checks remain the gate.

**Role matrix:** `READ_RESOURCES[*].roles` is labelled `matrix: "unverified"` and enforced only for fixture data in `upstreamMode=fixture`. No read in this plan touches real data; no client code infers permission from a role.

**Session contract (fixed by Plan 01, amended in Plan 02 Task 7):** `SessionDTO = {user:{id,displayName,roles}, capabilities}` plus opaque non-secret `sessionEpoch` used to distinguish real session replacements from ordinary refreshes during logout reconciliation. It is not a credential; callers treat it as context only (Plans 03–05 must include it in assertions when comparing sessions). Company is read from `/api/read/profile`; when absent the UI shows `common.unavailable`.

**Logout contract:** logout is `GET /api/auth/csrf` then `POST /api/auth/logout` with `CSRF_HEADER`, `credentials:"same-origin"`, `content-type: application/json` and no body, then `useSession().refresh()`. A missing/expired session is a 204 no-op; a live session without the token is a 403.

**Operational constraint (search privacy):** patient search travels as `GET /api/read/patients?q=…`. Browser-side surfaces (address bar/history/return targets) never capture it, and the clinic proxy logs no request URLs; any reverse proxy/deployment in front of the site must not log `q`. Alternative: switch search to POST transport when a live read contract is recorded (spec §6.6).

**Release classification:** shell and shared reads are an **interim read-only release**, not completed P0.

**`withFixtures` semantics (Plan 02 final):** `withFixtures()` is an element builder; every call installs a fresh fixture scenario and remounts the provider/router tree when rerendered (same or different scenario — no UI/provider state retained). Tests that need retained state avoid this helper or use component-level state.

- No PHI in URLs; search text and patient identifiers stay in component state. Only non-identifying filter keys are allowed in `returnTo`.
- 390 / 834 / 1440 renderable; drawer nav below 1280; bottom nav allowed below 768.
- Both locales render every string; missing Thai key falls back to EN; both catalogues must contain the same key set.
- Fictional fixture data only, every record marked `fictional: true`.

- No write endpoint, no mutation hook, no optimistic update in this plan.
- Unknown capability → disabled; unknown resource → 404 from proxy, never an upstream passthrough.

| Preflight finding | Plan 02 resolution |
|---|---|
| Harness/fixtures must precede read-client tests | New **Task 1 (fixtures + accounts)** and **Task 2 (MSW harness + render/auth helpers)** execute before **Task 3 (read transport)**. |
| Undefined `readAs`, `loginAsTestUser`, `attemptLogin`, `completeReauth`, `loginAs` | Every helper is defined below with file, signature and behavior. No test may call an undefined helper. |
| `ROUTES.reception` circular | Plan 02 tests use only `ROUTES.home`. Reception/queue/inventory routes and their nav items are added by Plans 03–05 in the same task as their pages. |
| Shell and pages not wired to router | Explicit route tree below; each task names exactly which route it mounts. |
| DTO schemas enforced proxy, not generic client | Proxy Zod-parses and strips; client checks envelope only. Unknown-field assertion moves to the proxy test. |
| Print capability missing | No capability is invented. `canPrint()` returns `false` with an "unverified" reason; the control stays visible and disabled until a recorded contract exists. |
| Playwright must run Vite proxy with matching APP_ORIGIN | Two `webServer` entries with the same origin string `http://localhost:4173`; Vite dev proxy forwards `/api` to `http://localhost:3000`, proxy `APP_ORIGIN=http://localhost:4173`. |
| `SessionDTO` lacks company | Company comes from `/api/read/profile` (fixture); Topbar/ProfilePage fall back to `common.unavailable`. |
| No logout handshake | `postLogout()` contract above, plus MSW and proxy handler tests. |
| Fixture accounts/resources, >20 search rows, downstream overrides missing | Task 1 ships 7 accounts, shared resources and 24 patients (two search pages); `FixtureOverrides` declares every key used by Plans 02–05 with an ownership rule. |
| Fixture/MSW auth handlers and providers unspecified | Task 2 defines exact handlers and provider wrapping for each render helper. |
| Locale selection unspecified | Locale is React state only (`LocaleProvider`); sign-in and shell toggles call `setLocale`; profile labels persistence `device_only`; no storage. |
| Bilingual copy unspecified | Reuse existing catalogue keys where they exist; add exactly the eight keys listed in Task 5; extend `i18n.test.ts` with key-set parity. |
| Nav manifest source unspecified | Single `NAV_MANIFEST` with `routeKey`; extension rule below; max 6 top-level items per role. |
| Role matrix unverified | `matrix:"unverified"` label; fixture-only filter; tests describe it as fixture filtering, not authorization. |

---

### Task 1: Fixture dataset, fixture accounts and contract exports

**Files:**
- Create: `contracts/fixtures/accounts.json`, `patients.json`, `appointments.json`, `encounters.json`, `staff.json`, `work-items.json`, `profile.json`, `access.json`
- Modify: `contracts/package.json` (export `./fixtures/*.json`)
- Modify: `proxy/src/routes/auth.ts` (fixture accounts from the shared JSON)
- Modify: `proxy/src/routes/auth.test.ts`
- Test: `app/src/test/fixtures.test.ts`

**Interfaces:**
- `accounts.json`: `Array<{usr: string; pwd: "fixture"; user: {id: string; displayName: string; roles: RoleCode[]}}>` with **one account per role** (`rec`, `doc`, `nur`, `pha`, `cas`, `mgr`, `adm`), all password `fixture`. These are fictional demo credentials, not secrets.
- `fixtureAuthenticate()` imports `accounts.json`, returns the matching `user` or `null`; no other account source exists. Live mode still throws `UNSUPPORTED` in `buildServer`.
- `patients.json`: ≥24 fictional patients, shape `{id, hn, fullName, phone, lastVisit, fictional: true}`. At least one Thai name, one mixed-script name (`สมชาย Smith`), and every `phone` starts with `08` so the broad query `08` returns all rows across **two** 20-row pages. One record also carries `internal_notes` so the proxy strip test is real; the MSW handler omits it and no client DTO declares it.
- `appointments.json` (≥8), `encounters.json` (≥4), `staff.json` (7), `work-items.json` (`{role, items: [{id, kind, labelKey, count, routeKey, requiredCapability?}]}`), `profile.json` (`{company, fictional: true}`).
- `access.json`: `{matrix: "unverified", resources: {[resource]: RoleCode[]}}` — the single fixture-only role filter source imported by both the proxy registry and the MSW handlers. `matrix:"unverified"` is mandatory; this file is never an authorization contract. Plan 02 rows: `patients`/`appointments` → REC, DOC, NUR, MGR, ADM; `encounters` → DOC, NUR, ADM; `staff` → ADM, MGR; `work-items`/`profile` → all seven roles. PHA is intentionally absent from `patients` so the negative fixture-filter test is meaningful.
- No fixture file contains real names, HN/phone values or identifiers that could collide with production data.

- [ ] **Step 1: Write failing fixture/account tests**

```ts
// app/src/test/fixtures.test.ts
import accounts from "@clinic/contracts/fixtures/accounts.json";
import patients from "@clinic/contracts/fixtures/patients.json";

it("marks every fixture patient as fictional", () => {
  for (const p of patients) expect(p.fictional).toBe(true);
});
it("provides more than one page of patients for the 20-row search page", () => {
  expect(patients.length).toBeGreaterThan(20);
  expect(patients.filter((p) => p.phone.startsWith("08")).length).toBeGreaterThan(20);
});
it("uses Thai, English and mixed-script names", () => {
  expect(patients.some((p) => /[\u0E00-\u0E7F]/.test(p.fullName))).toBe(true);
  expect(patients.some((p) => /[\u0E00-\u0E7F]/.test(p.fullName) && /[A-Za-z]/.test(p.fullName))).toBe(true);
});
it("exposes exactly one fixture account per role code", () => {
  expect(accounts.map((a) => a.user.roles[0]).sort()).toEqual(["ADM", "CAS", "DOC", "MGR", "NUR", "PHA", "REC"]);
});
```

```ts
// proxy/src/routes/auth.test.ts (additions; reuse the existing cookiePairs/cookieValue helpers)
it.each(["rec", "doc", "nur", "pha", "cas", "mgr", "adm"])(
  "authenticates the %s fixture account",
  async (usr) => { /* GET csrf, POST login, expect 200 and a session cookie */ },
);
it("rejects an unknown fixture account", async () => { /* expect 403 FORBIDDEN */ });
```

- [ ] **Step 2: Run to verify failure**

Run: `pnpm --filter @clinic/app test src/test/fixtures.test.ts && pnpm --filter @clinic/proxy test src/routes/auth.test.ts`
Expected: FAIL — fixture files and extra accounts missing.

- [ ] **Step 3: Create fixtures and wire fixture accounts**

Use `@clinic/contracts/fixtures/*.json` imports in both packages. Keep the existing auth guards, CSRF claim, audit codes and error handler untouched; only the fixture account source changes. JSON imports widen `roles` to `string[]`, so add a small local `isRoleCode` guard in each package (next to its `RoleCode` union) instead of unchecked casts.

- [ ] **Step 4: Run to verify pass**

Run: `pnpm --filter @clinic/app test src/test/fixtures.test.ts && pnpm --filter @clinic/proxy test src/routes/auth.test.ts`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add contracts/fixtures contracts/package.json proxy/src/routes/auth.ts proxy/src/routes/auth.test.ts app/src/test/fixtures.test.ts
git commit -m "test: add shared fictional fixtures and seven fixture accounts"
```

---

### Task 2: App test harness, MSW auth/read handlers and render helpers

**Files:**
- Create: `app/src/test/msw/handlers.ts`, `app/src/test/msw/server.ts`
- Create: `app/src/test/render.tsx`
- Modify: `app/src/test/setup.ts` (MSW lifecycle, `matchMedia`, viewport helper)
- Test: `app/src/test/harness.test.tsx`

**Interfaces:**
- `server` (`app/src/test/msw/server.ts`): `setupServer(...defaultHandlers)`; `setup.ts` starts it in `beforeAll`, calls `resetHandlers()` + `cleanup()` in `afterEach`, and closes it in `afterAll`.
- `defaultHandlers = createHandlers({ authed: false })` is exported from `handlers.ts` and used by `server.ts`, so unauthenticated auth-handler tests have a baseline.
- `createHandlers(scenario: FixtureScenario): RequestHandler[]` covers:
  - `GET /api/session` — `authed:false` → `401 {code:"AUTH_EXPIRED"}`; otherwise `SessionDTO` built from `accounts.json` for `scenario.role ?? "REC"` with `capabilities: scenario.caps ?? {}`.
  - `GET /api/auth/csrf` → `200 {token:"fixture-csrf"}`.
  - `POST /api/auth/login` → `403 {code:"FORBIDDEN"}` unless `x-clinic-csrf: fixture-csrf`; success returns the fixture `SessionDTO` and marks the scenario authenticated.
  - `POST /api/auth/logout` → `403` unless `x-clinic-csrf: fixture-csrf`; `204` and marks the scenario anonymous. Missing session is also `204`.
  - `GET /api/read/:resource` — `401` without a session; `404` for an unknown resource; role filter from `access.json` (fixture-only, `matrix:"unverified"`); 20-row pagination; `q` is a case-insensitive substring match over `fullName`, `hn` and `phone` with Thai characters preserved (same rule as the proxy fixture upstream); applies only the scenario keys it owns; unknown keys are ignored.
- Auth transport (`fetchCsrf`, `postLogin`, `postLogout`) and `attemptLogin` arrive in Task 5; Task 2 handlers are written against the wire contract so no forward import is needed.
- `FixtureScenario` type (exported from `handlers.ts`):

```ts
export type FixtureScenario = {
  role?: RoleCode;
  caps?: CapabilityMap;
  authed?: boolean;
  locale?: Locale;
  path?: string;
} & FixtureOverrides;

// Every key referenced by Plans 02–05. Handlers ignore keys they do not own;
// the plan that introduces a key documents its behavior and must add it here.
export interface FixtureOverrides {
  workItems?: "empty" | "error";
  profileCompany?: string | null;
  searchRows?: number;
  // Plans 03–05 (declared now; implemented by the owning plan's handler task)
  duplicates?: boolean;
  patient?: "withVitals" | "noVitals" | "duplicate";
  rows?: unknown;
  status?: string;
  visit?: string;
  withLeave?: boolean;
  encounterId?: string;
  missing?: boolean | string[];
  weight?: { value: number; unit: string } | null;
  allergies?: "unknown" | "known" | "none";
  state?: string;
  savedAt?: number | null;
  diagnoses?: unknown[];
  photo?: Record<string, unknown>;
  files?: string[];
  expired?: boolean;
  reviewer?: string | null;
  consent?: "missing" | "recorded";
  counts?: Record<string, number>;
  draft?: boolean;
  ordered?: number;
  received?: number;
  lowStock?: boolean;
  lines?: number[];
  serverGrandTotal?: number;
  valuation?: "unverified" | "verified";
  serviceMapping?: string;
  statuses?: string[];
  reorder?: unknown;
  appointments?: unknown[];
  completed?: boolean;
  daysToExpiry?: number;
  posted?: boolean;
  readiness?: string;
  [extensionKey: `x-${string}`]: unknown;
}
```

- `renderWithFixtures(ui, scenario?)`: installs `server.use(...createHandlers(scenario))`, calls `window.history.pushState({}, "", scenario?.path ?? ROUTES.home)`, then renders `<MemoryRouter initialEntries={[scenario?.path ?? ROUTES.home]}><AppProviders>{ui}</AppProviders></MemoryRouter>`. Use for anything reading session, capabilities or reads. Per-test `server.use(...)` overrides must be registered **after** this call (MSW prepends later handlers).
- `withFixtures(ui, scenario?)`: installs the scenario handlers and returns the provider-wrapped element for `rerender(withFixtures(...))` in state-transition tests. It is an element builder, not a render call (no render result). Every call is a fresh scenario — same or different — and remounts the router/provider tree, so React state and handler state reset together in the same container; see the `withFixtures` semantics note above.
- `renderWithCaps(caps, ui, opts?)`: renders `LocaleProvider initial={opts?.locale ?? "th"}` + `CapabilityProvider value={caps}` + `MemoryRouter`. Only for components that never call `useSession()`.
- `renderAt(path, ui, opts?)`: pushes `path` into `window.history` and renders `AppProviders` at `MemoryRouter initialEntries=[path]`; `opts` is `FixtureScenario` minus `path` and `locale` (the app locale starts Thai; tests switch it through `LanguageToggle`). Router-level tests that need the real `BrowserRouter` use `window.history.pushState({}, "", path)` then `render(<App />)`, as Plan 01 does.
- `setViewport(width: 390 | 834 | 1440)`: sets `window.innerWidth` and a `matchMedia` stub with the matching `min-width` result; no layout simulation.
- `attemptLogin(usr = "rec", pwd = "fixture"): Promise<SessionDTO>` is added in Task 5, after the auth transport exists: it calls `fetchCsrf()`/`postLogin()` against MSW and returns the DTO.
- `completeReauth()` is defined in Task 6's guard test (see explicit code there); it is not exported from the harness.

- [ ] **Step 1: Write failing harness tests**

```tsx
// app/src/test/harness.test.tsx
import { screen } from "@testing-library/react";
import { renderWithFixtures, renderWithCaps, renderAt, setViewport } from "./render";
import { useSession } from "../app/SessionProvider";
import { ROUTES } from "../app/routes";

function Probe() {
  const { status, session } = useSession();
  return <span>{status}:{session?.user.roles.join(",") ?? "-"}</span>;
}

it("serves an authenticated session for a fixture role", async () => {
  renderWithFixtures(<Probe />, { role: "DOC" });
  expect(await screen.findByText("authenticated:DOC")).toBeVisible();
});
it("serves an anonymous session when authed is false", async () => {
  renderWithFixtures(<Probe />, { authed: false });
  expect(await screen.findByText("anonymous:-")).toBeVisible();
});
it("enforces the CSRF header on MSW login and logout", async () => {
  const login = await fetch("/api/auth/login", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ usr: "rec", pwd: "fixture" }) });
  expect(login.status).toBe(403);
  const logout = await fetch("/api/auth/logout", { method: "POST", headers: { "content-type": "application/json" } });
  expect(logout.status).toBe(403);
});
it("renders capability-only components without a session provider", () => {
  renderWithCaps({ "read.lab": "denied" }, <span>caps</span>);
  expect(screen.getByText("caps")).toBeVisible();
});
it("does not invent routes outside ROUTES", () => {
  renderAt(ROUTES.search, <span>search</span>);
  expect(screen.getByText("search")).toBeVisible();
});
it("sets a viewport width for responsive assertions", () => {
  setViewport(834);
  expect(window.innerWidth).toBe(834);
});
```

- [ ] **Step 2: Run to verify failure**

Run: `pnpm --filter @clinic/app test src/test/harness.test.tsx`
Expected: FAIL — harness modules missing.

- [ ] **Step 3: Implement harness**

MSW handlers return already-narrowed fixture shapes (no unknown fields), so component tests never depend on client-side DTO parsing. The proxy is the only DTO parser.

- [ ] **Step 4: Run to verify pass**

Run: `pnpm --filter @clinic/app test src/test/harness.test.tsx`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/src/test
git commit -m "test: add msw auth/read handlers and render harness"
```

---

### Task 3: Read transport with allow-list and proxy-side Zod DTOs

**Files:**
- Create: `proxy/src/read/registry.ts`, `proxy/src/read/handler.ts`, `proxy/src/read/upstream.ts`, `proxy/src/read/handler.test.ts`
- Modify: `proxy/src/routes/index.ts` (register the read plugin)
- Create: `app/src/transport/read/client.ts`, `app/src/transport/read/client.test.ts`
- Create: `app/src/transport/read/query.ts`

**Interfaces:**
- `READ_RESOURCES: Record<string, ReadResource>` with:
```ts
type ReadContract = "fixture" | "verified" | "unverified";
interface ReadResource {
  schema: ZodType;                 // enforced at the proxy only
  contract: ReadContract;
  matrix: "unverified";           // ROLE-MATRIX is DECISION_REQUIRED
  roles?: RoleCode[];             // loaded from contracts/fixtures/access.json (fixture-only, unverified)
  fixtureFile?: string;           // imported from @clinic/contracts/fixtures
}
```
- Resources in this plan: `patients`, `appointments`, `encounters`, `staff`, `work-items`, `profile`. Downstream plans append resources with the same shape.
- `GET /api/read/:resource?limit&offset&q&filters`. Order: session (`401 {code:"AUTH_EXPIRED"}`) → allow-list (`404 {code:"NOT_FOUND"}`) → fixture role filter (`403 {code:"FORBIDDEN"}`, applied only when `contract === "fixture"` and `matrix === "unverified"`) → pagination cap (`limit ≤ 100`, default 20) → upstream → Zod parse/strip → `{items, total}`.
- The handler derives roles from `SessionStore` only; a client-supplied role header/query is ignored (test asserted).
- `readQuery<T>(resource: string, params: ReadParams, signal?: AbortSignal): Promise<ReadPage<T>>` where `ReadParams = {limit?, offset?, q?, filters?}` and `ReadPage<T> = {items: T[]; total: number}`. The client:
  - calls `/api/read/${encodeURIComponent(resource)}` with `credentials:"same-origin"`, `cache:"no-store"` and the query string;
  - validates only the envelope (`Array.isArray(items)`, `Number.isFinite(total)`), **not** the resource DTO;
  - maps non-OK responses with `mapHttpError` (no stack, no upstream text);
  - sends no retry.
- `useReadQuery<T>(resource, params)` wraps `useQuery` with `queryKey: [resource, params]`, `retry: false`, and passes `signal`; a `401`/`403` is an error state, never fabricated data.

- [ ] **Step 1: Write failing tests (helpers defined in-file)**

```ts
// proxy/src/read/handler.test.ts
import Database from "better-sqlite3";
import type { FastifyInstance } from "fastify";
import { expect, it } from "vitest";
import type { AppConfig } from "../config.js";
import { CSRF_COOKIE } from "../security/constants.js";
import { buildServer } from "../server.js";

const TEST_CONFIG: AppConfig = {
  appOrigin: "http://localhost:5173",
  databasePath: ":memory:",
  activeWrites: new Set(),
  upstreamMode: "fixture",
};

function cookiePairs(cookies: Array<{ name: string; value: string }>): string {
  return cookies.map((cookie) => `${cookie.name}=${cookie.value}`).join("; ");
}
function csrfCookie(res: { cookies: Array<{ name: string; value: string }> }): string {
  const value = res.cookies.find((cookie) => cookie.name === CSRF_COOKIE)?.value ?? "";
  return `${CSRF_COOKIE}=${value}`;
}

const app = () => buildServer({ config: TEST_CONFIG, database: new Database(":memory:"), auditSink: () => {} });

async function loginAsTestUser(target: FastifyInstance, usr = "rec") {
  const csrfRes = await target.inject({ method: "GET", url: "/api/auth/csrf" });
  const token = (csrfRes.json() as { token: string }).token;
  const login = await target.inject({
    method: "POST", url: "/api/auth/login",
    headers: { origin: TEST_CONFIG.appOrigin, "content-type": "application/json", "x-clinic-csrf": token, cookie: csrfCookie(csrfRes) },
    payload: { usr, pwd: "fixture" },
  });
  return { cookie: cookiePairs(login.cookies) };
}

async function readAs(usr: RoleCode, resource: string, query = "") {
  const target = app();
  const { cookie } = await loginAsTestUser(target, usr.toLowerCase());
  return target.inject({ method: "GET", url: `/api/read/${resource}${query}`, headers: { cookie } });
}

it("rejects a resource that is not in the allow-list", async () => {
  const res = await readAs("REC", "../api/resource/Patient");
  expect([400, 404]).toContain(res.statusCode);
});
it("filters fixture rows by the unverified matrix, not by a client role", async () => {
  const res = await readAs("PHA", "patients");
  expect(res.statusCode).toBe(403);
  expect(res.json()).toEqual({ code: "FORBIDDEN" });
});
it("ignores a client-supplied role header", async () => {
  const target = app();
  const { cookie } = await loginAsTestUser(target, "pha");
  const res = await target.inject({ method: "GET", url: "/api/read/patients", headers: { cookie, "x-role": "REC" } });
  expect(res.statusCode).toBe(403);
});
it("parses and narrows the DTO at the proxy, dropping unknown fields", async () => {
  const res = await readAs("REC", "patients");
  expect(res.statusCode).toBe(200);
  expect(res.json().items[0]).not.toHaveProperty("internal_notes");
});
it("caps pagination at 100 and defaults to 20", async () => {
  expect((await readAs("REC", "patients", "?limit=500")).json().items.length).toBeLessThanOrEqual(100);
  expect((await readAs("REC", "patients")).json().items.length).toBe(20);
});
```

```ts
// app/src/transport/read/client.test.ts
it("validates only the envelope and keeps proxy-provided DTO fields", async () => {
  server.use(http.get("/api/read/patients", () => HttpResponse.json({ items: [{ id: "PAT-1" }], total: 1 })));
  await expect(readQuery("patients", {})).resolves.toEqual({ items: [{ id: "PAT-1" }], total: 1 });
});
it("rejects a malformed envelope instead of trusting it", async () => {
  server.use(http.get("/api/read/patients", () => HttpResponse.json({ items: "no" })));
  await expect(readQuery("patients", {})).rejects.toMatchObject({ code: "UPSTREAM_UNAVAILABLE" });
});
it("maps a timeout to UPSTREAM_UNAVAILABLE without a stack", async () => {
  server.use(http.get("/api/read/patients", () => HttpResponse.error()));
  await expect(readQuery("patients", {})).rejects.toMatchObject({ code: "UPSTREAM_UNAVAILABLE" });
});
```

- [ ] **Step 2: Run to verify failure**

Run: `pnpm --filter @clinic/proxy test src/read && pnpm --filter @clinic/app test src/transport/read`
Expected: FAIL — modules missing.

- [ ] **Step 3: Implement registry, handler, upstream and client**

The fixture upstream imports `@clinic/contracts/fixtures/*.json`; it never reads arbitrary paths and never calls a network. `contract: "verified"` is impossible in this plan: every entry is `fixture` with `matrix:"unverified"`. Tests close every injected server and database in `afterEach`.

- [ ] **Step 4: Run to verify pass**

Run: `pnpm --filter @clinic/proxy test src/read && pnpm --filter @clinic/app test src/transport/read && pnpm --filter @clinic/proxy typecheck`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add proxy/src/read proxy/src/routes/index.ts app/src/transport/read
git commit -m "feat: add allow-listed read transport with proxy-side DTO parsing"
```

---

### Task 4: Authenticated shell, responsive navigation and router wiring

**Files:**
- Create: `app/src/features/shell/AppShell.tsx`, `SidebarNav.tsx`, `DrawerNav.tsx`, `Topbar.tsx`, `LanguageToggle.tsx`, `nav.ts`
- Modify: `app/src/app/router.tsx` (mount the shell layout and the `home` route)
- Test: `app/src/features/shell/shell.test.tsx`

**Interfaces:**
- `AppShell({children})` reads `useSession()` (never a role prop) and `useCapabilities()`:
  - `loading` → skeleton with `aria-busy`;
  - `anonymous` → guest state with a link to `ROUTES.login`, no nav;
  - `authenticated` → sidebar/drawer/bottom nav plus `<main id="main">{children}</main>`.
- `NavItem`/`NAV_MANIFEST`/`navFor`:
```ts
export interface NavItem {
  id: string;
  labelKey: string;
  routeKey: keyof typeof ROUTES;   // resolved via ROUTES[routeKey]
  icon: string;
  roles?: readonly RoleCode[];    // provisional presentation allow-list, unverified
  requiredCapability?: Capability;
  owner: "02" | "03" | "04" | "05";
}
export const NAV_MANIFEST: readonly NavItem[];
export function navFor(roles: readonly RoleCode[], caps: CapabilityMap): NavItem[];
```
  - Visibility: role allowed (or no `roles`) **and** `getCapability(caps, requiredCapability)` is neither `denied` nor `blocked_unverified`. Missing capability stays `read_only`.
  - Plan 02 items only: home (`nav.today`, all roles), search (`nav.search`, all roles), profile (`nav.profile`, all roles), system (`nav.system`, ADM/MGR only, `requiredCapability: "read.admin"`). No queue/inventory/patients items yet: those routes do not exist.
  - At most 6 primary items render per role; any additional visible items collapse under the existing `nav.more` menu. `navFor` never throws and never returns a literal path.
- `LanguageToggle()` calls `useLocale().setLocale`; it does not navigate, persist or reload.
- `Topbar` renders role label, company from `useReadQuery("profile")` in a child mounted only when `useSession().status === "authenticated"` (so the read never races the session request), `common.unavailable` when the value is absent/errors, search link to `ROUTES.search`, language toggle, notifications trigger, profile link.
- Router tree after this task:
```tsx
<Routes>
  <Route path={ROUTES.login} element={<LoginPlaceholder />} />
  <Route element={<AppShell><Outlet /></AppShell>}>
    <Route path={ROUTES.home} element={<HomePlaceholder />} />
  </Route>
</Routes>
```
  `HomePlaceholder` replaces Plan 01's `ShellPlaceholder` and keeps `data-testid="shell-placeholder"` so the Plan 01 app test still passes; `LoginPlaceholder` stays until Task 5 mounts `SignInPage`. Route ownership: `login` → Task 5; `search` → Task 8; `profile` → Task 7; `system` → Task 9; downstream plans add children under the same layout route. Plan 02 tests use `ROUTES.home`; `ROUTES.reception` is **not** referenced.

- [ ] **Step 1: Write failing tests**

```tsx
it("shows only the shell destinations in the reception presentation set", () => {
  renderAt(ROUTES.home, <AppShell><div /></AppShell>, { role: "REC" });
  expect(screen.getByRole("link", { name: /search|ค้นหา/i })).toBeInTheDocument();
  expect(screen.getByRole("link", { name: /today|home|วันนี้|หน้าแรก/i })).toBeInTheDocument();
  expect(screen.queryByRole("link", { name: /system|diagnostics|ระบบ/i })).toBeNull();
});
it("shows the system destination to ADM", () => {
  renderAt(ROUTES.home, <AppShell><div /></AppShell>, { role: "ADM", caps: { "read.admin": "read_only" } });
  expect(screen.getByRole("link", { name: /system|diagnostics|ระบบ/i })).toBeInTheDocument();
});
it("toggles language without losing the current path", () => {
  renderAt(ROUTES.home, <AppShell><div /></AppShell>, { role: "REC" });
  fireEvent.click(screen.getByRole("button", { name: "EN" }));
  expect(window.location.pathname).toBe(ROUTES.home);
});
it("exposes a skip link as the first focusable element", () => {
  renderAt(ROUTES.home, <AppShell><div /></AppShell>, { role: "REC" });
  expect(screen.getAllByRole("link")[0]).toHaveAttribute("href", "#main");
});
it("renders no navigation for an anonymous session", () => {
  renderWithFixtures(<AppShell><div /></AppShell>, { authed: false });
  expect(screen.queryByRole("navigation")).toBeNull();
  expect(screen.getByRole("link", { name: /sign in|เข้าสู่ระบบ/i })).toBeInTheDocument();
});
it("never resolves a nav path outside ROUTES", () => {
  for (const item of NAV_MANIFEST) expect(Object.values(ROUTES)).toContain(ROUTES[item.routeKey]);
});
```

- [ ] **Step 2: Run to verify failure**

Run: `pnpm --filter @clinic/app test src/features/shell`
Expected: FAIL — shell modules and router wiring missing.

- [ ] **Step 3: Implement shell and wire the layout route**

Desktop sidebar ≥1280; drawer 768–1279; bottom nav <768. Sticky topbar. No visual redesign: reuse Plan 01 tokens and `ui.css` primitives. Navigation labels come from existing `nav.*` catalogue keys.

- [ ] **Step 4: Run to verify pass**

Run: `pnpm --filter @clinic/app test src/features/shell`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/src/features/shell app/src/app/router.tsx
git commit -m "feat: add responsive role-aware shell and mount shell routes"
```

---

### Task 5: Sign-in, CSRF handshake, logout and bilingual copy

**Files:**
- Create: `app/src/features/auth/SignInPage.tsx`, `SignInPage.test.tsx`
- Create: `app/src/transport/auth.ts`, `app/src/transport/auth.test.ts`, `app/src/test/auth.ts`, `app/src/app/return-to.ts`
- Modify: `app/src/app/router.tsx` (mount `SignInPage` at `ROUTES.login`)
- Modify: `app/src/i18n/catalog/en.json`, `th.json`, `app/src/i18n/i18n.test.ts`

**Interfaces:**
- `CSRF_HEADER = "x-clinic-csrf"` (must match the proxy constant).
- `fetchCsrf(): Promise<{token: string}>` — `GET /api/auth/csrf`, `credentials:"same-origin"`, `cache:"no-store"`.
- `postLogin(usr, pwd): Promise<SessionDTO>` — fetches a fresh token immediately before `POST /api/auth/login`, sends `CSRF_HEADER` and JSON, never stores the password or token.
- `postLogout(): Promise<void>` — fresh token → `POST /api/auth/logout` with `CSRF_HEADER`, `content-type: application/json`, `credentials:"same-origin"`, no body; then the caller runs `useSession().refresh()`.
- `attemptLogin(usr = "rec", pwd = "fixture"): Promise<SessionDTO>` in `app/src/test/auth.ts`: calls `postLogin()` against the MSW handlers and returns the DTO for tests that assert on storage/locale, not on the form.
- `SignInPage()` renders username/password labels from `field.username`/`field.password`, submit from `page.signin.continue`, the TH/EN toggle, the `?reason=expired` banner from `page.signin.sessionExpired`, and a localised error region for 403/network failures. On success: `await refresh()` and navigate to the sanitized `returnTo` or `ROUTES.home`.
- Locale selection calls `useLocale().setLocale` and is React state only: no cookie, storage or profile write. `LocaleProvider` sits above the router (Plan 01), so the choice survives sign-in navigation and route changes.
- `sanitizeReturnTo(raw: string | null): string | null` in `app/src/app/return-to.ts` — accepts only paths starting `/clinic/` (rejects `//`, external URLs, encoded slashes) and only query keys `status`, `view`, `section`, `page`, `range`; everything else is dropped. Task 6's guards store and consume this value.
- New paired catalogue keys (both files, same names): `field.username`, `field.password`, `common.unavailable`, `profile.localeDeviceOnly`, `search.pageOf`, `print.unverifiedTitle`, `print.unverifiedBody`, `notif.empty`. Reuse all other existing keys; no parallel dictionaries.

- [ ] **Step 1: Write failing tests**

```tsx
it("renders Thai by default with a visible EN option", () => {
  renderWithFixtures(<SignInPage />, { authed: false });
  expect(screen.getByRole("button", { name: "EN" })).toBeVisible();
  expect(screen.getByLabelText(/ชื่อผู้ใช้|username/i)).toBeVisible();
});
it("shows a localised error for invalid credentials", async () => {
  renderWithFixtures(<SignInPage />, { authed: false });
  server.use(http.post("/api/auth/login", () => HttpResponse.json({ code: "FORBIDDEN" }, { status: 403 })));
  await user.type(screen.getByLabelText(/ชื่อผู้ใช้|username/i), "rec");
  await user.type(screen.getByLabelText(/รหัสผ่าน|password/i), "nope");
  await user.click(screen.getByRole("button", { name: /continue|ดำเนินการต่อ/i }));
  expect(await screen.findByRole("alert")).toHaveTextContent(/ไม่มีสิทธิ์|forbidden|invalid/i);
});
it("never writes the password to storage", async () => {
  server.use(...createHandlers({ authed: false, role: "REC" }));
  const setItem = vi.spyOn(Storage.prototype, "setItem");
  await attemptLogin("rec", "fixture");
  expect(setItem).not.toHaveBeenCalledWith(expect.anything(), expect.stringContaining("fixture"));
});
it("requires the CSRF header for logout and returns to anonymous", async () => {
  function SessionWrapper({ children }: { children: ReactNode }) {
    return <SessionProvider>{children}</SessionProvider>;
  }
  server.use(...createHandlers({ role: "REC" }));
  const { result } = renderHook(() => useSession(), { wrapper: SessionWrapper });
  await waitFor(() => expect(result.current.status).toBe("authenticated"));
  await postLogout();
  await result.current.refresh();
  await waitFor(() => expect(result.current.status).toBe("anonymous"));
});
it("keeps catalogue key sets identical", () => {
  expect(Object.keys(en).sort()).toEqual(Object.keys(th).sort());
});
```

- [ ] **Step 2: Run to verify failure**

Run: `pnpm --filter @clinic/app test src/features/auth src/transport/auth.test.ts src/i18n/i18n.test.ts`
Expected: FAIL — sign-in, auth transport and new keys missing.

- [ ] **Step 3: Implement sign-in, auth transport and copy**

Logout stays a POST with origin + JSON content type; do not weaken the proxy guards. The sign-in page never renders demo credentials (fixture accounts are for e2e only).

- [ ] **Step 4: Run to verify pass**

Run: `pnpm --filter @clinic/app test src/features/auth src/transport/auth.test.ts src/i18n/i18n.test.ts`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/src/features/auth app/src/transport/auth.ts app/src/transport/auth.test.ts app/src/test/auth.ts app/src/app/return-to.ts app/src/i18n app/src/app/router.tsx
git commit -m "feat: add sign-in, csrf handshake and logout transport"
```

---

### Task 6: Route guards, permission-denied and re-auth return

**Files:**
- Create: `app/src/app/guards.tsx`, `app/src/app/guards.test.tsx`, `app/src/features/system/PermissionDenied.tsx`
- Modify: `app/src/app/router.tsx` (wrap the shell layout in `RequireSession`)

**Interfaces:**
- `RequireSession({children})`: `loading` → `StatePanel` loading; `anonymous` → stores sanitized `returnTo` and `<Navigate to={ROUTES.login + "?reason=expired"} replace />`; `authenticated` → children.
- `RequireRole({roles, children})`: UI-only presentation guard reading the authenticated session's roles. It is not authorization; a matrix mismatch must still be enforced by the proxy. Non-matching roles render `PermissionDenied` in place.
- `PermissionDenied({resource})` reuses `StatePanel` and existing `state.denied.*` copy.
- `returnTo` storage: `sessionStorage["clinic.returnTo"]`, path only, produced by Task 5's `sanitizeReturnTo` (this task only stores/consumes it). Never store `q`, name, phone or ID.

- [ ] **Step 1: Write failing tests (helpers defined here)**

```tsx
// app/src/app/guards.test.tsx
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { App } from "./App";
import { ROUTES } from "./routes";
import { createHandlers } from "../test/msw/handlers";
import { server } from "../test/msw/server";
import { renderWithFixtures } from "../test/render";
import { RequireRole } from "./guards";
import { sanitizeReturnTo } from "./return-to";

async function completeReauth(user: ReturnType<typeof userEvent.setup>) {
  await user.type(await screen.findByLabelText(/ชื่อผู้ใช้|username/i), "rec");
  await user.type(screen.getByLabelText(/รหัสผ่าน|password/i), "fixture");
  await user.click(screen.getByRole("button", { name: /continue|ดำเนินการต่อ/i }));
  await waitFor(() => expect(screen.queryByLabelText(/ชื่อผู้ใช้|username/i)).toBeNull());
}

it("shows permission denied rather than an empty page", () => {
  renderWithFixtures(
    <RequireRole roles={["ADM"]}><div>admin</div></RequireRole>,
    { role: "NUR" },
  );
  expect(screen.getByText(/ไม่มีสิทธิ์|permission/i)).toBeVisible();
});
it("restores path and allow-listed query after re-auth", async () => {
  const user = userEvent.setup();
  window.history.pushState({}, "", `${ROUTES.home}?status=waiting`);
  server.use(...createHandlers({ authed: false }));
  render(<App />);
  await completeReauth(user);
  expect(window.location.pathname + window.location.search).toBe(`${ROUTES.home}?status=waiting`);
});
it("drops identifying query parameters from returnTo", () => {
  expect(sanitizeReturnTo(`${ROUTES.home}?q=สมชาย&status=waiting`)).toBe(`${ROUTES.home}?status=waiting`);
  expect(sanitizeReturnTo("https://evil.example")).toBeNull();
  expect(sanitizeReturnTo("//evil.example")).toBeNull();
});
```

- [ ] **Step 2: Run to verify failure**

Run: `pnpm --filter @clinic/app test src/app/guards.test.tsx`
Expected: FAIL.

- [ ] **Step 3: Implement guards**

`RequireSession` redirects only on `anonymous`; the login screen owns the actual re-auth and `returnTo` navigation. `PermissionDenied` is presentation only and must not be described as an authorization boundary.

- [ ] **Step 4: Run to verify pass**

Run: `pnpm --filter @clinic/app test src/app/guards.test.tsx`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/src/app app/src/features/system/PermissionDenied.tsx
git commit -m "feat: add route guards with sanitized re-auth return"
```

---

### Task 7: Notifications, work items and profile (read only)

**Files:**
- Create: `app/src/features/shell/NotificationsPanel.tsx`, `useWorkItems.ts`, `NotificationsPanel.test.tsx`
- Create: `app/src/features/profile/ProfilePage.tsx`, `ProfilePage.test.tsx`
- Modify: `app/src/app/router.tsx` (mount `ProfilePage` at `ROUTES.profile`)
- Modify: `app/src/features/shell/Topbar.tsx` (notifications trigger, profile link)

**Interfaces:**
- `useWorkItems()` → `useReadQuery<WorkItem>("work-items", {})`; `WorkItem = {id, kind, labelKey, count, routeKey?: string, requiredCapability?}`. The panel root carries `data-testid="work-items"`. A `routeKey` is resolved through `ROUTES`; items whose key is absent, whose capability is `denied`/`blocked_unverified`, or whose destination is not in the current `navFor` set are hidden. No item ever carries a literal path. Dismiss is not modelled (write).
- On a failed work-items read, render `role="alert"` with `errors.UPSTREAM_UNAVAILABLE`; never render a fabricated `0`.
- `ProfilePage()` shows display name/roles from `useSession()`, company from `useReadQuery("profile")` (fallback `common.unavailable`), locale selector with the `profile.localeDeviceOnly` label (no persistence), and logout via `postLogout()` + `refresh()` + navigate to `ROUTES.login`. No role-switch control.
- Topbar company also comes from `/api/read/profile`; it never assumes a `SessionDTO.company`.

- [ ] **Step 1: Write failing tests**

```tsx
it("hides items whose destination is not permitted", async () => {
  renderWithFixtures(<NotificationsPanel />, { role: "REC", caps: { "read.lab": "denied" } });
  expect(await screen.findByTestId("work-items")).toBeVisible();
  expect(screen.queryByText(/lab result|ผลตรวจทางห้องปฏิบัติการ/i)).toBeNull();
});
it("shows a localised empty state without a fabricated count", async () => {
  renderWithFixtures(<NotificationsPanel />, { role: "REC", workItems: "empty" });
  expect(await screen.findByText(/no notifications|ไม่มีการแจ้งเตือน/i)).toBeVisible();
});
it("does not render a fabricated count when the source fails", async () => {
  renderWithFixtures(<NotificationsPanel />, { role: "DOC" });
  server.use(http.get("/api/read/work-items", () => HttpResponse.error()));
  await waitFor(() => expect(screen.getByRole("alert")).toBeVisible());
  expect(screen.queryByText("0")).toBeNull();
});
it("labels locale persistence as device-only until verified", () => {
  renderWithFixtures(<ProfilePage />, { role: "REC" });
  expect(await screen.findByText(/เครื่องนี้|this device/i)).toBeVisible();
});
it("offers logout without a role-switch control", async () => {
  function ProfileHarness() {
    const { status } = useSession();
    return (<><span>{status}</span><ProfilePage /></>);
  }
  renderWithFixtures(<ProfileHarness />, { role: "REC" });
  await user.click(await screen.findByRole("button", { name: /ออกจากระบบ|log out/i }));
  await waitFor(() => expect(screen.getByText("anonymous")).toBeVisible());
  expect(screen.queryByRole("button", { name: /switch role|สลับบทบาท/i })).toBeNull();
});
```

- [ ] **Step 2: Run to verify failure**

Run: `pnpm --filter @clinic/app test src/features/shell/NotificationsPanel.test.tsx src/features/profile`
Expected: FAIL.

- [ ] **Step 3: Implement panel and profile**

Work items come from the read resource scoped by the proxy; no client aggregation. Profile never edits identity, role or company.

- [ ] **Step 4: Run to verify pass**

Run: `pnpm --filter @clinic/app test src/features/shell/NotificationsPanel.test.tsx src/features/profile`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/src/features/shell app/src/features/profile app/src/app/router.tsx
git commit -m "feat: add work items, notifications and profile read surfaces"
```

---

### Task 8: Global authorised search (APP-03)

**Files:**
- Create: `app/src/features/search/SearchPage.tsx`, `SearchPage.test.tsx`, `SearchResultList.tsx`, `usePatientSearch.ts`
- Modify: `app/src/app/router.tsx` (mount `SearchPage` at `ROUTES.search`)

**Interfaces:**
- `usePatientSearch(q: string, page: number)` delegates to `useReadQuery<PatientSummary>("patients", {q, page, pageSize: 20})`; no separate fetch wrapper. `PatientSummary = {id, hn, fullName, phone, lastVisit}` — no identity number, no clinical fields.
- Search text lives in component state only; it is not written to the URL or `returnTo`.
- Debounce 300 ms; abort the in-flight query when the text or page changes; reset to page 1 on text change.
- Pagination label uses `search.pageOf`; empty state uses `search.noResults`/`search.noResultsBody`.

- [ ] **Step 1: Write failing tests**

```tsx
it("searches Thai and mixed-script input without transliteration", async () => {
  renderWithFixtures(<SearchPage />, { role: "REC" });
  await user.type(screen.getByRole("searchbox"), "สมชาย Smith");
  expect(await screen.findByText(/สมชาย Smith/)).toBeVisible();
});
it("shows a localised empty state", async () => {
  renderWithFixtures(<SearchPage />, { role: "REC" });
  await user.type(screen.getByRole("searchbox"), "zzzz");
  expect(await screen.findByText(/ไม่พบ|no results/i)).toBeVisible();
});
it("paginates at 20 per page over more than 20 fixture rows", async () => {
  renderWithFixtures(<SearchPage />, { role: "REC" });
  await user.type(screen.getByRole("searchbox"), "08");
  expect(await screen.findByText(/1\s*\/\s*2|หน้า 1 \/ 2/i)).toBeVisible();
});
it("does not put the query in the URL", async () => {
  renderWithFixtures(<SearchPage />, { role: "REC" });
  await user.type(screen.getByRole("searchbox"), "สมชาย");
  expect(window.location.search).toBe("");
});
```

- [ ] **Step 2: Run to verify failure**

Run: `pnpm --filter @clinic/app test src/features/search`
Expected: FAIL.

- [ ] **Step 3: Implement search**

The proxy fixture upstream performs substring matching over `fullName`, `hn` and `phone` (case-insensitive, Thai preserved). No transliteration, no external search service, no PHI in URLs.

- [ ] **Step 4: Run to verify pass**

Run: `pnpm --filter @clinic/app test src/features/search`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/src/features/search app/src/app/router.tsx
git commit -m "feat: add authorised patient search"
```

---

### Task 9: System states and print/export preview (APP-04)

**Files:**
- Create: `app/src/features/system/SystemStatesPage.tsx`, `app/src/features/print/PrintPreview.tsx`, `usePrint.ts`, `app/src/features/system/system.test.tsx`
- Modify: `app/src/app/router.tsx` (mount `SystemStatesPage` at `ROUTES.system`)

**Interfaces:**
- `SystemStatesPage()` renders the eight required states using Plan 01 primitives: populated, empty, loading, validation, error, success, permission denied, unsaved changes. It is a demo surface, not an admin control panel.
- `PrintPreview({template, locale})` takes an explicit locale; it never inherits the UI locale silently.
- **Print capability is absent from `contracts/capabilities.json`.** `canPrint(_caps: CapabilityMap): boolean` therefore returns `false` in this plan. No capability is invented, and no role grants print. The print control stays visible and disabled with `print.unverifiedTitle`/`print.unverifiedBody`; `usePrint().print()` returns `false` without calling `window.print()`.
- Consumes the canonical `WriteGate` from Plan 01 Task 9 (`@/ui/WriteGate`) for gated routed actions only. Print is a plain disabled native button with a described reason, not a fabricated capability state.
- Gated route actions use `RoutedControl` as the direct child (never Router `Link` or a custom routed wrapper). Non-gated read navigation uses ordinary Router `Link`.

- [ ] **Step 1: Write failing tests**

```tsx
it("renders all eight required states", () => {
  renderWithCaps({}, <SystemStatesPage />);
  for (const label of ["Populated", "Empty", "Loading", "Validation", "Error", "Success", "Permission denied", "Unsaved changes"])
    expect(screen.getByText(new RegExp(label, "i"))).toBeVisible();
});
it("keeps print visible but disabled because no verified print capability exists", async () => {
  const print = vi.spyOn(window, "print").mockImplementation(() => {});
  renderWithCaps({ "read.admin": "enabled" }, <PrintPreview template="receipt" locale="th" />);
  const button = screen.getByRole("button", { name: /print|พิมพ์/i });
  expect(button).toBeDisabled();
  expect(screen.getByText(/ยังไม่ได้ตรวจสอบ|not verified/i)).toBeVisible();
  await user.click(button);
  expect(print).not.toHaveBeenCalled();
  expect(canPrint({ "read.admin": "enabled" })).toBe(false);
});
```

- [ ] **Step 2: Run to verify failure**

Run: `pnpm --filter @clinic/app test src/features/system`
Expected: FAIL.

- [ ] **Step 3: Implement states and print preview**

Do not enable print for any role. A future plan may enable it only after `contracts/capabilities.json` and a verified manifest define it.

- [ ] **Step 4: Run to verify pass**

Run: `pnpm --filter @clinic/app test src/features/system`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/src/features/system app/src/features/print app/src/app/router.tsx
git commit -m "feat: add system states and unverified print preview"
```

---

### Task 10: Playwright shell suite, responsive checks and proxy origin

**Files:**
- Create: `app/playwright.config.ts`, `app/e2e/shell.spec.ts`, `app/e2e/a11y.spec.ts`, `app/e2e/helpers.ts`

**Interfaces:**
- `playwright.config.ts`:
  - `use.baseURL = "http://localhost:4173"`.
  - `webServer` is an array:
    1. proxy: `pnpm --filter @clinic/proxy dev` with `PORT=3000`, `UPSTREAM_MODE=fixture`, `DATABASE_PATH=:memory:`, `APP_ORIGIN=http://localhost:4173`; `url: "http://localhost:3000/api/health"`.
    2. app: `pnpm --filter @clinic/app dev -- --host localhost --port 4173 --strictPort` with `CLINIC_PROXY_TARGET=http://localhost:3000`; `url: "http://localhost:4173/clinic/login"`.
  - The `baseURL`, `APP_ORIGIN` and Vite host strings must be identical (`localhost`, not mixed with `127.0.0.1`); Vite's dev proxy forwards `/api` same-origin, preserving Origin and cookies.
  - `reuseExistingServer: !process.env.CI`.
- `app/e2e/helpers.ts` exports `loginAs(page, role: RoleCode)`: goes to `ROUTES.login` via `page.goto("/clinic/login")`, fills the account from `@clinic/contracts/fixtures/accounts.json`, submits, and waits for the shell. It never injects cookies directly, so the real origin + CSRF handshake runs.
- Fixtures are served by the proxy in `fixture` mode; no live upstream is contacted.

- [ ] **Step 1: Write failing e2e tests**

```ts
test("shell has no serious axe violations at 1440", async ({ page }) => {
  await page.goto("/clinic/login");
  const results = await new AxeBuilder({ page }).analyze();
  expect(results.violations.filter((v) => v.impact === "serious")).toEqual([]);
});
test("nav becomes a drawer at 834", async ({ page }) => {
  await page.setViewportSize({ width: 834, height: 1000 });
  await loginAs(page, "REC");
  await expect(page.getByRole("button", { name: /menu/i })).toBeVisible();
});
test("notification label does not overflow at 390", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await loginAs(page, "REC");
  await page.getByRole("button", { name: /notifications|การแจ้งเตือน/i }).click();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
});
test("no horizontal scroll at 390 on search", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await loginAs(page, "REC");
  await page.goto("/clinic/search");
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
});
```

- [ ] **Step 2: Run to verify failure**

Run: `pnpm --filter @clinic/app e2e`
Expected: FAIL — config and helper missing.

- [ ] **Step 3: Configure Playwright and the two web servers**

Add `@axe-core/playwright` usage. Keep the proxy in `fixture` mode; never set `UPSTREAM_MODE=live` (it throws by design).

- [ ] **Step 4: Run to verify pass**

Run: `pnpm --filter @clinic/app e2e`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add app/playwright.config.ts app/e2e
git commit -m "test: add shell e2e, a11y and proxy-origin coverage"
```

---

## Downstream integration rules (Plans 03–05)

- Fixtures live only in `contracts/fixtures/*`; a downstream plan adding pharmacy/invoice/etc. data adds files there and exports them through `@clinic/contracts/fixtures/*.json`. No second fixture directory.
- A downstream plan adding a resource must, in the same task: append `READ_RESOURCES`, add the fixture file, extend `createHandlers()` and `FixtureOverrides`, add its `ROUTES` keys **and** its `NAV_MANIFEST` items (with `owner` set). Nav items may not reference a route that does not exist yet.
- Downstream e2e uses `loginAs(page, role)` from Plan 02; all seven fixture accounts already exist.
- Downstream tests use `renderWithFixtures`/`renderWithCaps`/`renderAt`/`server`/`attemptLogin`/`setViewport`; none may define a second session hook, fetch wrapper or fixture server.
- Role lists remain presentation/fixture filters until `ROLE-MATRIX` is recorded; no downstream test may claim role authorization from them. Role-specific routes wrap in Plan 02's `RequireRole` (presentation guard), and the proxy still owns every allow/deny decision.

## Self-Review

| Spec area | Covered by |
|---|---|
| §2 APP-01…04 | Tasks 4–9 |
| APP-02 profile, locale preference, logout | Tasks 5, 7 |
| Canonical WriteGate | Consumed from Plan 01 Task 9 |
| `ROUTES`, `useSession`, `SessionDTO`, `CSRF_HEADER` | Consumed from Plan 01 Tasks 7, 10 |
| §3.3 proxy read shape/allow-list | Task 3 |
| §3.5 guards, roles, session expiry | Tasks 4–6 |
| §4 locale at sign-in, header, profile | Tasks 4, 5, 7 |
| §5 responsive, a11y, 390/834/1440 | Tasks 4, 10 |
| §6.6 read activation | Task 3 registry status |
| §9.1 read contract/component/e2e/a11y | Tasks 3, 10 |

**Deferred:** role-home dashboards (Plans 03–05), real upstream reads (needs recorded contract), notification dismiss state (write), print enablement (no capability exists).

**Known gaps:** `work-items`, `profile` and search DTOs are fixtures until a read contract is recorded; the registry keeps them `fixture` and the UI must not present them as verified. `ROLE-MATRIX` is `DECISION_REQUIRED`; no role-based behavior in this plan is an authorization claim. Print stays disabled until a capability is defined and verified.
