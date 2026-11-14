# Clinic Platform — 01 Foundation (Proxy, Auth, i18n, Design System) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stand up the React + TypeScript app shell and the same-origin proxy with login, browser-facing CSRF/origin protection, capability flags, error taxonomy, design tokens and the reused TH/EN catalogue — with every write disabled.

**Architecture:** pnpm workspace with two packages: `app/` (Vite React SPA) and `proxy/` (Fastify BFF). The browser only calls the proxy. Proxy sessions are server-side and `HttpOnly`; browser CSRF tokens are proxy-issued and unrelated to any upstream token. Capabilities default to deny. Read data comes from a fixture upstream until a read contract is recorded (spec §6.6).

**Tech Stack:** TypeScript (strict), React 18, Vite, React Router, TanStack Query, react-i18next, Zod, Fastify 5, `better-sqlite3`, Vitest, React Testing Library, MSW, Playwright, axe-core.

**Spec:** `docs/superpowers/specs/2026-11-05-clinic-application-design.md`

**Parallelism:** Foundation must land first. Plans 02–05 depend on this plan. Plan 06's substrate (Tasks 1–4) may start in parallel with Plans 03–05 once this plan's proxy interfaces exist; its activation tasks depend on the surface plans.

## Global Constraints

**Release classification:** this plan delivers foundation only. It is not a P0 completion milestone and enables no write.

- Writes are disabled by default (`READ_ONLY`); no write route may be reachable in this plan.
- Browser-facing CSRF/origin protection covers **login and logout**, independent of upstream CSRF (spec §3.3).
- No secrets, tokens or API keys in `localStorage`, `sessionStorage`, bundles or logs.
- No patient identifiers or free text in URLs, analytics, console output or logs.
- Default locale `th`; supported `th | en`; keys reused from the prototype catalogue (spec §4.3).
- Target breakpoints 390 / 834 / 1440 px; interactive targets ≥ 44×44 CSS px.
- Evaluation host `10.0.100.10:8080` is HTTP-only; no `Secure` cookie flag there. Never call it in tests.
- Fictional fixture data only; every fixture record is marked `fictional: true`.

## Review Focus

1. **CSRF token missing or stale on login** — the request is rejected with a recoverable error, and the client can fetch a fresh token. Test: `rejects login without the proxy CSRF header even from the right origin` (Task 7).
2. **Cross-origin form post to a mutation route** — must be rejected even if cookies are present. Test: `rejects a login from a different origin` (Task 7).
3. **Unknown/missing capability** — every action must default to visually disabled with a reason, never enabled. Test: `defaults every unknown capability to read_only` and `renders the control visibly disabled with a reason when not enabled` (Task 9).
4. **Missing Thai key** — must fall back to English, never render the raw key. Test: `falls back to English when a Thai value is absent` (Task 4).
5. **Thai long label inside a 44 px control** — must wrap rather than truncate. Test: `lets a long Thai label wrap instead of truncating` (Task 3).

---

### Task 1: Workspace scaffold and quality gates

**Files:**
- Create: `package.json`, `pnpm-workspace.yaml`, `tsconfig.base.json`, `.editorconfig`, `.gitignore`
- Create: `app/package.json`, `app/vite.config.ts`, `app/tsconfig.json`, `app/index.html`, `app/src/main.tsx`
- Create: `app/src/test/setup.ts`, `app/src/styles/index.css`
- Create: `proxy/package.json`, `proxy/tsconfig.json`, `proxy/vitest.config.ts`, `proxy/src/config.ts`, `proxy/src/server.ts`
- Create: `contracts/package.json` (workspace package `@clinic/contracts`, `exports: {"./capabilities.json": "./capabilities.json"}`), `contracts/capabilities.json`
- Test: `app/src/smoke.test.ts`

**Interfaces:**
- Produces: workspace packages `@clinic/app`, `@clinic/proxy`; scripts `pnpm -w typecheck`, `pnpm -w test`, `pnpm -w lint`, plus per-package `dev`, `build`, `start`, `test`, `e2e`.
- Produces: `proxy/src/config.ts` exporting `loadConfig(env?: NodeJS.ProcessEnv): AppConfig` with `{appOrigin, databasePath, activeWrites, upstreamMode}`; all later proxy tasks consume it.

- [ ] **Step 1: Create workspace manifests and install dependencies**

Root `package.json` scripts: `typecheck: pnpm -r typecheck`, `test: pnpm -r test`, `lint: biome check .`. Add dev dependency `@biomejs/biome` and root `biome.json` with `"formatter": {"indentStyle": "space"}`.

Install the exact dependency set (pin majors, commit the lockfile):

```bash
# app runtime
pnpm --filter @clinic/app add react@18 react-dom@18 react-router-dom @tanstack/react-query react-i18next i18next zod
# app dev/test
pnpm --filter @clinic/app add -D typescript vite @vitejs/plugin-react vitest jsdom \
  @testing-library/react @testing-library/user-event @testing-library/jest-dom \
  msw @types/react @types/react-dom @playwright/test @axe-core/playwright
# proxy
pnpm --filter @clinic/proxy add fastify @fastify/cookie better-sqlite3 zod
pnpm --filter @clinic/proxy add -D typescript vitest @types/node @types/better-sqlite3 tsx
```

Add `app` scripts: `dev: vite`, `build: vite build`, `test: vitest run`, `e2e: playwright test`; `proxy` scripts: `dev: tsx watch src/server.ts`, `build: tsc -p tsconfig.json`, `start: node dist/server.js`, `test: vitest run`.

- [ ] **Step 2: Write the failing smoke test**

```ts
// app/src/smoke.test.ts
import { describe, expect, it } from "vitest";
describe("workspace", () => {
  it("runs vitest with jsdom", () => {
    expect(typeof document).toBe("object");
  });
});
```

- [ ] **Step 3: Run it to verify it fails**

Run: `pnpm --filter @clinic/app test`
Expected: FAIL — vitest config or jsdom environment missing.

- [ ] **Step 4: Add Vite/Vitest config and minimal entry**

`app/vite.config.ts` uses `react()` and `test: { environment: "jsdom", setupFiles: ["./src/test/setup.ts"] }`. `app/src/test/setup.ts` imports `@testing-library/jest-dom/vitest` and registers `afterEach(cleanup)`. `app/src/main.tsx` renders `<div id="root" />` placeholder. `app/tsconfig.json` and `proxy/tsconfig.json` set `resolveJsonModule: true` and depend on `@clinic/contracts`; `pnpm-workspace.yaml` lists `app`, `proxy`, `contracts`. `proxy/vitest.config.ts` sets `test: { environment: "node" }`. `proxy/src/server.ts` exports `buildServer(): FastifyInstance`.

- [ ] **Step 5: Run tests and typecheck**

Run: `pnpm --filter @clinic/app test && pnpm -w typecheck`
Expected: PASS (1 test).

- [ ] **Step 6: Commit**

```bash
git add package.json pnpm-workspace.yaml tsconfig.base.json app proxy biome.json
git commit -m "chore: scaffold clinic app and proxy workspace"
```

---

### Task 2: Design tokens and token contract test

**Files:**
- Create: `app/src/ui/tokens.css`, `app/src/styles/base.css`, `app/src/styles/index.css`
- Modify: `app/src/main.tsx` (import `./styles/index.css`)
- Test: `app/src/ui/tokens.test.ts`

**Interfaces:**
- Produces: CSS custom properties consumed by every component: `--canvas`, `--surface`, `--ink`, `--muted`, `--border`, `--primary`, `--accent`, `--success`, `--warning`, `--danger`, `--info`, `--neutral`, `--radius-card`, `--target-min`, `--font-sans`.
- Produces: `app/src/styles/index.css` as the single stylesheet entry, importing `./base.css` and `../ui/tokens.css` in that order.

- [ ] **Step 1: Write the failing token test**

```ts
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
const css = readFileSync(new URL("./tokens.css", import.meta.url), "utf8");
describe("design tokens", () => {
  it.each(["--canvas", "--ink", "--primary", "--danger", "--neutral", "--target-min", "--font-sans"])(
    "defines %s", (name) => expect(css).toContain(name));
  it("sets the minimum target to 44px", () => {
    expect(css).toMatch(/--target-min:\s*44px/);
  });
  it("includes a Thai-capable font fallback", () => {
    expect(css).toMatch(/Noto Sans Thai|IBM Plex Sans Thai|Sukhumvit Set/);
  });
  it("is imported by the single stylesheet entry", () => {
    const index = readFileSync(new URL("../styles/index.css", import.meta.url), "utf8");
    expect(index).toMatch(/tokens\.css/);
  });
});
```

- [ ] **Step 2: Run to verify it fails**

Run: `pnpm --filter @clinic/app test src/ui/tokens.test.ts`
Expected: FAIL — `ENOENT tokens.css`.

- [ ] **Step 3: Implement tokens**

Copy measured values from the spec appendix's `DESIGN.md` reference (`#f5f6f8`, `#ffffff`, `#0a1b39`, `#6c7688`, `#e7e8eb`, `#2e37a4`, `#00d3c7`), set `--neutral: #6c7688` for neutral status chips, and reuse the existing Thai fallback stack. Set `--target-min: 44px`. `app/src/styles/index.css` imports `./base.css` then `../ui/tokens.css`, and `main.tsx` imports it once.

- [ ] **Step 4: Run to verify it passes**

Run: `pnpm --filter @clinic/app test src/ui/tokens.test.ts`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/src/ui/tokens.css app/src/styles app/src/main.tsx app/src/ui/tokens.test.ts
git commit -m "feat: add design tokens with Thai font fallback"
```

---

### Task 3: UI primitives with accessibility contract

**Files:**
- Create: `app/src/ui/Button.tsx`, `app/src/ui/StatusChip.tsx`, `app/src/ui/StatePanel.tsx`, `app/src/ui/PageHeader.tsx`, `app/src/ui/ui.css`
- Modify: `app/src/styles/index.css` (import `../ui/ui.css` last)
- Test: `app/src/ui/primitives.test.tsx`

**Interfaces:**
- Produces: `Button({variant: "primary"|"secondary"|"danger", disabledReason?: string, ...ButtonHTMLAttributes})`; `StatusChip({tone: "success"|"warning"|"danger"|"info"|"neutral", label: string, icon?: ReactNode})`; `StatePanel({kind: "empty"|"loading"|"error"|"permission-denied", title: string, detail?: string, onRetry?: () => void})`; `PageHeader({title: string, breadcrumb?: ReactNode, actions?: ReactNode})`.

- [ ] **Step 1: Write failing tests**

```tsx
import { readFileSync } from "node:fs";

it("exposes a disabled reason to assistive tech", () => {
  render(<Button disabled disabledReason="Awaiting backend verification">Save</Button>);
  expect(screen.getByRole("button", { name: /save/i })).toBeDisabled();
  expect(screen.getByText("Awaiting backend verification")).toBeInTheDocument();
});
it("never renders a status chip by colour alone", () => {
  render(<StatusChip tone="danger" label="Out of stock" />);
  expect(screen.getByText("Out of stock")).toBeVisible();
});
it("uses a 44px minimum target class", () => {
  render(<Button>Open</Button>);
  expect(screen.getByRole("button")).toHaveClass("ui-button");
});
it("lets a long Thai label wrap instead of truncating", () => {
  const css = readFileSync(new URL("./ui.css", import.meta.url), "utf8");
  expect(css).toMatch(/white-space:\s*normal/);
  expect(css).not.toMatch(/text-overflow:\s*ellipsis/);
});
```

- [ ] **Step 2: Run to verify failure**

Run: `pnpm --filter @clinic/app test src/ui/primitives.test.tsx`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement primitives**

Use plain global classes in `ui.css` (`.ui-button`, `.ui-chip`, `.ui-state`), **not** CSS modules, so tests and the disabled gate can assert stable class names. `Button` applies `min-height: var(--target-min)`, `:focus-visible` ring, and renders `aria-describedby` pointing at the reason element when disabled. `StatusChip` always renders text; `neutral` uses `--neutral`. `StatePanel` renders `role="status"` for loading and `role="alert"` for error.

- [ ] **Step 4: Run to verify pass**

Run: `pnpm --filter @clinic/app test src/ui/primitives.test.tsx`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add app/src/ui
git commit -m "feat: add accessible UI primitives"
```

---

### Task 4: Reuse prototype TH/EN catalogue

**Files:**
- Create: `app/src/i18n/catalog/th.json`, `app/src/i18n/catalog/en.json`, `app/src/i18n/index.ts`, `app/src/i18n/clinical.ts`
- Test: `app/src/i18n/i18n.test.ts`

**Interfaces:**
- Produces: `initI18n(locale: Locale, resources?: { th: Record<string,string>; en: Record<string,string> }): i18n`; `Locale = "th" | "en"`; `fallbackLng: "en"`; `t(key: string): string`.
- Produces: `translateClinicalText(text: string): string` — identity by contract; callers must use it instead of `t()` for clinician/patient-authored content.
- Key policy: reuse keys from the prototype runtime snapshot verbatim. Key sets need not be identical; a Thai value may be absent and fall back. No new namespace is introduced.

**Source catalogue (exists outside this repo):**
`/Users/sunphunkosol/Library/Application Support/Open Design/namespaces/release-stable/data/projects/b741c393-f46e-46bb-8e71-c0652dc75d7c/handoff/clinic-runtime-translations.json`
This is the OpenDesign project's runtime union, not a repo file. Do not invent keys; if the path is unavailable, stop and report rather than authoring a replacement catalogue.

- [ ] **Step 1: Write failing tests**

```ts
it("loads both catalogues with non-empty values", () => {
  for (const dict of [th, en]) for (const [k, v] of Object.entries(dict)) expect(v.trim(), k).not.toBe("");
});
it("renders required shell keys instead of returning the key", () => {
  const i18n = initI18n("th");
  for (const key of ["nav.today", "nav.patients", "app.name"]) expect(i18n.t(key)).not.toBe(key);
});
it("falls back to English when a Thai value is absent", () => {
  const i18n = initI18n("th", { th: {}, en: { "nav.today": "Today" } });
  expect(i18n.t("nav.today")).toBe("Today");
});
it("keeps clinical free text untranslated", () => {
  expect(translateClinicalText("ผู้ป่วยมีอาการแพ้ยา")).toBe("ผู้ป่วยมีอาการแพ้ยา");
});
it("does not translate a record id passed as free text", () => {
  expect(translateClinicalText("PAT-0001")).toBe("PAT-0001");
});
```

- [ ] **Step 2: Run to verify failure**

Run: `pnpm --filter @clinic/app test src/i18n/i18n.test.ts`
Expected: FAIL — catalogues/index missing.

- [ ] **Step 3: Import the external catalogue and implement init**

Read the JSON above and write the `{key, th, en}` rows into both catalogues, preserving keys. Drop demo-only strings (fictional person names, sample amounts) and list them in `app/src/i18n/demo-strings.txt`. Do **not** rewrite keys or add a namespace. `initI18n` merges optional test resources over the JSON, sets `fallbackLng: "en"`, `interpolation.escapeValue: false`, and returns the i18next instance. `translateClinicalText` returns its input.

- [ ] **Step 4: Run to verify pass**

Run: `pnpm --filter @clinic/app test src/i18n/i18n.test.ts`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add app/src/i18n
git commit -m "feat: reuse prototype TH/EN catalogue with safe fallback"
```

---

### Task 5: Locale provider, formatters, and mid-draft switch

**Files:**
- Create: `app/src/i18n/LocaleProvider.tsx`, `app/src/i18n/format.ts`
- Test: `app/src/i18n/locale-provider.test.tsx`, `app/src/i18n/format.test.ts`

**Interfaces:**
- Produces: `LocaleProvider({initial: Locale, children})`; `useLocale(): {locale, setLocale}`; `formatDate(iso: string, locale: Locale): string`; `formatMoney(amount: number, locale: Locale): string`; `formatDateTime(iso: string, locale: Locale): string`.

- [ ] **Step 1: Write failing tests**

```tsx
it("preserves an uncontrolled draft value across a locale switch", () => {
  render(<Harness />);
  fireEvent.change(screen.getByLabelText(/note|บันทึก/i), { target: { value: "draft text" } });
  fireEvent.click(screen.getByRole("button", { name: "EN" }));
  expect(screen.getByLabelText(/note|บันทึก/i)).toHaveValue("draft text");
});
it("formats dates deterministically in Gregorian Bangkok time", () => {
  expect(formatDate("2026-11-03T00:00:00Z", "th")).toBe("03/11/2026");
  expect(formatDate("2026-11-03T00:00:00Z", "en")).toBe("03/11/2026");
  expect(formatDateTime("2026-11-03T07:05:00Z", "en")).toBe("03/11/2026 14:05");
});
it("formats THB exactly per locale", () => {
  expect(formatMoney(1200, "th")).toBe("฿1,200.00");
  expect(formatMoney(1200, "en")).toBe("THB 1,200.00");
});
```

- [ ] **Step 2: Run to verify failure**

Run: `pnpm --filter @clinic/app test src/i18n`
Expected: FAIL — modules missing.

- [ ] **Step 3: Implement provider and formatters**

Provider stores `locale` in React state and sets `document.documentElement.lang`. Formatters are explicit, not locale-data dependent: convert the ISO instant to `Asia/Bangkok` with `Intl.DateTimeFormat("en-GB", { timeZone: "Asia/Bangkok", ... }).formatToParts`, then assemble `DD/MM/YYYY` and `DD/MM/YYYY HH:mm`; format money with `Intl.NumberFormat("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })` and prefix `฿` (th) or `THB ` (en). No Buddhist-era conversion and no reliance on host locale defaults.

- [ ] **Step 4: Run to verify pass**

Run: `pnpm --filter @clinic/app test src/i18n`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/src/i18n app/src/i18n/format.ts
git commit -m "feat: add locale provider with draft-preserving switch"
```

---

### Task 6: Error taxonomy and localised mapping

**Files:**
- Create: `app/src/domain/errors.ts`, `app/src/domain/errors.test.ts`, `app/src/transport/error-map.ts`

**Interfaces:**
- Produces: `type ErrorCode = "AUTH_EXPIRED" | "FORBIDDEN" | "VALIDATION" | "CONFLICT_STALE" | "CONFLICT_DUPLICATE" | "UPSTREAM_UNAVAILABLE" | "RATE_LIMITED" | "FILE_TOO_LARGE" | "UNSUPPORTED" | "UNKNOWN"`.
- Produces: `type AppError = {code: ErrorCode; retryable: boolean; fieldErrors?: Record<string,string>}`; `ERROR_CODES: readonly ErrorCode[]`; `fromWire(code: unknown): ErrorCode` returning `"UNKNOWN"` for any unrecognised value; `toI18nKey(error: AppError): string`; `UNKNOWN_FALLBACK_KEY: "errors.UNKNOWN"`.
- Tests import `th.json` and `en.json` directly to assert `errors.<code>` exists for every `ERROR_CODES` entry.

- [ ] **Step 1: Write failing tests**

```ts
it("maps AUTH_EXPIRED to a localisable key and marks it retryable after re-auth", () => {
  expect(toI18nKey({ code: "AUTH_EXPIRED", retryable: true })).toBe("errors.AUTH_EXPIRED");
});
it("maps any unrecognised wire code to UNKNOWN", () => {
  expect(fromWire("SOMETHING_NEW")).toBe("UNKNOWN");
  expect(fromWire(undefined)).toBe("UNKNOWN");
  expect(fromWire(42)).toBe("UNKNOWN");
});
it("falls back for unknown codes without exposing server text", () => {
  expect(toI18nKey({ code: "UNKNOWN", retryable: false })).toBe("errors.UNKNOWN");
});
it("has a catalogue entry for every error code", () => {
  for (const code of ERROR_CODES) {
    expect(thCatalog[`errors.${code}`], code).toBeTruthy();
    expect(enCatalog[`errors.${code}`], code).toBeTruthy();
  }
});
it("never includes a stack or server message in the client error", () => {
  expect(JSON.stringify({ code: "VALIDATION", retryable: false })).not.toMatch(/Traceback|frappe/i);
});
```

- [ ] **Step 2: Run to verify failure**

Run: `pnpm --filter @clinic/app test src/domain/errors.test.ts`
Expected: FAIL.

- [ ] **Step 3: Implement taxonomy and map**

`error-map.ts` maps HTTP status + machine code through `fromWire`; 5xx becomes `UPSTREAM_UNAVAILABLE`; a recognised but unsupported operation is `UNSUPPORTED`; any unknown body shape becomes `UNKNOWN`. Add the missing `errors.*` entries for every code to both catalogues (same namespace rule), with Thai and English copy — this is the one permitted catalogue addition in this plan.

- [ ] **Step 4: Run to verify pass**

Run: `pnpm --filter @clinic/app test src/domain/errors.test.ts`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/src/domain/errors.ts app/src/domain/errors.test.ts app/src/transport/error-map.ts
git commit -m "feat: add error taxonomy with safe localisation mapping"
```

---

### Task 7: Proxy — origin allow-list, browser CSRF, pre-session and login

**Files:**
- Create: `proxy/src/security/constants.ts`, `proxy/src/security/origin.ts`, `proxy/src/security/csrf.ts`, `proxy/src/security/csrf-store.ts`
- Create: `proxy/src/session/store.ts`
- Create: `proxy/src/routes/auth.ts`
- Modify: `proxy/src/server.ts`, `proxy/src/config.ts` (already exists from Task 1)
- Test: `proxy/src/security/security.test.ts`, `proxy/src/security/csrf-store.test.ts`

**Interfaces:**
- Produces constants: `CSRF_HEADER = "x-clinic-csrf"`, `CSRF_COOKIE = "dc_csrf"`, `SESSION_COOKIE = "dc_session"`. These are proxy-owned names; the upstream CSRF header is never exposed.
- Produces: `assertTrustedOrigin(req, appOrigin: string): boolean` — exact `Origin` match; when absent, exact origin parsed from `Referer`; when both absent or unparseable, `false`.
- Produces: `CsrfStore` with `issue(ownerId: string, purpose: "login" | "session"): string` (returns plaintext once), `verify(ownerId, token, purpose): boolean`, `consume(ownerId, token, purpose): boolean`, `revokeAll(ownerId): void`. Tokens are stored as SHA-256 hashes, expire after 30 minutes for login and 8 hours for session.
- Produces: `SessionStore` with `create(user: {id, displayName, roles: RoleCode[]}): SessionId`, `get(id): SessionRecord | undefined`, `rotate(oldId): SessionId`, `destroy(id): void` where `SessionRecord = {userId: string; displayName: string; roles: RoleCode[]; createdAt: number; expiresAt: number}`. Roles are captured at login so read handlers never trust a request-supplied role.
- Produces DTOs (exact shape shared with the app): `CsrfResponse = {token: string}`; `LoginRequest = {usr: string; pwd: string}`; `SessionDTO = {user: {id: string; displayName: string; roles: RoleCode[]}; capabilities: CapabilityMap}` (the same type Plan 01 Task 9 and Plan 02 consume; `Record<string, CapabilityState>` is never used).
- Routes: `GET /api/auth/csrf` → sets `dc_csrf` pre-session cookie if absent, returns `CsrfResponse`; `POST /api/auth/login` → requires `CSRF_HEADER`, creates `dc_session`, rotates the token, deletes the pre-session, returns `SessionDTO`; `POST /api/auth/logout` → requires `CSRF_HEADER`, destroys the session + tokens, clears both cookies, `204`.
- Cookie flags: `HttpOnly`, `SameSite=Lax`, `Path=/`; `Secure` only when `APP_ORIGIN` starts with `https://`.

**Storage schema (created before any route uses it):**

```sql
pre_sessions(id TEXT PRIMARY KEY, created_at INTEGER NOT NULL, expires_at INTEGER NOT NULL);
csrf_tokens(token_hash TEXT PRIMARY KEY, owner_id TEXT NOT NULL, purpose TEXT NOT NULL,
            expires_at INTEGER NOT NULL, used_at INTEGER);
sessions(id TEXT PRIMARY KEY, user_id TEXT NOT NULL, display_name TEXT NOT NULL,
         roles_json TEXT NOT NULL, created_at INTEGER NOT NULL, expires_at INTEGER NOT NULL);
```

- [ ] **Step 1: Write failing origin and CSRF tests**

```ts
it("rejects a login from a different origin", async () => {
  const res = await app.inject({ method: "POST", url: "/api/auth/login",
    headers: { origin: "https://evil.example", "content-type": "application/json" }, payload: { usr: "a", pwd: "b" } });
  expect(res.statusCode).toBe(403);
});
it("rejects a request with neither Origin nor Referer", async () => {
  const res = await app.inject({ method: "POST", url: "/api/auth/login", headers: { "content-type": "application/json" } });
  expect(res.statusCode).toBe(403);
});
it("accepts a same-origin Referer when Origin is absent", async () => {
  const res = await app.inject({ method: "POST", url: "/api/auth/login",
    headers: { referer: `${TEST_ORIGIN}/clinic/login`, "content-type": "application/json" } });
  expect(res.statusCode).not.toBe(403);
});
it("rejects login without the proxy CSRF header even from the right origin", async () => {
  const res = await app.inject({ method: "POST", url: "/api/auth/login",
    headers: { origin: TEST_ORIGIN, "content-type": "application/json" }, payload: { usr: "a", pwd: "b" } });
  expect(res.statusCode).toBe(403);
});
it("rejects a state change sent as text/plain", async () => {
  const res = await app.inject({ method: "POST", url: "/api/auth/logout",
    headers: { origin: TEST_ORIGIN, "content-type": "text/plain" } });
  expect(res.statusCode).toBe(415);
});
it("rejects a login token issued for a different pre-session", async () => {
  const a = await getCsrf();
  const b = await getCsrf();
  const res = await postLoginWith(a.token, b.csrfCookie);
  expect(res.statusCode).toBe(403);
});
it("rotates the token on login and rejects replay of the login token", async () => {
  const first = await getCsrf();
  const login = await postLogin(first);
  expect(login.statusCode).toBe(200);
  const replay = await postLoginWith(first.token, first.csrfCookie);
  expect(replay.statusCode).toBe(403);
  const second = await getCsrf(login.cookies);
  expect(second.token).not.toBe(first.token);
});
it("returns a SessionDTO and sets an HttpOnly session cookie", async () => {
  const login = await postLogin(await getCsrf());
  expect(login.json()).toMatchObject({ user: { id: expect.any(String), roles: expect.any(Array) }, capabilities: expect.any(Object) });
  expect(login.cookies.find((c) => c.name === "dc_session")?.httpOnly).toBe(true);
  expect(JSON.stringify(login.json())).not.toMatch(/csrf|token/i);
});
```

`proxy/src/security/csrf-store.test.ts`:

```ts
it("stores only a hash, never the plaintext token", () => {
  const token = csrf.issue("pre-1", "login");
  const rows = db.prepare("SELECT token_hash FROM csrf_tokens").all();
  expect(JSON.stringify(rows)).not.toContain(token);
});
it("consumes a login token exactly once", () => {
  const token = csrf.issue("pre-1", "login");
  expect(csrf.consume("pre-1", token, "login")).toBe(true);
  expect(csrf.consume("pre-1", token, "login")).toBe(false);
});
it("expires a login token after thirty minutes", () => {
  const token = csrf.issue("pre-1", "login");
  vi.advanceTimersByTime(30 * 60 * 1000 + 1);
  expect(csrf.verify("pre-1", token, "login")).toBe(false);
});
it("rejects a token presented with a different owner", () => {
  const token = csrf.issue("pre-1", "login");
  expect(csrf.verify("pre-2", token, "login")).toBe(false);
});
```

- [ ] **Step 2: Run to verify failure**

Run: `pnpm --filter @clinic/proxy test src/security/security.test.ts`
Expected: FAIL — routes and stores missing.

- [ ] **Step 3: Implement storage, origin and CSRF checks**

Create the three tables before registering routes. Order for every state-changing request: origin → content-type (`application/json` for login/logout, `multipart/form-data` only on the upload route) → CSRF → credential/permission check. `GET /api/auth/csrf` may create a pre-session row and cookie; it never authenticates. Login verifies the token against the `dc_csrf` owner, consumes it, authenticates upstream, creates the session, issues a session-purpose token, deletes the pre-session, and clears `dc_csrf`. Store only token hashes. Log `{event, ipHash, route}`.

- [ ] **Step 4: Run to verify pass**

Run: `pnpm --filter @clinic/proxy test src/security/security.test.ts`
Expected: PASS (8 tests).

- [ ] **Step 5: Commit**

```bash
git add proxy/src/security proxy/src/session/store.ts proxy/src/routes/auth.ts proxy/src/server.ts
git commit -m "feat: protect proxy login and mutations with origin and csrf checks"
```

---

### Task 8: Session rotation, logout, and expiry

**Files:**
- Modify: `proxy/src/session/store.ts`, `proxy/src/routes/auth.ts`, `proxy/src/server.ts`
- Test: `proxy/src/session/store.test.ts`, `proxy/src/routes/auth.test.ts`

**Interfaces:**
- Extends `SessionStore` with `destroyAllForUser(userId): void`.
- `rotate(oldId)` deletes the old row and inserts a new id preserving `userId`, `displayName` and `roles`; `logout` destroys the session, calls `CsrfStore.revokeAll(sessionId)`, clears `dc_session` and `dc_csrf`.

- [ ] **Step 1: Write failing tests**

```ts
it("rotates the session id and invalidates the old one", () => {
  const s = store.create({ id: "u1", displayName: "Test", roles: ["REC"] });
  const r = store.rotate(s);
  expect(store.get(s)).toBeUndefined();
  expect(store.get(r)?.userId).toBe("u1");
});
it("survives a store reopen with roles intact", () => {
  const s = store.create({ id: "u1", displayName: "Test", roles: ["REC"] });
  expect(openStore(file).get(s)).toMatchObject({ userId: "u1", roles: ["REC"] });
});
it("logout destroys the session and revokes its CSRF tokens", async () => {
  const login = await postLogin(await getCsrf());
  const sid = cookieValue(login, "dc_session");
  const token = await getCsrf(login.cookies);
  const out = await postLogout(token.token, login.cookies);
  expect(out.statusCode).toBe(204);
  expect(store.get(sid)).toBeUndefined();
  expect(csrf.verify(sid, token.token, "session")).toBe(false);
});
it("expires an idle session after eight hours", () => {
  vi.setSystemTime(new Date("2026-11-05T00:00:00Z"));
  const s = store.create({ id: "u1", displayName: "Test", roles: ["REC"] });
  vi.setSystemTime(new Date("2026-11-05T08:00:01Z"));
  expect(store.get(s)).toBeUndefined();
});
```

- [ ] **Step 2: Run to verify failure**

Run: `pnpm --filter @clinic/proxy test src/session`
Expected: FAIL.

- [ ] **Step 3: Implement rotation, expiry and logout**

TTL 8 hours, no sliding renewal in this plan (rolling renewal is a separate decision). Expired rows are filtered on read and deleted lazily. Logout is idempotent: a missing session still returns `204` after clearing cookies.

- [ ] **Step 4: Run to verify pass**

Run: `pnpm --filter @clinic/proxy test`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add proxy/src/session proxy/src/routes/auth.ts proxy/src/server.ts
git commit -m "feat: add rotating sessions with logout revocation"
```

---

### Task 9: Capability descriptor with default-deny registry

**Files:**
- Create: `contracts/capabilities.json`
- Create: `app/src/domain/capability.ts`, `app/src/domain/capability.test.tsx`, `proxy/src/capabilities.ts`, `proxy/src/capabilities.test.ts`
- Create: `app/src/features/shell/CapabilityProvider.tsx`, `app/src/ui/WriteGate.tsx`, `app/src/ui/write-gate.test.tsx`

**Interfaces:**
- Produces: `type RoleCode = "REC" | "DOC" | "NUR" | "PHA" | "CAS" | "MGR" | "ADM"`.
- Produces: `type Capability = "write.patient.create" | "write.patient.update" | "write.appointment.book" | "write.appointment.reschedule" | "write.appointment.cancel" | "write.appointment.checkin" | "write.encounter.draft" | "write.encounter.complete" | "write.medication.order" | "write.procedure.complete" | "write.lab.order" | "write.lab.review" | "write.photo.consent" | "write.photo.upload" | "write.photo.link" | "write.photo.annotate" | "write.stock.receive" | "write.stock.transfer" | "write.stock.adjust" | "write.purchase_order.draft" | "write.purchase_order.submit" | "write.invoice.submit" | "write.payment.receive" | "write.payment.refund" | "write.admin.user" | "write.admin.master" | "read.patient" | "read.appointment" | "read.encounter" | "read.lab" | "read.photo" | "read.inventory" | "read.billing" | "read.report" | "read.admin"`.
- Produces: `type CapabilityState = "enabled" | "read_only" | "blocked_unverified" | "denied" | "error_upstream"`; `type CapabilityMap = Partial<Record<Capability, CapabilityState>>`. These are the wire values; they map 1:1 to spec §6.1: `enabled` = `ENABLED_VERIFIED`, `read_only` = `READ_ONLY`, `blocked_unverified` = `BLOCKED_UNVERIFIED`, `denied` = `DENIED_FOR_ROLE`, `error_upstream` = `ERROR_UPSTREAM`. No other spelling is introduced anywhere.
- Produces: `CAPABILITY_NAMES: readonly Capability[]` (order and names match `contracts/capabilities.json`); `isCapability(value: string): value is Capability`; `getCapability(map: CapabilityMap, c: Capability): CapabilityState` defaulting to `"read_only"`.
- Produces: `CapabilityProvider({value: CapabilityMap, children})`; `useCapabilities(): {caps: CapabilityMap, get(c: Capability): CapabilityState}`.
- Produces the **canonical disabled gate**: `WriteGate({capability, reason?, children})` accepts one child element.
  - Routed navigation uses explicit `RoutedControl` from `@/ui/WriteGate`, placed directly under the gate. It preserves mounted content/ref while removing disabled href. Supported roots are `RoutedControl`, shared `Button`, or native `button/a/input/select/textarea`; arbitrary custom roots and ordinary Router `Link` fail closed even for enabled capabilities. Compose custom stateful content inside a supported root, or let a custom routed component own its gate internally and forward its ref to the direct `RoutedControl` child; never infer compatibility from a `to` prop.
  - `enabled`: renders the child unchanged.
  - `read_only` | `blocked_unverified` | `error_upstream`: renders the child visibly disabled (cloned with `disabled`, or `aria-disabled="true"` for links) plus the localised reason, and never unmounts it.
  - `denied`: renders `PermissionDenied` in place of the control (spec §6.1: `DENIED_FOR_ROLE` is hidden), never an enabled control.
  - `reason` overrides default copy. This is the only write-gate primitive; Plans 02–06 import it and must not define another.
- Produces proxy `buildCapabilityMap(active: ReadonlySet<string>): CapabilityMap` returning every name in `contracts/capabilities.json`, defaulting to `"read_only"`, and `"enabled"` only for explicitly active and contract-verified operations.

- [ ] **Step 1: Write failing tests**

```tsx
it("defaults every unknown capability to read_only", () => {
  expect(getCapability({}, "write.patient.create")).toBe("read_only");
});
it("preserves an explicit blocked or upstream-error state", () => {
  expect(getCapability({ "write.patient.create": "blocked_unverified" }, "write.patient.create")).toBe("blocked_unverified");
  expect(getCapability({ "write.patient.create": "error_upstream" }, "write.patient.create")).toBe("error_upstream");
});
it("matches the shared capability contract exactly", () => {
  expect([...CAPABILITY_NAMES].sort()).toEqual([...contractNames].sort());
  expect(CAPABILITY_NAMES.every(isCapability)).toBe(true);
});
// The next two tests live in `app/src/ui/write-gate.test.tsx`.
it("renders the control visibly disabled with a reason when not enabled", () => {
  render(<CapabilityProvider value={{}}><WriteGate capability="write.patient.create"><button>Save</button></WriteGate></CapabilityProvider>);
  const save = screen.getByRole("button", { name: "Save" });
  expect(save).toBeDisabled();
  expect(save).toHaveAttribute("aria-describedby");
  expect(screen.getByText(/ยังไม่เปิดใช้งาน|not available/i)).toBeVisible();
});
it("renders the child unchanged when enabled", () => {
  render(<CapabilityProvider value={{ "write.patient.create": "enabled" }}><WriteGate capability="write.patient.create"><button>Save</button></WriteGate></CapabilityProvider>);
  expect(screen.getByRole("button", { name: "Save" })).toBeEnabled();
});
it("replaces the control with permission-denied when the role is denied", () => {
  render(<CapabilityProvider value={{ "write.patient.create": "denied" }}><WriteGate capability="write.patient.create"><button>Save</button></WriteGate></CapabilityProvider>);
  expect(screen.queryByRole("button", { name: "Save" })).toBeNull();
  expect(screen.getByText(/ไม่มีสิทธิ์|permission denied/i)).toBeVisible();
});
it("keeps the control disabled with a retry path when upstream errors", () => {
  render(<CapabilityProvider value={{ "write.patient.create": "error_upstream" }}><WriteGate capability="write.patient.create"><button>Save</button></WriteGate></CapabilityProvider>);
  expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
  expect(screen.getByText(/ลองอีกครั้ง|try again/i)).toBeVisible();
});
```

Proxy test in `proxy/src/capabilities.test.ts`:

```ts
it("emits every contract capability and defaults to read_only", () => {
  const map = buildCapabilityMap(new Set());
  expect(Object.keys(map).sort()).toEqual([...contractNames].sort());
  expect(Object.values(map).every((s) => s === "read_only")).toBe(true);
});
it("enables only the explicitly active operation", () => {
  const map = buildCapabilityMap(new Set(["write.patient.create"]));
  expect(map["write.patient.create"]).toBe("enabled");
  expect(map["write.patient.update"]).toBe("read_only");
});
```

- [ ] **Step 2: Run to verify failure**

Run: `pnpm --filter @clinic/app test src/domain/capability.test.tsx && pnpm --filter @clinic/proxy test src/capabilities.test.ts`
Expected: FAIL.

- [ ] **Step 3: Implement descriptor and provider**

`contracts/capabilities.json` (workspace package `@clinic/contracts`) is the single name list, imported by both packages as `@clinic/contracts/capabilities.json`. Proxy builds the map from it; the app's `CAPABILITY_NAMES` is typed against the union and compared to the JSON in tests. Proxy never marks a write `enabled` without an active flag and a verified manifest entry (Plan 06); in this plan everything is `read_only`. The React provider never derives capabilities from roles. `WriteGate` is implemented here and exported from `@/ui/WriteGate` for all later plans.

- [ ] **Step 4: Run to verify pass**

Run: `pnpm --filter @clinic/app test src/domain/capability.test.tsx src/ui/write-gate.test.tsx && pnpm --filter @clinic/proxy test src/capabilities.test.ts`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add contracts/capabilities.json app/src/domain/capability.ts app/src/domain/capability.test.tsx app/src/features/shell/CapabilityProvider.tsx app/src/ui/WriteGate.tsx app/src/ui/write-gate.test.tsx proxy/src/capabilities.ts proxy/src/capabilities.test.ts
git commit -m "feat: add default-deny capability descriptor"
```

---

### Task 10: App entry, router skeleton, session DTO, and proxy registration

**Files:**
- Create: `app/src/app/App.tsx`, `app/src/app/providers.tsx`, `app/src/app/router.tsx`, `app/src/app/routes.ts`, `app/src/app/SessionProvider.tsx`
- Create: `app/src/domain/session.ts`
- Modify: `app/src/main.tsx`
- Create: `proxy/src/routes/health.ts`, `proxy/src/routes/session.ts`, `proxy/src/routes/index.ts`
- Modify: `proxy/src/server.ts`
- Test: `app/src/app/app.test.tsx`, `proxy/src/routes/health.test.ts`, `proxy/src/routes/session.test.ts`

**Interfaces:**
- Produces `ROUTES` in `app/src/app/routes.ts`: at minimum `{login: "/clinic/login", home: "/clinic", search: "/clinic/search", system: "/clinic/system", profile: "/clinic/profile"}`. Later plans extend this object; they do not hardcode paths in tests.
- Produces `SessionDTO` in `app/src/domain/session.ts`, matching the proxy exactly: `{user: {id: string; displayName: string; roles: RoleCode[]}; capabilities: CapabilityMap}`. `SessionProvider({children})` fetches `GET /api/session`, exposes `useSession(): {status: "loading" | "authenticated" | "anonymous"; session: SessionDTO | null; refresh(): Promise<void>}`. A `401` yields `anonymous`, never an error state.
- Produces proxy `registerRoutes(app: FastifyInstance): void` registering `/api/auth/*`, `/api/health`, `/api/session`; `buildServer(): FastifyInstance` creates the instance, registers cookies, stores and routes; `server.ts` calls `buildServer().listen(...)` only when executed directly.
- Produces: `GET /api/health` → `{status:"ok", upstream:"fixture"|"unreachable"}`; `GET /api/session` → `SessionDTO` or `401 {code:"AUTH_EXPIRED"}`.
- `app/src/main.tsx` imports `./styles/index.css`, wraps `<App />` in `StrictMode`, and mounts to `#root`. This is the only place ReactDOM is called.

- [ ] **Step 1: Write failing tests**

`app/src/app/app.test.tsx`:

```tsx
import { render, screen, waitFor } from "@testing-library/react";

const okSession = { user: { id: "u1", displayName: "Test", roles: ["REC"] }, capabilities: {} };

beforeEach(() => {
  vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify(okSession), { status: 200, headers: { "content-type": "application/json" } })));
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
  vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ code: "AUTH_EXPIRED" }), { status: 401 })));
  render(<SessionProvider><SessionProbe /></SessionProvider>);
  await waitFor(() => expect(screen.getByText("anonymous")).toBeVisible());
});
```

Plan 01 has no MSW layer; these tests stub `fetch` directly. MSW handlers arrive in Plan 02 Task 2, which must not replace `SessionProvider`.

`proxy/src/routes/session.test.ts`:

```ts
it("returns 401 for an unauthenticated session", async () => {
  const res = await buildServer().inject({ method: "GET", url: "/api/session" });
  expect(res.statusCode).toBe(401);
  expect(res.json()).toEqual({ code: "AUTH_EXPIRED" });
});
it("returns the exact SessionDTO shape for an authenticated session", async () => {
  const { cookie } = await loginAsTestUser();
  const res = await buildServer().inject({ method: "GET", url: "/api/session", headers: { cookie } });
  expect(res.json()).toEqual({ user: { id: expect.any(String), displayName: expect.any(String), roles: expect.any(Array) }, capabilities: expect.any(Object) });
});
it("reports fixture upstream without leaking configuration", async () => {
  const res = await buildServer().inject({ method: "GET", url: "/api/health" });
  expect(res.json()).toEqual({ status: "ok", upstream: "fixture" });
});
```

- [ ] **Step 2: Run to verify failure**

Run: `pnpm -w test`
Expected: FAIL — `App`, providers, session provider and routes missing.

- [ ] **Step 3: Implement entry, session provider and proxy registration**

`providers.tsx` composes QueryClientProvider, LocaleProvider (i18n initialized once), CapabilityProvider and ErrorBoundary. `router.tsx` defines `ROUTES.home` as a placeholder shell surface (`data-testid="shell-placeholder"`); the real sign-in page lands in Plan 02 and mounts at `ROUTES.login`. `SessionProvider` performs one query on mount and on `refresh()`; it never retries a 401. `registerRoutes` is called by `buildServer`; no route is registered twice.

- [ ] **Step 4: Run to verify pass**

Run: `pnpm -w test && pnpm -w typecheck`
Expected: PASS; typecheck clean.

- [ ] **Step 5: Commit**

```bash
git add app/src/app app/src/domain/session.ts app/src/main.tsx proxy/src/routes proxy/src/server.ts
git commit -m "feat: wire app entry, session dto and proxy routes"
```

---

## Self-Review

| Spec area | Covered by | Interface exported for later plans |
|---|---|---|
| §3.3 same-origin proxy, browser CSRF/origin incl. login | Tasks 7–8 | `CSRF_HEADER`, `CSRF_COOKIE`, `SESSION_COOKIE`, `CsrfStore`, `/api/auth/csrf` |
| §3.5 auth/role behaviour | Tasks 7–8, 10 (role matrix remains decision-required) | `SessionStore`, `SessionDTO`, `SessionProvider`, `useSession` |
| §3.2 layering (routes, transport, capabilities) | Task 10 | `ROUTES` |
| §4 TH/EN reuse, fallback, formatting | Tasks 4–5 | `initI18n`, `translateClinicalText`, `LocaleProvider`, formatters |
| §5 tokens, 44 px, focus | Tasks 2–3 | `--neutral`, `--target-min`, `ui.css` primitives |
| §6.1 capability states, default deny | Task 9 | `CapabilityState` incl. `error_upstream`, `contracts/capabilities.json`, proxy `buildCapabilityMap`, canonical `WriteGate` |
| §6.4 prohibited implementations | Global constraints; no mutation route or mutation hook exists | none (absence is the guarantee) |
| §3.3.8 error taxonomy | Task 6 | `ERROR_CODES`, `fromWire`, `errors.*` keys |
| §8.1 D0 exit criteria | Tasks 1–10 (sign-in UI itself is Plan 02 Task 4) | — |
| §9.1 security + i18n tests | Tasks 4, 6, 7, 8, 9 | — |

**Cross-plan contracts fixed here (later plans consume, never redefine):** `ROUTES` (Task 10), `SessionDTO`/`useSession` (Task 10), `CSRF_HEADER` + `GET /api/auth/csrf` handshake (Task 7), `CapabilityState` incl. `error_upstream` (Task 9), and `WriteGate` (Task 9).

**Deferred deliberately:** read adapters (Plan 02), any write route (Plan 06), live upstream calls (no contract exists).

**Known gap:** real upstream login cannot be implemented until BC3-18 is recorded. Task 7's upstream call is an interface with a fixture implementation; `UPSTREAM_MODE=live` must throw `UNSUPPORTED` until a contract file exists.
