import { fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import { App } from "../../app/App";
import { ROUTES } from "../../app/routes";
import { CAPABILITY_NAMES, type CapabilityMap } from "../../domain/capability";
import { createHandlers } from "../../test/msw/handlers";
import { server } from "../../test/msw/server";
import { renderWithCaps, setViewport } from "../../test/render";
import { PRINT_TEMPLATES, PrintPreview } from "../print/PrintPreview";
import { canPrint, usePrint } from "../print/usePrint";
import { LanguageToggle } from "../shell/LanguageToggle";
import { SystemStatesPage } from "./SystemStatesPage";

afterEach(() => vi.restoreAllMocks());

function HookProbe({ onResult }: { onResult: (result: boolean) => void }) {
  const output = usePrint();
  return (
    <button type="button" onClick={() => onResult(output.print())}>
      Attempt hook
    </button>
  );
}

it.each(["th", "en"] as const)(
  "renders eight labelled demo states in %s",
  (locale) => {
    renderWithCaps({}, <SystemStatesPage />, { locale });
    const labels =
      locale === "en"
        ? [
            "Populated",
            "Empty",
            "Loading",
            "Validation",
            "Error",
            "Success",
            "Permission denied",
            "Unsaved changes",
          ]
        : [
            "มีข้อมูล",
            "ไม่มีข้อมูล",
            "กำลังโหลด",
            "ตรวจสอบข้อมูล",
            "เกิดข้อผิดพลาด",
            "สำเร็จ",
            "ไม่มีสิทธิ์เข้าถึง",
            "มีการเปลี่ยนแปลงที่ยังไม่บันทึก",
          ];
    for (const label of labels)
      expect(screen.getByRole("heading", { name: label })).toBeVisible();
    expect(screen.getByRole("textbox")).toHaveAttribute("aria-invalid", "true");
    expect(screen.getByRole("textbox")).toHaveAccessibleDescription(
      locale === "en"
        ? "Example: enter a value before continuing."
        : "ตัวอย่าง: กรอกข้อมูลก่อนดำเนินการต่อ",
    );
    expect(screen.getByRole("status")).toBeVisible();
    expect(screen.getByRole("alert")).toBeVisible();
  },
);

it("requires explicit output locale, independent of interface language", async () => {
  const user = userEvent.setup();
  renderWithCaps(
    {},
    <>
      <LanguageToggle />
      <PrintPreview template="receipt" locale="en" />
    </>,
  );
  const sheet = screen.getByRole("article");
  expect(sheet).toHaveAttribute("lang", "en");
  expect(within(sheet).getByRole("heading", { name: "Receipt" })).toBeVisible();
  expect(screen.getByRole("button", { name: "พิมพ์ / ส่งออก" })).toBeDisabled();
  await user.click(screen.getByRole("button", { name: "EN" }));
  expect(sheet).toHaveAttribute("lang", "en");
  expect(screen.getByRole("button", { name: "Print / export" })).toBeDisabled();
});

it.each(PRINT_TEMPLATES)(
  "%s remains a labelled Thai layout sample under English UI without record reads",
  (template) => {
    const fetch = vi.spyOn(globalThis, "fetch");
    renderWithCaps({}, <PrintPreview template={template} locale="th" />, {
      locale: "en",
    });
    const sheet = screen.getByRole("article", { name: "ตัวอย่างรูปแบบเอกสาร" });
    expect(sheet).toHaveAttribute("lang", "th");
    expect(within(sheet).getByText("ตัวอย่าง — ไม่ใช่หลักฐานทางคลินิก")).toBeVisible();
    expect(fetch).not.toHaveBeenCalled();
  },
);

it("output selector changes document only; UI toggle never resets document language", async () => {
  const user = userEvent.setup();
  renderWithCaps(
    {},
    <>
      <LanguageToggle />
      <SystemStatesPage />
    </>,
  );
  await user.selectOptions(
    screen.getByRole("combobox", { name: "ภาษาที่พิมพ์" }),
    "en",
  );
  expect(screen.getByRole("article")).toHaveAttribute("lang", "en");
  await user.click(screen.getByRole("button", { name: "EN" }));
  await user.selectOptions(
    screen.getByRole("combobox", { name: "Documents" }),
    "lab",
  );
  expect(
    within(screen.getByRole("article")).getByRole("heading", {
      name: "Lab request",
    }),
  ).toBeVisible();
  await user.click(screen.getByRole("button", { name: "TH" }));
  expect(screen.getByRole("article")).toHaveAttribute("lang", "en");
  expect(screen.getByRole("combobox", { name: "ภาษาที่พิมพ์" })).toHaveValue("en");
});

it.each([
  {},
  { "read.admin": "enabled" },
  Object.fromEntries(CAPABILITY_NAMES.map((name) => [name, "enabled"])),
] as CapabilityMap[])(
  "never grants print, even with enabled capabilities: %j",
  async (caps) => {
    const print = vi.spyOn(window, "print").mockImplementation(() => {});
    const result = vi.fn();
    renderWithCaps(
      caps,
      <>
        <PrintPreview template="receipt" locale="th" />
        <HookProbe onResult={result} />
      </>,
      { locale: "en" },
    );
    const button = screen.getByRole("button", { name: "Print / export" });
    expect(button.tagName).toBe("BUTTON");
    expect(button).toBeDisabled();
    expect(button).toHaveAccessibleDescription(
      /Printing is not available.*contract and permissions are verified/,
    );
    await userEvent.setup().click(button);
    fireEvent.click(screen.getByRole("button", { name: "Attempt hook" }));
    expect(result).toHaveBeenCalledWith(false);
    expect(canPrint(caps)).toBe(false);
    expect(print).not.toHaveBeenCalled();
  },
);

it("sample routed gate suppresses href by default; read navigation remains ordinary link", () => {
  renderWithCaps({}, <SystemStatesPage />, { locale: "en" });
  const example = screen.getByRole("link", {
    name: "Gated navigation example",
  });
  expect(example).toHaveAttribute("aria-disabled", "true");
  expect(example).not.toHaveAttribute("href");
  expect(screen.getByRole("link", { name: "Back to home" })).toHaveAttribute(
    "href",
    ROUTES.home,
  );
});

it("enabled sample gate navigates only to existing preview anchor, never writes or enables print", () => {
  renderWithCaps({ "write.patient.update": "enabled" }, <SystemStatesPage />, {
    locale: "en",
  });
  expect(
    screen.getByRole("link", { name: "Gated navigation example" }),
  ).toHaveAttribute("href", `${ROUTES.system}#system-preview`);
  expect(screen.getByRole("button", { name: "Print / export" })).toBeDisabled();
});

it.each(["REC", "DOC", "NUR", "PHA", "CAS", "MGR", "ADM"] as const)(
  "protected system route never grants print to %s",
  async (role) => {
    server.use(...createHandlers({ role }));
    window.history.pushState({}, "", ROUTES.system);
    render(<App />);
    expect(
      await screen.findByRole("heading", { level: 1, name: "พิมพ์และส่งออก" }),
    ).toBeVisible();
    expect(screen.getByRole("button", { name: "พิมพ์ / ส่งออก" })).toBeDisabled();
  },
);

it.each([390, 834, 1440] as const)(
  "system route exposes labelled controls and sample at %s",
  async (width) => {
    setViewport(width);
    server.use(...createHandlers({ role: "ADM" }));
    window.history.pushState({}, "", ROUTES.system);
    render(<App />);
    expect(
      await screen.findByRole("combobox", { name: "ภาษาที่พิมพ์" }),
    ).toBeVisible();
    expect(screen.getByRole("article")).toHaveAttribute("lang", "th");
    expect(screen.getByRole("button", { name: "พิมพ์ / ส่งออก" })).toBeDisabled();
  },
);

it("anonymous system route still requires session", async () => {
  server.use(...createHandlers({ authed: false }));
  window.history.pushState({}, "", ROUTES.system);
  render(<App />);
  await screen.findByRole("textbox", { name: /ชื่อผู้ใช้/ });
  expect(screen.queryByRole("article")).toBeNull();
});
