import { fireEvent, render, screen } from "@testing-library/react";
import { useTranslation } from "react-i18next";
import { describe, expect, it } from "vitest";
import { LocaleProvider, useLocale } from "./LocaleProvider";

function Harness() {
  const { locale, setLocale } = useLocale();
  return (
    <div>
      <label htmlFor="note">{locale === "th" ? "บันทึก" : "Note"}</label>
      <textarea id="note" defaultValue="" />
      <button type="button" onClick={() => setLocale("en")}>
        EN
      </button>
    </div>
  );
}

function Probe() {
  const { t } = useTranslation();
  const { locale, setLocale } = useLocale();
  return (
    <div>
      <span data-testid="locale">{locale}</span>
      <span data-testid="today">{t("nav.today")}</span>
      <button type="button" onClick={() => setLocale("en")}>
        EN
      </button>
    </div>
  );
}

describe("LocaleProvider", () => {
  it("preserves an uncontrolled draft value across a locale switch", () => {
    render(
      <LocaleProvider initial="th">
        <Harness />
      </LocaleProvider>,
    );
    fireEvent.change(screen.getByLabelText(/note|บันทึก/i), {
      target: { value: "draft text" },
    });
    fireEvent.click(screen.getByRole("button", { name: "EN" }));
    expect(screen.getByLabelText(/note|บันทึก/i)).toHaveValue("draft text");
  });

  it("updates the document language and the shared i18n instance", async () => {
    render(
      <LocaleProvider initial="th">
        <Probe />
      </LocaleProvider>,
    );
    expect(document.documentElement.lang).toBe("th");
    expect(screen.getByTestId("today")).toHaveTextContent("วันนี้");
    fireEvent.click(screen.getByRole("button", { name: "EN" }));
    expect(screen.getByTestId("locale")).toHaveTextContent("en");
    expect(document.documentElement.lang).toBe("en");
    await screen.findByText("Today");
  });
});
