import { readFile } from "node:fs/promises";
import { createServer, type RequestListener } from "node:http";
import { expect, test } from "@playwright/test";

const ref = "wsa1_0123456789abcdef0123456789abcdef_0123456789abcdef_abcdefghijklmnop";
const visit = `/clinic/visits/SYNTHETIC-ENC?appointment_ref=${ref}`;
const modified = "2026-11-20 09:00:00.000001";
const pdfName = "Synthetic blood count.pdf";
const pngName = "Synthetic scan.png";
const deniedName = "Synthetic denied.pdf";
const mismatchName = "Synthetic mismatch.pdf";
const names = [pdfName, pngName, deniedName, mismatchName];
const reports = names.map((name, i) => ({
  name, id: `SYNTHETIC-FILE-${i}`, sizeBytes: 1200, uploadedBy: "synthetic-user", uploadedAt: modified,
  downloadUrl: `/private/files/${encodeURIComponent(name)}?fid=SYNTHETIC-FILE-${i}`,
}));

function syntheticPdf() {
  const stream = "0.13 0.55 0.49 rg 54 630 504 96 re f\n1 1 1 rg BT /F1 30 Tf 76 668 Td (SYNTHETIC LAB REPORT) Tj ET\n"
    + "0.04 0.17 0.24 rg BT /F1 18 Tf 54 594 Td (Validation sample  -  not a patient record.) Tj ET\n";
  const objects = [
    "<< /Type /Catalog /Pages 2 0 R >>",
    "<< /Type /Pages /Count 1 /Kids [3 0 R] >>",
    "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
    `<< /Length ${stream.length} >>\nstream\n${stream}endstream`,
    "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
  ];
  let source = "%PDF-1.4\n";
  const offsets = objects.map((object, i) => {
    const offset = source.length;
    source += `${i + 1} 0 obj\n${object}\nendobj\n`;
    return offset;
  });
  return Buffer.from(`${source}xref\n0 6\n0000000000 65535 f \n${offsets.map((offset) =>
    `${String(offset).padStart(10, "0")} 00000 n \n`).join("")}trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n${source.length}\n%%EOF\n`);
}

// Chromium issues native link downloads outside page.route. Give the app
// and stored-byte fixtures one real loopback origin without a Frappe server.
async function fixtureOrigin(vite: string | undefined, serveFile: RequestListener) {
  if (!vite || !/^https?:\/\/(?:localhost|127\.0\.0\.1):\d+$/.test(vite)) throw new Error("Expected loopback Vite server");
  const server = createServer((request, response) => {
    if (request.url?.startsWith("/private/files/")) {
      serveFile(request, response);
      return;
    }
    if (request.method !== "GET" || !request.url?.startsWith("/") || request.url.startsWith("//") ||
        request.url.includes("\\") || request.url.startsWith("/api/")) {
      response.writeHead(404).end();
      return;
    }
    void fetch(`${vite}${request.url}`, { headers: { accept: request.headers.accept ?? "*/*" }, redirect: "error" }).then(async (result) => {
      const body = Buffer.from(await result.arrayBuffer());
      response.writeHead(result.status, { "Content-Type": result.headers.get("Content-Type") ?? "text/plain" }).end(body);
    }).catch(() => response.writeHead(502).end());
  });
  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
  const address = server.address();
  if (!address || typeof address === "string") throw new Error("Missing loopback server address");
  return {
    url: `http://127.0.0.1:${address.port}`,
    close: () => { server.close(); server.closeAllConnections(); },
  };
}

test.skip(process.env.VITE_CLINIC_NATIVE !== "1", "Requires native transport in the Vite server.");
// Chromium's older headless shell has no native PDF viewer.
test.use({ channel: "chromium", viewport: { width: 1280, height: 900 } });

test("previews synthetic private files, preserves encounter draft, and downloads stored bytes", async ({ page, baseURL }, info) => {
  const pdf = syntheticPdf();
  const reads: string[] = [];
  const writes: string[] = [];
  let png: Buffer;
  const fixture = await fixtureOrigin(baseURL, (request, response) => {
    const exact = request.url ?? "";
    reads.push(exact);
    const headers = { "Cache-Control": "no-store", "Content-Disposition": "attachment" };
    switch (reports.find(({ downloadUrl }) => downloadUrl === exact)?.name) {
      case pdfName: response.writeHead(200, { ...headers, "Content-Type": "application/pdf" }).end(pdf); break;
      case pngName: response.writeHead(200, { ...headers, "Content-Type": "image/png" }).end(png); break;
      case deniedName: response.writeHead(403).end(); break;
      case mismatchName: response.writeHead(200, { "Content-Type": "application/pdf" }).end("<html>Not a PDF</html>"); break;
      default: response.writeHead(404).end();
    }
  });
  try {
    await page.route((url) => /https?:/.test(url.protocol) && url.origin !== fixture.url, (route) => route.abort());
    await page.route("**/api/**", (route) => {
      if (route.request().method() !== "GET") writes.push(route.request().url());
      return route.fulfill({ status: 404 });
    });
    await page.route("**/api/method/clinic_ui.api.session.bootstrap", (route) => route.fulfill({
      json: { message: { mode: "live", sessionEpoch: "synthetic-epoch", user: { id: "u", displayName: "Synthetic Doctor", roles: ["DOC"] },
        capabilities: { "read.patient": "enabled", "read.appointment": "read_only" }, csrfToken: "synthetic-token" } },
    }));
    await page.route("**/api/method/clinic_ui.api.appointments.view?*", (route) => route.fulfill({
      json: { message: { workspace_patient_id: "SYNTHETIC-PAT", appointment_id: ref, native_appointment_id: "SYNTHETIC-APT",
        appointment_date: "2026-11-20", appointment_time: "09:30:00", status: "Open", modified,
        can_reschedule: false, can_checkin: false, is_fixture: false } },
    }));
    await page.route("**/api/method/clinic_ui.api.encounters.open_for_appointment?*", (route) => route.fulfill({
      json: { message: { context: { appointment: "SYNTHETIC-APT", patient: "SYNTHETIC-PAT", practitioner: "SYNTHETIC-DOC", status: "Closed" },
        encounter: { id: "SYNTHETIC-ENC", modified, docstatus: 0, symptoms: [], diagnoses: [] },
        notes: { state: "available", items: [], next_offset: null }, medications: { state: "available", complete: true, items: [] } } },
    }));
    await page.route("**/api/method/clinic_ui.api.lab_reports.list_for_encounter?*", (route) => route.fulfill({
      json: { message: { items: reports, nextOffset: null } },
    }));
    await page.addInitScript(() => localStorage.setItem("clinic.locale", "en"));
    await page.goto(`${fixture.url}${visit}`);
    const draft = page.getByRole("searchbox", { name: "Symptoms" });
    await draft.fill("Synthetic unsaved symptom");
    png = Buffer.from(await page.evaluate(() => {
      const canvas = document.createElement("canvas");
      canvas.width = 600;
      canvas.height = 360;
      const ctx = canvas.getContext("2d")!;
      ctx.fillStyle = "#0f4545";
      ctx.fillRect(0, 0, 600, 360);
      ctx.fillStyle = "#d7eee7";
      ctx.font = "bold 36px sans-serif";
      ctx.fillText("Synthetic lab image", 44, 152);
      ctx.font = "20px sans-serif";
      ctx.fillText("Preview check · no patient data", 44, 196);
      return canvas.toDataURL("image/png").split(",")[1];
    }), "base64");
    const view = (name: string) => page.getByRole("button", { name: `View ${name}`, exact: true });
    await expect(view(pdfName)).toBeVisible();
    expect(reads).toEqual([]);

    await view(pdfName).click();
    const pdfDialog = page.getByRole("dialog", { name: pdfName });
    await expect(pdfDialog.getByRole("button", { name: "Close" })).toBeFocused();
    const pdfFrame = pdfDialog.getByTitle(`Preview of ${pdfName}`);
    await expect(pdfFrame).toHaveAttribute("src", /^blob:/);
    await expect.poll(() => page.frames().some((frame) => frame.url().startsWith("chrome-extension://"))).toBe(true);
    await pdfDialog.screenshot({ path: info.outputPath("synthetic-pdf.png") });
    expect(reads).toEqual([reports[0]?.downloadUrl]);
    const pending = page.waitForEvent("download");
    await pdfDialog.getByRole("link", { name: `Download ${pdfName}` }).click();
    const saved = await pending;
    expect(saved.suggestedFilename()).toBe(pdfName);
    expect(await readFile(await saved.path())).toEqual(pdf);
    expect(reads).toEqual([reports[0]?.downloadUrl, reports[0]?.downloadUrl]);
    await page.keyboard.press("Escape");
    await expect(pdfDialog).toBeHidden();
    await expect(view(pdfName)).toBeFocused();
    await expect(draft).toHaveValue("Synthetic unsaved symptom");
    expect(new URL(page.url()).pathname + new URL(page.url()).search).toBe(visit);

    await page.setViewportSize({ width: 390, height: 760 });
    await view(pngName).click();
    const pngDialog = page.getByRole("dialog", { name: pngName });
    const image = pngDialog.getByRole("img", { name: `Preview of ${pngName}` });
    await expect.poll(() => image.evaluate((element: HTMLImageElement) => element.naturalWidth)).toBe(600);
    await pngDialog.screenshot({ path: info.outputPath("synthetic-image-mobile.png") });
    await pngDialog.getByRole("button", { name: "Close" }).click();
    for (const [name, message] of [
      [deniedName, "Marley denied access to this file."],
      [mismatchName, "This file can’t be previewed."],
    ] as const) {
      await view(name).click();
      const dialog = page.getByRole("dialog", { name });
      await expect(dialog.getByText(message)).toBeVisible();
      await expect(dialog.locator("iframe, img")).toHaveCount(0);
      await dialog.getByRole("button", { name: "Close" }).click();
    }
    expect(reads).toEqual([reports[0]?.downloadUrl, ...reports.map(({ downloadUrl }) => downloadUrl)]);
    expect(writes).toEqual([]);
  } finally {
    fixture.close();
  }
});
