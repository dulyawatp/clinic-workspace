import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { Button } from "./Button";
import { PageHeader } from "./PageHeader";
import { StatePanel } from "./StatePanel";
import { StatusChip } from "./StatusChip";

describe("UI primitives", () => {
  it("exposes a disabled reason to assistive tech", () => {
    render(
      <Button disabled disabledReason="Awaiting backend verification">
        Save
      </Button>,
    );
    const button = screen.getByRole("button", { name: /save/i });
    expect(button).toBeDisabled();
    expect(button).toHaveAccessibleDescription("Awaiting backend verification");
    expect(screen.getByText("Awaiting backend verification")).toBeVisible();
  });

  it("preserves caller descriptions alongside disabled reasons", () => {
    render(
      <>
        <p id="help">Check details first</p>
        <Button disabled aria-describedby="help" disabledReason="Not available">
          Save
        </Button>
      </>,
    );
    expect(screen.getByRole("button")).toHaveAccessibleDescription(
      "Check details first Not available",
    );
  });

  it("does not show a disabled reason for an enabled button", () => {
    render(<Button disabledReason="Not available">Save</Button>);
    expect(screen.queryByText("Not available")).not.toBeInTheDocument();
    expect(screen.getByRole("button")).toBeEnabled();
  });

  it.each(["primary", "secondary", "danger"] as const)(
    "supports %s button styling and native attributes",
    (variant) => {
      render(
        <Button variant={variant} className="custom" name="save">
          Save
        </Button>,
      );
      expect(screen.getByRole("button")).toHaveClass(
        "ui-button",
        `ui-button--${variant}`,
        "custom",
      );
      expect(screen.getByRole("button")).toHaveAttribute("name", "save");
      expect(screen.getByRole("button")).toHaveAttribute("type", "button");
    },
  );

  it("never renders a status chip by colour alone", () => {
    render(
      <StatusChip tone="danger" label="Out of stock" icon={<span>!</span>} />,
    );
    expect(screen.getByText("Out of stock")).toBeVisible();
    expect(screen.getByText("!").parentElement).toHaveAttribute(
      "aria-hidden",
      "true",
    );
  });

  it("supports neutral status styling", () => {
    render(<StatusChip tone="neutral" label="Not reviewed" />);
    expect(screen.getByText("Not reviewed")).toHaveClass("ui-chip--neutral");
  });

  it("uses a 44px minimum target class", () => {
    render(<Button>Open</Button>);
    expect(screen.getByRole("button")).toHaveClass("ui-button");
    const css = readFileSync(resolve("src/ui/ui.css"), "utf8");
    expect(css).toMatch(/min-height:\s*var\(--target-min\)/);
    expect(css).toMatch(/:focus-visible/);
  });

  it("lets a long Thai label wrap instead of truncating", () => {
    const css = readFileSync(resolve("src/ui/ui.css"), "utf8");
    expect(css).toMatch(/white-space:\s*normal/);
    expect(css).not.toMatch(/text-overflow:\s*ellipsis/);
  });

  it("announces loading", () => {
    render(<StatePanel kind="loading" title="Loading patients" />);
    expect(screen.getByRole("status")).toHaveTextContent("Loading patients");
  });

  it("announces errors and supports retry", () => {
    const onRetry = vi.fn();
    render(
      <StatePanel
        kind="error"
        title="Could not load"
        detail="Try again"
        onRetry={onRetry}
        retryLabel="ลองอีกครั้ง"
      />,
    );
    expect(screen.getByRole("alert")).toHaveTextContent("Could not load");
    expect(screen.getByText("Try again")).toBeVisible();
    screen.getByRole("button", { name: "ลองอีกครั้ง" }).click();
    expect(onRetry).toHaveBeenCalledOnce();
  });

  it.each(["empty", "permission-denied"] as const)(
    "renders %s state",
    (kind) => {
      render(<StatePanel kind={kind} title="No records available" />);
      expect(
        screen.getByRole("heading", { name: "No records available" }),
      ).toBeVisible();
      expect(screen.queryByRole("button")).not.toBeInTheDocument();
    },
  );

  it("renders page heading, breadcrumb and actions", () => {
    render(
      <PageHeader
        title="Patients"
        breadcrumb={<a href="/">Home</a>}
        actions={<Button>Add patient</Button>}
      />,
    );
    expect(
      screen.getByRole("heading", { level: 1, name: "Patients" }),
    ).toBeVisible();
    expect(screen.getByRole("link", { name: "Home" })).toBeVisible();
    expect(screen.getByRole("button", { name: "Add patient" })).toBeVisible();
  });
});
