import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { LocaleProvider } from "../i18n/LocaleProvider";
import {
  Badge,
  Button,
  Card,
  DataTable,
  Drawer,
  PageHeader,
  SampleBanner,
  UnavailablePanel,
} from "./components";

function withLocale(ui: React.ReactElement) {
  return render(<LocaleProvider initial="en">{ui}</LocaleProvider>);
}

describe("design buttons", () => {
  it.each(["primary", "cyan", "secondary", "ghost", "danger"] as const)(
    "renders a %s variant as a native button",
    (variant) => {
      withLocale(<Button variant={variant}>Save</Button>);
      const button = screen.getByRole("button", { name: "Save" });
      expect(button).toHaveClass("pc-button", `pc-button--${variant}`);
      expect(button).toHaveAttribute("type", "button");
    },
  );

  it("supports small and block sizing without losing the target", () => {
    withLocale(
      <Button size="sm" block>
        Filter
      </Button>,
    );
    expect(screen.getByRole("button")).toHaveClass(
      "pc-button--sm",
      "pc-button--block",
    );
  });

  it("keeps a disabled action disabled and labelled", () => {
    withLocale(<Button disabled>Cancel</Button>);
    expect(screen.getByRole("button", { name: "Cancel" })).toBeDisabled();
  });
});

describe("status badges", () => {
  it("always renders readable text, never colour alone", () => {
    withLocale(<Badge tone="warning">Low stock</Badge>);
    expect(screen.getByText("Low stock")).toHaveClass(
      "pc-badge",
      "pc-badge--warning",
    );
  });

  it.each(["success", "danger", "info", "demo", "neutral"] as const)(
    "supports the %s tone",
    (tone) => {
      withLocale(<Badge tone={tone}>{tone}</Badge>);
      expect(screen.getByText(tone)).toHaveClass(`pc-badge--${tone}`);
    },
  );
});

describe("cards", () => {
  it("renders head, body and foot regions", () => {
    withLocale(
      <Card title="Today’s queue" hint="Sample" footer={<span>Footer</span>}>
        <p>Two patients waiting</p>
      </Card>,
    );
    expect(
      screen.getByRole("heading", { name: "Today’s queue" }),
    ).toBeVisible();
    expect(screen.getByText("Sample")).toBeVisible();
    expect(screen.getByText("Two patients waiting")).toBeVisible();
    expect(screen.getByText("Footer")).toBeVisible();
  });
});

describe("data tables", () => {
  it("wraps rows in a scroll container with a caption", () => {
    withLocale(
      <DataTable
        caption="Movements"
        columns={[
          { key: "item", label: "Item" },
          { key: "qty", label: "Quantity", align: "end" },
        ]}
        rows={[["Paracetamol", "12"]]}
      />,
    );
    const table = screen.getByRole("table", { name: "Movements" });
    expect(table.parentElement).toHaveClass("pc-tablewrap");
    expect(
      screen.getByRole("columnheader", { name: "Quantity" }),
    ).toHaveClass("pc-num");
  });

  it("states the empty table instead of rendering an empty grid", () => {
    withLocale(
      <DataTable
        columns={[{ key: "item", label: "Item" }]}
        rows={[]}
        empty="No stock movements yet"
      />,
    );
    expect(screen.getByText("No stock movements yet")).toBeVisible();
  });
});

describe("page header band", () => {
  it("renders title, eyebrow, subtitle, breadcrumb and actions", () => {
    withLocale(
      <PageHeader
        title="Doctor’s day"
        eyebrow="Clinic"
        subtitle="Today"
        breadcrumb={<a href="/clinic">Home</a>}
        actions={<Button>Open</Button>}
      />,
    );
    expect(
      screen.getByRole("heading", { level: 1, name: "Doctor’s day" }),
    ).toBeVisible();
    expect(screen.getByText("Clinic")).toBeVisible();
    expect(screen.getByText("Today")).toBeVisible();
    expect(screen.getByRole("link", { name: "Home" })).toBeVisible();
    expect(screen.getByRole("button", { name: "Open" })).toBeVisible();
  });
});

describe("unavailable panel", () => {
  it("names the state and its explanation", () => {
    withLocale(
      <UnavailablePanel
        title="This screen is not connected yet"
        detail="Layout only"
      />,
    );
    expect(
      screen.getByRole("heading", {
        name: "This screen is not connected yet",
      }),
    ).toBeVisible();
    expect(screen.getByText("Layout only")).toBeVisible();
  });
});

describe("sample banner", () => {
  it("labels sample content explicitly", () => {
    withLocale(
      <SampleBanner label="Demo data" title="Sample invoices">
        Not a Marley contract
      </SampleBanner>,
    );
    expect(screen.getByTestId("sample-banner")).toBeVisible();
    expect(screen.getByText("Demo data")).toBeVisible();
    expect(screen.getByText("Not a Marley contract")).toBeVisible();
  });
});

describe("drawer", () => {
  it("opens modally, focuses its close control and routes dismissal to the owner", async () => {
    const onClose = vi.fn();
    const showModal = vi.fn(function (this: HTMLDialogElement) {
      this.setAttribute("open", "");
    });
    Object.defineProperty(HTMLDialogElement.prototype, "showModal", {
      configurable: true,
      value: showModal,
    });

    withLocale(
      <Drawer title="Receive stock" onClose={onClose}>
        <p>Body</p>
      </Drawer>,
    );
    expect(showModal).toHaveBeenCalledOnce();
    expect(
      screen.getByRole("heading", { name: "Receive stock" }),
    ).toBeVisible();
    const close = screen.getByRole("button", { name: "Close" });
    expect(close).toHaveFocus();

    // Escape reaches the owner through the dialog's cancel event.
    fireEvent(screen.getByRole("dialog"), new Event("cancel", { cancelable: true }));
    expect(onClose).toHaveBeenCalledTimes(1);

    fireEvent.click(close);
    expect(onClose).toHaveBeenCalledTimes(2);
  });

  it("keeps the labelled dialog and its footer actions reachable", () => {
    Object.defineProperty(HTMLDialogElement.prototype, "showModal", {
      configurable: true,
      value: function (this: HTMLDialogElement) {
        this.setAttribute("open", "");
      },
    });
    withLocale(
      <Drawer title="Details" onClose={() => {}} footer={<Button>Save</Button>}>
        <p>Line items</p>
      </Drawer>,
    );
    expect(screen.getByRole("dialog", { name: "Details" })).toBeVisible();
    expect(screen.getByText("Line items")).toBeVisible();
    expect(screen.getByRole("button", { name: "Save" })).toBeVisible();
  });
});
