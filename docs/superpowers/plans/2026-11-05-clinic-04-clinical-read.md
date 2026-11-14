# Clinic Platform — 04 Clinical Read Delivery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver the doctor, nurse and clinical-support surfaces (DOC-01…04, NUR-01/02, PAT-04/05 read) as read-only screens: one visit workspace with nine sections, photo viewing/comparison, lab detail, with no ordering, completion, annotation-save or review action enabled.

**Architecture:** A single `/clinic/visits/:id` route owns the encounter context and renders section components that read independently. Section components never fetch patient identity themselves; the workspace provides it. Photo and lab features reuse the same read layer and the same `WriteGate`.

**Tech Stack:** React 18, TanStack Query, React Router nested routes, Zod, MSW, Vitest/RTL, Playwright.

**Spec:** `docs/superpowers/specs/2026-11-05-clinic-application-design.md`

**Parallelism:** Depends on Plans 01–02. Runs in parallel with Plans 03 and 05. Plan 06's `WA-VST-01a`, `WA-MED-01`, `WA-VST-01b` depend on this plan; photo/lab writes are A7+.

## Global Constraints

**Read contract:** all data comes from Plan 02's `useReadQuery`/`readQuery` allow-list; tests use Plan 02's `renderWithCaps`/`renderAt`/`server`. This plan adds resources to the registry but never adds a fetch wrapper.
**Routing contract:** extend `ROUTES` with `doctor`, `visit(id)`, `labs`, `lab(id)`, `procedure(id)`, `nurse`, `nurseTasks`, `patientPhotos(id)`, `photoCompare(id)`. Product links use those helpers; tests may assert literal URLs. Each task that introduces a route adds its `ROUTES` key **and** its `NAV_MANIFEST` item (`owner: "04"`) in the same task; nav paths resolve through `ROUTES` only and must not reference a route that does not exist yet.
**Fixture contract:** extend `contracts/fixtures/*.json`, `READ_RESOURCES`, `createHandlers()` and `FixtureOverrides` from Plan 02. Do not create a second fixture directory, MSW server, session hook or fetch wrapper. Fixture role filters remain `matrix:"unverified"` and are not authorization.
**Release classification:** this plan is interim read-only, not completed P0. Clinical P0 requires `WA-VST-01a/b` and `WA-MED-01`; photo P0 requires `WA-PHO-01…04`. Preview annotation is explicitly not the P0 annotation capability.

- Exactly one editable target per clinical fact, and in this plan zero editable targets: all clinical fields read-only.
- Prior history is rendered read-only; no copy-into-note control.
- Free text is never written to display/layout fields — no write paths exist here at all.
- The patient/encounter header is sticky and persists across section changes.
- Photos: no annotation save, no original mutation, no public file URL.
- Labs: no status transition and no review action; unknown status renders raw + annotation.
- TH/EN complete; 390/834/1440 reviewed; note sections stack as accordions on phone.

## Review Focus

1. **Deep link to a visit with a missing encounter id** — clear not-found/forbidden state, no empty workspace that invites data entry. Test: `shows not found for an unknown visit`.
2. **Two encounters for the same patient on the same day** — header always shows the encounter id, and sections never switch context. Test: `keeps the encounter id across section changes`.
3. **Vitals recorded in different units** — units are shown per value; no implicit conversion. Test: `renders each vital with its own unit`.
4. **Photo without consent evidence, or an unsaved annotation at 390 px** — consent-missing state with no capture affordance; touch toolbar keeps 44 px targets, no horizontal scroll, and dirty shapes survive a locale switch. Tests: `shows a consent-missing state and no capture action` (Task 5); `annotation toolbar is touch-usable at 390` (Task 10).
5. **Lab marked reviewed with no reviewer** — the UI must not claim review; shows raw state + annotation. Test: `flags reviewed-without-reviewer as not verified`.

---

### Task 1: Clinical domain types and section model

**Files:**
- Create: `app/src/domain/clinical.ts`, `app/src/features/clinical/sections.ts`
- Create: `contracts/fixtures/clinical.json` (visit context, sections, vitals, diagnoses, medications, photos, labs, nurse tasks, procedures; all `fictional: true`)
- Modify: `app/src/test/msw/handlers.ts` (clinical `READ_RESOURCES`, scenario keys `encounterId`, `missing`, `weight`, `allergies`, `state`, `savedAt`, `diagnoses`, `photo`, `files`, `consent`, `reviewer`, `expired`, `draft`, `counts`)
- Test: `app/src/domain/clinical.test.ts`

**Interfaces:**
- Produces: `VisitSection = "overview"|"history"|"exam"|"note"|"diagnoses"|"medications"|"procedures"|"lab"|"photos"|"review"`; `VisitContext = {encounterId, patientId, patientName, appointmentId?, practitionerName, startedAt, state}`; `SECTION_ORDER: VisitSection[]`; `sectionLabelKey(s): string`.
- Fixture rows are consumed through Plan 02's `renderWithFixtures`; this plan does not create a second fixture source.

- [ ] **Step 1: Write failing tests**

```ts
it("has exactly one section per spec section", () => {
  expect(SECTION_ORDER).toEqual(["overview","history","exam","note","diagnoses","medications","procedures","lab","photos","review"]);
});
it("labels every section in both catalogues", () => {
  const th = initI18n("th"), en = initI18n("en");
  for (const s of SECTION_ORDER) {
    expect(th.t(sectionLabelKey(s))).not.toBe(sectionLabelKey(s));
    expect(en.t(sectionLabelKey(s))).not.toBe(sectionLabelKey(s));
  }
});
it("treats a missing encounter id as invalid context", () => {
  expect(isValidVisitContext({ ...baseCtx, encounterId: "" })).toBe(false);
});
```

- [ ] **Step 2: Run to verify failure**

Run: `pnpm --filter @clinic/app test src/domain/clinical.test.ts`
Expected: FAIL.

- [ ] **Step 3: Implement types, order, validation**

`isValidVisitContext` requires non-empty encounter, patient and practitioner. No optional encounter fallback.

- [ ] **Step 4: Run to verify pass**

Run: `pnpm --filter @clinic/app test src/domain/clinical.test.ts`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/src/domain/clinical.ts app/src/features/clinical/sections.ts
git commit -m "feat: add clinical section model and visit context validation"
```

---

### Task 2: Visit workspace shell with persistent context

**Files:**
- Create: `app/src/features/clinical/VisitWorkspacePage.tsx`, `VisitHeader.tsx`, `VisitSectionNav.tsx`, `SectionFrame.tsx`
- Test: `app/src/features/clinical/workspace.test.tsx`

**Interfaces:**
- Produces: `VisitWorkspacePage()` at `/clinic/visits/:id` with nested section route `?section=<VisitSection>`; `VisitHeader({context})`; `VisitSectionNav({active, onChange})`; `SectionFrame({section, children})`.

- [ ] **Step 1: Write failing tests**

```tsx
it("keeps the encounter id across section changes", async () => {
  renderWithFixtures(<VisitWorkspacePage />, { encounterId: "ENC-1" });
  expect(await screen.findByTestId("encounter-id")).toHaveTextContent("ENC-1");
  await user.click(screen.getByRole("tab", { name: /ประวัติ|history/i }));
  expect(screen.getByTestId("encounter-id")).toHaveTextContent("ENC-1");
});
it("shows not found for an unknown visit", async () => {
  renderWithFixtures(<VisitWorkspacePage />, { missing: true });
  expect(await screen.findByText(/ไม่พบ|not found/i)).toBeVisible();
  expect(screen.queryByRole("textbox")).toBeNull();
});
it("stacks sections as accordions at 390", async () => {
  setViewport(390);
  renderWithFixtures(<VisitWorkspacePage />);
  expect(await screen.findByTestId("section-accordion")).toBeVisible();
});
```

- [ ] **Step 2: Run to verify failure**

Run: `pnpm --filter @clinic/app test src/features/clinical/workspace.test.tsx`
Expected: FAIL.

- [ ] **Step 3: Implement workspace**

Desktop: tab rail + content. Phone: accordion with only the active section expanded. Header sticky, shows name, ID, date/time, appointment id, practitioner, state, plus a privacy notice when a shared-device flag is set.

- [ ] **Step 4: Run to verify pass**

Run: `pnpm --filter @clinic/app test src/features/clinical`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/src/features/clinical
git commit -m "feat: add single visit workspace shell with persistent context"
```

---

### Task 3: Overview, history and exam/vitals read

**Files:**
- Create: `app/src/features/clinical/OverviewSection.tsx`, `HistorySection.tsx`, `ExamSection.tsx`, `VitalsTable.tsx`
- Test: `app/src/features/clinical/history-exam.test.tsx`

**Interfaces:**
- Produces: `HistorySection({patientId, encounterId})` read-only; `ExamSection({encounterId})`; `VitalsTable({vitals})` showing value + unit + measuredAt + source.

- [ ] **Step 1: Write failing tests**

```tsx
it("marks prior history read-only and offers no copy control", async () => {
  renderWithFixtures(<HistorySection />);
  expect(await screen.findByTestId("history-readonly")).toBeVisible();
  expect(screen.queryByRole("button", { name: /copy|คัดลอก/i })).toBeNull();
  expect(screen.queryByRole("textbox")).toBeNull();
});
it("renders each vital with its own unit", async () => {
  renderWithFixtures(<VitalsTable />, { weight: { value: 62, unit: "kg" } });
  expect(await screen.findByText("62 kg")).toBeVisible();
});
it("does not render a zero for an unrecorded vital", async () => {
  renderWithFixtures(<VitalsTable />, { weight: null });
  expect(screen.queryByText("0 kg")).toBeNull();
  expect(screen.getByText(/—|ยังไม่บันทึก|not recorded/i)).toBeVisible();
});
it("flags an allergy state of unknown distinctly from none", async () => {
  renderWithFixtures(<OverviewSection />, { allergies: "unknown" });
  expect(await screen.findByText(/ไม่ทราบ|unknown/i)).toBeVisible();
  expect(screen.queryByText(/ไม่มีประวัติแพ้|none reported/i)).toBeNull();
});
```

- [ ] **Step 2: Run to verify failure**

Run: `pnpm --filter @clinic/app test src/features/clinical/history-exam.test.tsx`
Expected: FAIL.

- [ ] **Step 3: Implement sections**

History groups: chief complaint (current visit), HPI, past/surgical/family, allergies, medications; prior items badged "from prior record". Exam: structured findings rendered as definition lists; no free-text area.

- [ ] **Step 4: Run to verify pass**

Run: `pnpm --filter @clinic/app test src/features/clinical`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/src/features/clinical
git commit -m "feat: add read-only overview history and exam sections"
```

---

### Task 4: Note, diagnoses, medications and review read

**Files:**
- Create: `app/src/features/clinical/NoteSection.tsx`, `DraftStatusBar.tsx`
- Create: `app/src/features/clinical/DiagnosesSection.tsx`, `MedicationsSection.tsx`, `ReviewSection.tsx`
- Test: `app/src/features/clinical/note.test.tsx`, `orders-read.test.tsx`

**Interfaces:**
- Produces: `NoteSection({encounterId})` rendering read-only note + status; `DraftStatusBar({state: "none"|"draft"|"signed"|"amended", savedAt?, savedBy?})`.
- Produces: `DiagnosesSection({encounterId})` — coded diagnosis rows or an explicit "not coded" state; `MedicationsSection({encounterId})` — ordered lines with state `ordered` only, never `dispensed`; `ReviewSection({encounterId})` — read-only completeness summary listing missing sections, with no submit control in this plan.

- [ ] **Step 1: Write failing tests**

```tsx
it("shows draft vs signed as different states", async () => {
  const { rerender } = renderWithFixtures(<NoteSection />, { state: "draft" });
  expect(await screen.findByText(/ฉบับร่าง|draft/i)).toBeVisible();
  rerender(withFixtures(<NoteSection />, { state: "signed" }));
  expect(await screen.findByText(/ลงนาม|signed/i)).toBeVisible();
  expect(screen.queryByText(/ฉบับร่าง|draft/i)).toBeNull();
});
it("shows per-section saved time only when the server provided one", async () => {
  renderWithFixtures(<DraftStatusBar />, { state: "draft", savedAt: null });
  expect(screen.queryByText(/บันทึกล่าสุด|last saved/i)).toBeNull();
});
it("offers no save control in read-only delivery", async () => {
  renderWithFixtures(<NoteSection />);
  expect(screen.queryByRole("button", { name: /บันทึก|save/i })).toBeNull();
});
it("renders a diagnosis list or an explicit not-coded state", async () => {
  renderWithFixtures(<DiagnosesSection />, { diagnoses: [] });
  expect(await screen.findByText(/ยังไม่ระบุรหัส|not coded/i)).toBeVisible();
});
it("never labels a medication order as dispensed", async () => {
  renderWithFixtures(<MedicationsSection />);
  expect(await screen.findByText(/สั่งยา|ordered/i)).toBeVisible();
  expect(screen.queryByText(/จ่ายยาแล้ว|dispensed/i)).toBeNull();
});
it("lists missing sections without a submit control", async () => {
  renderWithFixtures(<ReviewSection />, { missing: ["exam", "note"] });
  expect(await screen.findByText(/การตรวจร่างกาย|examination/i)).toBeVisible();
  expect(screen.queryByRole("button", { name: /ยืนยัน|submit/i })).toBeNull();
});
```

- [ ] **Step 2: Run to verify failure**

Run: `pnpm --filter @clinic/app test src/features/clinical/note.test.tsx`
Expected: FAIL.

- [ ] **Step 3: Implement note read**

Render sections (assessment, plan, rationale, follow-up) from the read DTO; unknown composition renders a "not composed" notice rather than inventing text.

- [ ] **Step 4: Run to verify pass**

Run: `pnpm --filter @clinic/app test src/features/clinical/note.test.tsx`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/src/features/clinical
git commit -m "feat: add read-only clinical note with draft state display"
```

---

### Task 5: Photo gallery, compare viewer and annotation editor shell (PAT-04/05 read, PAT-05 unsaved state)

**Files:**
- Create: `app/src/features/photos/PhotoGalleryPage.tsx`, `PhotoComparePage.tsx`, `PhotoCard.tsx`, `ConsentBadge.tsx`, `PhotoCompare.tsx`
- Create: `app/src/features/photos/AnnotationEditor.tsx`, `AnnotationToolbar.tsx`, `AnnotationShapeList.tsx`, `useAnnotationDraft.ts`, `UnsavedAnnotationBanner.tsx`
- Create: `app/src/domain/annotation.ts`
- Create: `scripts/verify-photo-deps.mjs`, `docs/decisions/photo-libs.md`
- Test: `app/src/features/photos/photos.test.tsx`, `annotation-draft.test.tsx`, `scripts/verify-photo-deps.test.mjs`

**Interfaces:**
- Produces: `PhotoGalleryPage()` at `/clinic/patients/:id/photos`; `PhotoComparePage()` at `.../compare`; `Photo = {id, category: "before"|"after"|"profile"|"clinical", bodyArea, takenAt, consentStatus: "recorded"|"missing"|"unknown", url, version: number}` where `url` is a proxy path, never upstream.
- Produces the **canonical, library-independent** annotation document (used by Plan 06 `WA-PHO-04`):

```ts
// app/src/domain/annotation.ts
export type ShapeKind = "arrow" | "ellipse" | "rectangle" | "text" | "freehand";
export type AnnotationShapeV1 = {
  id: string; kind: ShapeKind;
  points: Array<[number, number]>;      // normalised 0..1 against the original image
  text?: string;
  style: { stroke: string; width: number; fill?: string };
};
export type AnnotationDocV1 = {
  schemaVersion: 1; photoId: string; originalSha256: string;
  image: { width: number; height: number };
  shapes: AnnotationShapeV1[];
};
export const AnnotationDocV1Schema: ZodType<AnnotationDocV1>;
export function toNormalised(points: Array<[number, number]>, image: {width:number;height:number}): Array<[number, number]>;
export function fromNormalised(points: Array<[number, number]>, image: {width:number;height:number}): Array<[number, number]>;
```

  No Konva node attributes, no canvas transform, no library-owned `id` semantics inside this type. Rendering libraries are adapters only.
- Produces: `useAnnotationDraft(photoId: string): {doc: AnnotationDocV1 | null, shapes: AnnotationShapeV1[], dirty: boolean, add(shape): void, update(id, patch): void, remove(id): void, undo(): void, redo(): void, reset(): void}`.
- Produces: `AnnotationEditor({photo, mode: "preview" | "persisted"})`. `preview` is the only mode in this plan: drawing is local-only, never uploaded, and Save annotation is disabled with a visible reason. `persisted` is wired in Plan 06 `WA-PHO-04`.
- Produces: `AnnotationShapeList({shapes, onSelect, onNudge, onRemove})` — the accessible DOM alternative. Every shape is reachable and editable without the canvas: select, arrow-key nudge, text edit, delete.
- Produces: `PhotoCompare({before, after})` wrapping `react-compare-slider`, plus a native `<input type="range">` "reveal" control for keyboard and screen-reader users.
- Produces: `UnsavedAnnotationBanner({dirty})`; navigation away with `dirty === true` requires confirmation.
- Read access to the gallery and compare route requires capability `read.photo`; denial hides the tab and shows permission-denied in place.

**Library policy (verify before installing; do not guess versions):**
- Annotation rendering: `react-konva` + `konva`. Comparison: `react-compare-slider`.
- The `react-konva` major must match the installed React major (React 18 in this plan). `react-compare-slider` must declare React 18 in `peerDependencies`. Check with `pnpm view <pkg> peerDependencies version` and the packages' own docs before adding them; record the exact resolved versions, doc URLs and date in `docs/decisions/photo-libs.md`.
- If either package cannot satisfy the React major, **do not install it**: implement the same interface with native SVG/canvas and record the fallback in the ADR. The shape JSON and accessibility model are unchanged either way.
- Pin exact versions in `app/package.json` (no `^` on these two) so the ADR, lockfile and code agree.

- [ ] **Step 1: Verify library compatibility and record the decision**

Run (network read only; the project itself is not modified):
```bash
pnpm view react-konva peerDependencies version
pnpm view react-compare-slider peerDependencies version
```
Expected: React major 18 is inside `react-konva`'s peer range and `react-compare-slider` lists React 18. Write `docs/decisions/photo-libs.md` with the resolved versions, the two documentation URLs, the check date, and either `decision: adopt` or `decision: svg-fallback`. If the check fails, stop and use the fallback.

- [ ] **Step 2: Write failing tests**

```tsx
it("validates a canonical annotation document with no library fields", () => {
  const doc = { schemaVersion: 1, photoId: "PH-1", originalSha256: "a".repeat(64),
    image: { width: 1000, height: 800 }, shapes: [arrow({ points: [[0.1, 0.1], [0.4, 0.4]] })] };
  expect(AnnotationDocV1Schema.parse(doc)).toEqual(doc);
  expect(JSON.stringify(doc)).not.toMatch(/konva|attrs|transform/i);
});
it("keeps normalised coordinates stable across image sizes", () => {
  const n = toNormalised([[100, 80]], { width: 1000, height: 800 });
  expect(fromNormalised(n, { width: 500, height: 400 })).toEqual([[50, 40]]);
});
it("adds and removes a shape from the DOM list without a pointer", async () => {
  renderWithFixtures(<AnnotationEditor photo={photo} mode="preview" />);
  await user.click(screen.getByRole("button", { name: /เพิ่มรูปทรง|add shape/i }));
  expect(await screen.findByRole("row", { name: /arrow|ลูกศร/i })).toBeVisible();
  await user.click(screen.getByRole("button", { name: /ลบ|delete/i }));
  expect(screen.queryByRole("row", { name: /arrow|ลูกศร/i })).toBeNull();
});
it("nudges a selected shape with the keyboard", async () => {
  renderWithFixtures(<AnnotationEditor photo={photo} mode="preview" />);
  addArrow();
  await user.click(screen.getByRole("row", { name: /arrow|ลูกศร/i }));
  await user.keyboard("{ArrowRight}");
  expect(screen.getByTestId("annotation-canvas")).toHaveAttribute("data-first-x", "0.12");
});
it("filters before/after by body area", async () => {
  renderWithFixtures(<PhotoComparePage />);
  await user.selectOptions(screen.getByLabelText(/บริเวณ|body area/i), "face");
  expect(await screen.findByTestId("compare-pair")).toBeVisible();
});
it("provides a keyboard reveal control for comparison", () => {
  renderWithFixtures(<PhotoCompare />);
  expect(screen.getByRole("slider", { name: /reveal|เลื่อนเปรียบเทียบ/i })).toBeVisible();
});
it("shows a consent-missing state and no capture action", async () => {
  renderWithFixtures(<PhotoGalleryPage />, { consent: "missing" });
  expect(await screen.findByText(/ไม่มีความยินยอม|consent missing/i)).toBeVisible();
  expect(screen.queryByRole("button", { name: /ถ่ายภาพ|capture/i })).toBeNull();
});
it("hides the gallery when read.photo is denied", () => {
  renderWithFixtures(<PhotoGalleryPage />, { caps: { "read.photo": "denied" } });
  expect(screen.getByText(/ไม่มีสิทธิ์|permission denied/i)).toBeVisible();
});
it("renders images from the proxy path only", async () => {
  renderWithFixtures(<PhotoGalleryPage />, { photo: { url: "/api/files/abc" } });
  const img = await screen.findByRole("img", { name: /ก่อนทำ|before/i });
  expect(img.getAttribute("src")).toMatch(/^\/api\/files\//);
});
it("marks the draft dirty after a local shape and keeps it across a locale switch", async () => {
  renderWithFixtures(<AnnotationEditor photo={photo} mode="preview" />);
  addArrow();
  expect(await screen.findByText(/ยังไม่บันทึก|unsaved/i)).toBeVisible();
  await user.click(screen.getByRole("button", { name: "EN" }));
  expect(screen.getByTestId("annotation-canvas")).toHaveAttribute("data-shape-count", "1");
  expect(screen.getByText(/unsaved/i)).toBeVisible();
});
it("warns before leaving a dirty annotation draft", async () => {
  renderWithFixtures(<AnnotationEditor photo={photo} mode="preview" />);
  addShape();
  const event = new Event("beforeunload", { cancelable: true });
  window.dispatchEvent(event);
  expect(event.defaultPrevented).toBe(true);
});
it("disables save and explains that persistence is not enabled", async () => {
  renderWithFixtures(<AnnotationEditor photo={photo} mode="preview" />);
  const save = await screen.findByRole("button", { name: /บันทึกภาพวาด|save annotation/i });
  expect(save).toBeDisabled();
  expect(screen.getByText(/ยังไม่เปิดใช้งาน|not enabled/i)).toBeVisible();
});
it("never mutates the original photo reference while previewing", async () => {
  renderWithFixtures(<AnnotationEditor photo={photo} mode="preview" />);
  addShape();
  expect(screen.getByRole("img")).toHaveAttribute("src", "/api/files/abc");
});
```

Dependency guard test (in `scripts/verify-photo-deps.test.mjs`, not React). Version strings below are synthetic test inputs, not recommendations:

```js
test("fails when react-konva major does not match React major", () => { expect(run({ react: "18.3.1", konva: "19.0.0" })).toBe(1); });
test("fails when the ADR is missing for an installed photo lib", () => { expect(run({ installed: ["react-compare-slider"], adr: null })).toBe(1); });
test("fails when versions are ranges instead of exact pins", () => { expect(run({ reactKonvaRange: "^18.2.0" })).toBe(1); });
test("passes for the recorded adopt decision with exact pins", () => { expect(run({ react: "18.3.1", konva: "18.2.0", adr: "adopt", exact: true })).toBe(0); });
```

- [ ] **Step 3: Run to verify failure**

Run: `pnpm --filter @clinic/app test src/features/photos && node --test scripts/verify-photo-deps.test.mjs`
Expected: FAIL — modules, ADR and dependency script missing.

- [ ] **Step 4: Implement schema, adapters and accessible editor**

Install only the packages the Step 1 check approved, at the exact versions recorded in the ADR. `AnnotationEditor` renders `react-konva` (or the SVG fallback) and always renders `AnnotationShapeList`; the canvas is `aria-hidden` because the list is the accessible editing surface. Comparison uses `react-compare-slider` plus the native range control. The editor stores only `AnnotationDocV1` in state; it never calls the API and never re-encodes the original. Toolbar targets are 44×44 px and collapse to a bottom bar at ≤767 px. `data-shape-count` and `data-first-x` expose state for tests and status text.

- [ ] **Step 5: Run to verify pass**

Run: `pnpm --filter @clinic/app test src/features/photos && node --test scripts/verify-photo-deps.test.mjs`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add app/src/features/photos app/src/domain/annotation.ts app/package.json docs/decisions/photo-libs.md scripts/verify-photo-deps.mjs scripts/verify-photo-deps.test.mjs
git commit -m "feat: add photo read, accessible annotation draft and compare"
```

---

### Task 6: Lab worklist and detail read (DOC-04)

**Files:**
- Create: `app/src/features/labs/LabListPage.tsx`, `LabDetailPage.tsx`, `LabStatusChip.tsx`, `ResultFiles.tsx`
- Test: `app/src/features/labs/labs.test.tsx`

**Interfaces:**
- Produces: `LabListPage()` at `/clinic/labs`; `LabDetailPage()` at `/clinic/labs/:id`; `LabState = "ordered"|"sample_collected"|"sent"|"received"|"reviewed"|"cancelled"|"unknown"`; `classifyLabState(raw): LabState`.

- [ ] **Step 1: Write failing tests**

```tsx
it("lists external and native labs as different kinds", async () => {
  renderWithFixtures(<LabListPage />);
  expect(await screen.findByText(/แล็บภายนอก|external/i)).toBeVisible();
  expect(screen.getByText(/แล็บใน|in-house/i)).toBeVisible();
});
it("flags reviewed-without-reviewer as not verified", async () => {
  renderWithFixtures(<LabDetailPage />, { state: "reviewed", reviewer: null });
  expect(await screen.findByText(/ยังไม่ยืนยันผู้ตรวจ|reviewer not recorded/i)).toBeVisible();
  expect(screen.queryByText(/ตรวจทานแล้ว|reviewed/i)).toBeNull();
});
it("offers no review action", async () => {
  renderWithFixtures(<LabDetailPage />, { state: "received" });
  expect(screen.queryByRole("button", { name: /review|ตรวจทาน/i })).toBeNull();
});
it("shows result files only via private proxy links", async () => {
  renderWithFixtures(<LabDetailPage />, { files: ["a.pdf"] });
  expect(await screen.findByRole("link", { name: /a\.pdf/ })).toHaveAttribute("href", /^\/api\/files\//);
});
```

- [ ] **Step 2: Run to verify failure**

Run: `pnpm --filter @clinic/app test src/features/labs`
Expected: FAIL.

- [ ] **Step 3: Implement lab read**

Status filters map to display states; unknown state renders raw + note. No `Critical` badge unless a clinician-entered flag exists in the DTO.

- [ ] **Step 4: Run to verify pass**

Run: `pnpm --filter @clinic/app test src/features/labs`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/src/features/labs
git commit -m "feat: add read-only lab worklist and detail"
```

---

### Task 7: Doctor My Day (DOC-01)

**Files:**
- Create: `app/src/features/clinical/MyDayPage.tsx`, `NextPatientCard.tsx`, `WorkCounts.tsx`
- Test: `app/src/features/clinical/myday.test.tsx`

**Interfaces:**
- Produces: `MyDayPage()` at `/clinic/doctor`; `NextPatientCard({appointment})`; counts `{myAppointmentsToday, openDrafts, labsAwaitingReview, followUps}`.

- [ ] **Step 1: Write failing tests**

```tsx
it("shows one next-patient card with open visit and continue draft", async () => {
  renderWithFixtures(<MyDayPage />, { draft: true });
  expect(await screen.findByTestId("next-patient")).toBeVisible();
  expect(screen.getByRole("link", { name: /ดำเนินการต่อ|continue draft/i })).toHaveAttribute("href", /\/clinic\/visits\//);
});
it("shows counts from the server aggregate, not a client filter", async () => {
  renderWithFixtures(<MyDayPage />, { counts: { openDrafts: 3 } });
  expect(await screen.findByTestId("count-openDrafts")).toHaveTextContent("3");
});
it("shows an error state when counts fail", async () => {
  server.use(http.get("/api/read/my-day-counts", () => HttpResponse.error()));
  renderWithFixtures(<MyDayPage />);
  expect(await screen.findByRole("alert")).toBeVisible();
});
```

- [ ] **Step 2: Run to verify failure**

Run: `pnpm --filter @clinic/app test src/features/clinical/myday.test.tsx`
Expected: FAIL.

- [ ] **Step 3: Implement My Day**

Next patient only from authorised appointments; "Start consultation" is a link to an existing encounter, never creates one. Availability shown read-only.

- [ ] **Step 4: Run to verify pass**

Run: `pnpm --filter @clinic/app test src/features/clinical`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/src/features/clinical
git commit -m "feat: add doctor my day with next patient handoff"
```

---

### Task 8: Nurse home and tasks read (NUR-01/02)

**Files:**
- Create: `app/src/features/nursing/NurseHomePage.tsx`, `NurseTasksPage.tsx`, `AssignedPatientList.tsx`
- Test: `app/src/features/nursing/nursing.test.tsx`

**Interfaces:**
- Produces: `NurseHomePage()` at `/clinic/nurse`; `NurseTasksPage()` at `/clinic/nurse/tasks`; task kinds `vitals | prep | aftercare | lab_dispatch`.

- [ ] **Step 1: Write failing tests**

```tsx
it("shows assigned patients without clinical note content", async () => {
  renderWithFixtures(<NurseHomePage />);
  expect(await screen.findByText(/สมชาย/)).toBeVisible();
  expect(screen.queryByText(/assessment|ประเมิน/)).toBeNull();
});
it("renders vitals and prep tabs but no ward or ICU module", async () => {
  renderWithFixtures(<NurseTasksPage />);
  expect(await screen.findByRole("tab", { name: /สัญญาณชีพ|vitals/i })).toBeVisible();
  expect(screen.queryByText(/เตียง|bed|icu/i)).toBeNull();
});
it("disables vitals save", async () => {
  renderWithFixtures(<NurseTasksPage />);
  expect(await screen.findByRole("button", { name: /บันทึก|save/i })).toBeDisabled();
});
```

- [ ] **Step 2: Run to verify failure**

Run: `pnpm --filter @clinic/app test src/features/nursing`
Expected: FAIL.

- [ ] **Step 3: Implement nursing read**

Task list from assigned encounters; urgency label only where the source provides one. No auto-alerts.

- [ ] **Step 4: Run to verify pass**

Run: `pnpm --filter @clinic/app test src/features/nursing`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/src/features/nursing
git commit -m "feat: add read-only nurse worklist and tasks"
```

---

### Task 9: Procedure detail read (DOC-03)

**Files:**
- Create: `app/src/features/procedures/ProcedurePage.tsx`, `ConsumablePreview.tsx`
- Test: `app/src/features/procedures/procedure.test.tsx`

**Interfaces:**
- Produces: `ProcedurePage()` at `/clinic/procedures/:id`; `ConsumablePreview({lines})` showing item, qty, uom, batch, expiry, warehouse.

- [ ] **Step 1: Write failing tests**

```tsx
it("shows batch and expiry for a consumable line", async () => {
  renderWithFixtures(<ProcedurePage />);
  expect(await screen.findByText(/LOT-1/)).toBeVisible();
  expect(screen.getByText(/2027-01-31|31\/01\/2027/)).toBeVisible();
});
it("disables complete treatment with a reason", async () => {
  renderWithFixtures(<ProcedurePage />);
  expect(await screen.findByRole("button", { name: /เสร็จสิ้น|complete/i })).toBeDisabled();
  expect(screen.getByText(/รอการตรวจสอบ|awaiting backend verification/i)).toBeVisible();
});
it("warns on expired stock but still disables completion", async () => {
  renderWithFixtures(<ProcedurePage />, { expired: true });
  expect(await screen.findByText(/หมดอายุ|expired/i)).toBeVisible();
});
```

- [ ] **Step 2: Run to verify failure**

Run: `pnpm --filter @clinic/app test src/features/procedures`
Expected: FAIL.

- [ ] **Step 3: Implement procedure read**

No stock arithmetic in the client; quantities come from the DTO. Completion gated by `write.procedure.complete`.

- [ ] **Step 4: Run to verify pass**

Run: `pnpm --filter @clinic/app test src/features/procedures`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/src/features/procedures
git commit -m "feat: add read-only procedure detail with consumable preview"
```

---

### Task 10: Clinical read e2e and cross-section context checks

**Files:**
- Create: `app/e2e/clinical.spec.ts`, `app/e2e/labs.spec.ts`

**Interfaces:**
- Consumes Plan 02 harness (`loginAs`, fixtures, MSW handlers); produces D2–D4 evidence. `loginAs(page, role)` runs through the Vite proxy with matching `APP_ORIGIN`; no cookie injection.

- [ ] **Step 1: Write failing e2e tests**

```ts
test("workspace keeps patient and encounter across all sections at 1440", async ({ page }) => {
  await loginAs(page, "DOC"); await page.goto("/clinic/visits/ENC-1");
  for (const name of ["History", "Exam", "Note", "Photos", "Lab", "Review"]) {
    await page.getByRole("tab", { name }).click();
    await expect(page.getByTestId("encounter-id")).toHaveText("ENC-1");
  }
});
test("mobile workspace uses accordions and no horizontal scroll", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await loginAs(page, "DOC"); await page.goto("/clinic/visits/ENC-1");
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
});
test("lab detail offers no review action", async ({ page }) => {
  await loginAs(page, "DOC"); await page.goto("/clinic/labs/LAB-1");
  await expect(page.getByRole("button", { name: /review/i })).toHaveCount(0);
});
test("annotation toolbar is touch-usable at 390", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await loginAs(page, "DOC"); await page.goto("/clinic/patients/PAT-1/photos/compare");
  const tool = page.getByRole("button", { name: /ลูกศร|arrow/i });
  const box = await tool.boundingBox();
  expect(box!.height).toBeGreaterThanOrEqual(44);
  expect(box!.width).toBeGreaterThanOrEqual(44);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
});
test("dirty annotation survives a locale switch at 390", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await loginAs(page, "DOC"); await page.goto("/clinic/patients/PAT-1/photos/compare");
  await page.getByTestId("annotation-canvas").click({ position: { x: 20, y: 20 } });
  await page.getByRole("button", { name: "EN" }).click();
  await expect(page.getByText(/unsaved/i)).toBeVisible();
});
```

- [ ] **Step 2: Run to verify failure**

Run: `pnpm --filter @clinic/app e2e e2e/clinical.spec.ts`
Expected: FAIL.

- [ ] **Step 3: Fix selectors/layout only**

- [ ] **Step 4: Run to verify pass**

Run: `pnpm --filter @clinic/app e2e`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/e2e
git commit -m "test: add clinical read e2e and context checks"
```

---

## Self-Review

| Spec area | Covered by |
|---|---|
| DOC-02 one workspace, nine sections | Tasks 1–4 |
| Diagnoses, medications and review read | Task 4 |
| History read-only, no duplicate editor | Task 3 |
| Vitals units/date, missing ≠ zero | Task 3 |
| PAT-04/05 gallery + compare read | Task 5 |
| PAT-05 unsaved state, 390 touch editor (preview) | Tasks 5, 10 |
| Photo libs verified against React major + documented | Task 5 Step 1, dependency guard test |
| Canonical versioned shape JSON, renderer-independent | Task 5 |
| Accessible DOM editing alternative + keyboard compare | Task 5 |
| DOC-04 lab states, external vs native, reviewer rule | Task 6 |
| DOC-01 My Day | Task 7 |
| NUR-01/02, no ward/ICU | Task 8 |
| DOC-03 consumables preview | Task 9 |
| AT-05/08 read side, AT-09 read side, V3-AT-04 | Tasks 3–9 |
| §5 responsive | Tasks 2, 10 |

**Deferred:** consent capture, private upload, encounter linking and annotation persistence (Plan 06 Tasks 14–17); draft save, medication ordering, completion, lab transitions (Plan 06). Upload/review remain `EXT`/blocked.

**Library note:** `react-konva`/`konva` and `react-compare-slider` are adapters only. The persisted document is `AnnotationDocV1`; no renderer attribute may enter it. Exact versions are resolved at implementation time against React 18 and recorded in `docs/decisions/photo-libs.md`; the SVG fallback is the sanctioned alternative if compatibility fails.

**Known gap:** section DTO composition is fixture-only; the workspace must not claim a composed signed note until the note read contract is recorded.
