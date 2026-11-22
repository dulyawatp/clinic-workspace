import { createHash } from "node:crypto";
import { expect, test, type Response } from "@playwright/test";

const origin = "http://127.0.0.1:18081";
const rpc = "/api/method/";
const annotations = `${rpc}clinic_photos.api.visit_annotations.`;
const photos = `${rpc}clinic_photos.api.visit_photos.`;

type Slot = "Before" | "After";

test("armed native physician mouse and touch annotation roundtrip", async ({ page }) => {
  test.skip(
    process.env.CLINIC_SIT_VERIFIED_SITE !== "marley-sit.local" ||
    process.env.CLINIC_SIT_ANNOTATION_WRITE !== "fresh-synthetic-linked-draft" ||
    !process.env.CLINIC_SIT_APPOINTMENT || !process.env.CLINIC_SIT_ENCOUNTER,
    "Requires verified site, explicitly armed fresh synthetic linked draft and both identifiers",
  );
  const appointment = process.env.CLINIC_SIT_APPOINTMENT!;
  const encounter = process.env.CLINIC_SIT_ENCOUNTER!;
  const user = process.env.CLINIC_SIT_PHYSICIAN_USER;
  const password = process.env.CLINIC_SIT_PHYSICIAN_PASSWORD;
  if (user !== "sit.doc@example.invalid" || !password) throw new Error("Synthetic physician credentials required");

  const keys: Partial<Record<Slot, string>> = {};
  const checks: Record<Slot, number> = { Before: 0, After: 0 };
  let armed: Slot | null = null;
  let blocked = false;
  await page.route("**/*", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const method = request.method();
    if (url.origin !== origin) {
      blocked = true;
      return route.abort();
    }
    if (["GET", "HEAD", "OPTIONS"].includes(method)) {
      const readMethods = new Set([
        `${rpc}clinic_ui.api.session.bootstrap`,
        `${rpc}clinic_ui.api.appointments.view`,
        `${rpc}clinic_ui.api.encounters.open_for_appointment`,
        `${rpc}clinic_ui.api.encounters.view`,
      ]);
      if (!url.pathname.startsWith("/api/") || readMethods.has(url.pathname)) return route.continue();
      blocked = true;
      return route.abort();
    }
    if (method !== "POST") {
      blocked = true;
      return route.abort();
    }
    if (url.pathname === `${rpc}login`) return route.continue();
    let payload: Record<string, unknown> = {};
    try { payload = request.postDataJSON().payload ?? {}; } catch { /* fail closed */ }
    const target = payload.appointment === appointment;
    const slot = payload.slot === "Before" || payload.slot === "After" ? payload.slot : null;
    if ((url.pathname === `${photos}read_pair` || url.pathname === `${rpc}clinic_photos.api.visit_options.capture_options`) && target)
      return route.continue();
    if ((url.pathname === `${photos}read_image` || url.pathname === `${annotations}read`) && target && slot)
      return route.continue();
    if (url.pathname === `${annotations}status` && target && slot && keys[slot] &&
        payload.request_key === keys[slot] && checks[slot]++ < 3) return route.continue();
    if (url.pathname === `${annotations}submit` && target && slot && armed === slot && !keys[slot] &&
        payload.encounter === encounter && payload.expected_version === 0 &&
        typeof payload.request_key === "string" && /^[A-Za-z0-9_-]{16,128}$/.test(payload.request_key)) {
      keys[slot] = payload.request_key;
      armed = null;
      return route.continue();
    }
    blocked = true;
    return route.abort();
  });

  const responseFor = (suffix: string, slot?: Slot, key?: string) => page.waitForResponse((response: Response) => {
    if (new URL(response.url()).pathname !== suffix || response.request().method() !== "POST") return false;
    if (!slot) return true;
    try {
      const payload = response.request().postDataJSON().payload;
      return payload?.appointment === appointment && payload?.slot === slot &&
        (key === undefined || payload?.request_key === key);
    } catch {
      return false;
    }
  });
  const digest = async (response: Response) => createHash("sha256").update(await response.body()).digest("hex");
  const message = async (response: Response) => (await response.json()).message;
  const ensure = (condition: unknown) => { if (!condition) throw new Error("SIT roundtrip condition failed"); };

  // Never expose credentials, identifiers, response payloads, or Playwright action logs.
  let step = "login";
  try {
    await page.goto("/clinic/login");
    await page.locator('input[name="usr"]').fill(user);
    await page.locator('input[name="pwd"]').fill(password);
    await page.locator('button[type="submit"]').click();
    await page.locator("header.shell-topbar").waitFor({ state: "visible" });
    const live = await page.evaluate(async () => {
      const response = await fetch("/api/method/clinic_ui.api.session.bootstrap");
      if (!response.ok) return false;
      const data = await response.json();
      return data.message?.mode === "live" && data.message?.user?.roles?.includes("DOC") === true;
    });
    ensure(live);
    const route = `/clinic/visits/${encodeURIComponent(encounter)}?appointment_ref=${encodeURIComponent(appointment)}`;
    step = "open linked draft";
    await page.goto(route);
    const pairResponse = responseFor(`${photos}read_pair`);
    await page.locator(".visit-photo-disclosure summary").click();
    const pair = await message(await pairResponse);
    ensure(pair.appointment === appointment && pair.before?.photo && pair.after?.photo);

    const open = async (slot: Slot) => {
      const image = responseFor(`${photos}read_image`, slot);
      const drawing = responseFor(`${annotations}read`, slot);
      await page.getByRole("button", { name: slot === "Before"
        ? /^(Annotate Before photo|วาดกำกับภาพก่อน)$/
        : /^(Annotate After photo|วาดกำกับภาพหลัง)$/ }).click();
      return { image: await image, drawing: await drawing };
    };
    const close = () => page.getByRole("button", { name: /^(Close drawing|ปิดหน้าวาดกำกับ)$/ }).click();
    const hashes: Partial<Record<Slot, string>> = {};
    step = "verify fresh slots";
    // Check both fresh slots before making either irreversible write.
    for (const slot of ["Before", "After"] as const) {
      const initial = await open(slot);
      const baseline = await message(initial.drawing);
      ensure(initial.image.ok() && baseline.version === 0 && baseline.annotation === null &&
        baseline.can_write === true && baseline.pending_request_key === null);
      hashes[slot] = await digest(initial.image);
      await close();
    }

    const savedDrawings: Partial<Record<Slot, string>> = {};
    for (const slot of ["Before", "After"] as const) {
      step = `${slot} drawing`;
      const initial = await open(slot);
      const baseline = await message(initial.drawing);
      ensure(initial.image.ok() && await digest(initial.image) === hashes[slot] &&
        baseline.version === 0 && baseline.annotation === null && baseline.can_write === true &&
        baseline.pending_request_key === null);
      const surface = page.locator(".annotation-stage__surface");
      await page.getByRole("button", { name: /^(Freehand|วาดอิสระ)$/ }).click();
      await surface.scrollIntoViewIfNeeded();
      await surface.evaluate((element) => element.scrollIntoView({ block: "center", inline: "center" }));
      const box = await surface.boundingBox();
      ensure(box && box.width > 40 && box.height > 40);
      const start = { x: box!.x + box!.width * 0.3, y: box!.y + box!.height * 0.3 };
      const end = { x: box!.x + box!.width * 0.6, y: box!.y + box!.height * 0.6 };
      ensure(await surface.evaluate((element, point) => {
        const hit = document.elementFromPoint(point.x, point.y);
        return hit !== null && (hit === element || element.contains(hit));
      }, start));
      if (slot === "Before") {
        await page.mouse.move(start.x, start.y);
        await page.mouse.down();
        await page.mouse.move(end.x, end.y, { steps: 8 });
        await page.mouse.up();
      } else {
        const cdp = await page.context().newCDPSession(page);
        await cdp.send("Emulation.setTouchEmulationEnabled", { enabled: true, maxTouchPoints: 1 });
        try {
          await cdp.send("Input.dispatchTouchEvent", { type: "touchStart", touchPoints: [start] });
          for (let i = 1; i <= 8; i++) {
            await cdp.send("Input.dispatchTouchEvent", { type: "touchMove", touchPoints: [{
              x: start.x + (end.x - start.x) * i / 8, y: start.y + (end.y - start.y) * i / 8,
            }] });
          }
          await cdp.send("Input.dispatchTouchEvent", { type: "touchEnd", touchPoints: [] });
        } finally {
          await cdp.send("Emulation.setTouchEmulationEnabled", { enabled: false });
          await cdp.detach();
        }
      }
      if (slot === "Before") {
        const undo = page.getByRole("button", { name: /^(Undo|ย้อนกลับ)$/ });
        const redo = page.getByRole("button", { name: /^(Redo|ทำซ้ำ)$/ });
        await expect(undo).toBeEnabled();
        await undo.click();
        await expect(redo).toBeEnabled();
        await redo.click();
        await expect(undo).toBeEnabled();
      }
      step = `${slot} submit`;
      armed = slot;
      const submit = responseFor(`${annotations}submit`);
      await page.getByRole("button", { name: /^(Save annotation version|บันทึกเวอร์ชันคำอธิบาย)$/ }).click();
      await submit;
      ensure(keys[slot] && !blocked);
      step = `${slot} status`;
      let committed = false;
      for (let i = 0; i < 3; i++) {
        const status = responseFor(`${annotations}status`, slot, keys[slot]);
        await page.getByRole("button", { name: /^(Check save|ตรวจสอบการบันทึก)$/ }).click();
        const result = await message(await status);
        if (result.state === "committed") {
          ensure(result.request_key === keys[slot]);
          committed = true;
          break;
        }
        ensure(result.state === "pending");
        if (i < 2) await page.waitForTimeout(400 * (i + 1));
      }
      ensure(committed);
      await page.getByText(/^(Layer saved separately; the original is unchanged\.|บันทึกเลเยอร์แยก ต้นฉบับไม่เปลี่ยน)$/).waitFor();
      await close();
      const verified = await open(slot);
      const saved = await message(verified.drawing);
      ensure(saved.version === 1 && saved.annotation?.shapes?.length > 0 &&
        await digest(verified.image) === hashes[slot]);
      savedDrawings[slot] = JSON.stringify(saved.annotation);
      await close();
    }
    step = "reload and verify";
    await page.reload();
    const pairAgainResponse = responseFor(`${photos}read_pair`);
    await page.locator(".visit-photo-disclosure summary").click();
    const pairAgain = await message(await pairAgainResponse);
    ensure(pairAgain.before?.photo === pair.before.photo && pairAgain.after?.photo === pair.after.photo);
    for (const slot of ["Before", "After"] as const) {
      const reopened = await open(slot);
      const saved = await message(reopened.drawing);
      ensure(reopened.image.ok() && await digest(reopened.image) === hashes[slot] &&
        saved.version === 1 && JSON.stringify(saved.annotation) === savedDrawings[slot] && !blocked);
      await expect(page.locator(".annotation-stage__surface")).toBeVisible();
      await close();
    }
  } catch {
    throw new Error(`SIT annotation roundtrip failed at ${step}; do not repeat a save without native same-key reconciliation`);
  }
});
