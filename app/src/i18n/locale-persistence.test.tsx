import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it } from "vitest";
import { App } from "../app/App";
import { ROUTES } from "../app/routes";
import { createHandlers } from "../test/msw/handlers";
import { server } from "../test/msw/server";
import { initI18n } from "./index";
import { LOCALE_KEY, readStoredLocale } from "./locale-storage";

beforeEach(() => {
  window.history.replaceState({}, "", ROUTES.home);
  server.use(...createHandlers());
});

describe("language preference persistence", () => {
  it("stores nothing just by rendering", async () => {
    render(<App />);
    expect(await screen.findByTestId("role-home")).toBeVisible();
    expect(localStorage.getItem(LOCALE_KEY)).toBeNull();
  });

  it("persists an explicit choice and applies it on the next load", async () => {
    const user = userEvent.setup();
    render(<App />);
    await screen.findByTestId("role-home");
    expect(document.documentElement.lang).toBe("th");

    await user.click(screen.getByRole("button", { name: "EN" }));
    expect(localStorage.getItem(LOCALE_KEY)).toBe("en");
    expect(document.documentElement.lang).toBe("en");
    expect(screen.getByText("Your workspace")).toBeVisible();

    cleanup();
    window.history.replaceState({}, "", ROUTES.home);
    render(<App />);
    expect(await screen.findByText("Your workspace")).toBeVisible();
    expect(document.documentElement.lang).toBe("en");
  });

  it("ignores a stored value that is not a supported locale", async () => {
    localStorage.setItem(LOCALE_KEY, "fr");
    expect(readStoredLocale()).toBeNull();
    render(<App />);
    expect(await screen.findByText("พื้นที่ทำงานของคุณ")).toBeVisible();
    expect(document.documentElement.lang).toBe("th");
  });

  it("keeps Thai and English catalogues addressable for the stored locale", () => {
    expect(initI18n("en").t("roleHome.title")).toBe("Your workspace");
    expect(initI18n("th").t("roleHome.title")).toBe("พื้นที่ทำงานของคุณ");
  });
});
