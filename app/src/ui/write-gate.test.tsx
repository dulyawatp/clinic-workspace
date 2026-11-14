import { fireEvent, render, screen } from "@testing-library/react";
import { createRef, forwardRef, type RefObject, useState } from "react";
import { I18nextProvider } from "react-i18next";
import { Link, MemoryRouter, useLocation } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import { RoutedControl, WriteGate } from "@/ui/WriteGate";
import type {
  Capability,
  CapabilityMap,
  CapabilityState,
} from "../domain/capability";
import { CapabilityProvider } from "../features/shell/CapabilityProvider";
import { initI18n } from "../i18n";
import { LocaleProvider, useLocale } from "../i18n/LocaleProvider";
import { Button } from "./Button";

function LocationProbe() {
  return <p data-testid="location">{useLocation().pathname}</p>;
}

function RetentionLabel() {
  const [value] = useState("retained");
  return <span>{value}</span>;
}

function RetentionTree({ state }: { state: CapabilityState }) {
  return (
    <CapabilityProvider value={{ "write.patient.create": state }}>
      <WriteGate capability="write.patient.create">
        <button type="button">
          <RetentionLabel />
        </button>
      </WriteGate>
    </CapabilityProvider>
  );
}

function LocaleSwitch() {
  const { setLocale } = useLocale();
  return (
    <button type="button" onClick={() => setLocale("en")}>
      English
    </button>
  );
}

const StatefulLabel = forwardRef<
  HTMLButtonElement,
  { initialize: () => number }
>(function StatefulLabel({ initialize }, ref) {
  const [count, setCount] = useState(initialize);
  return (
    <button
      type="button"
      ref={ref}
      data-custom-render="kept"
      onDoubleClick={() => setCount((n) => n + 1)}
    >
      Save {count}
    </button>
  );
});

function StatefulTree({
  state,
  anchorRef,
  labelRef,
  initialize,
  onAuxClick,
}: {
  state: CapabilityState;
  anchorRef: RefObject<HTMLAnchorElement>;
  labelRef: RefObject<HTMLButtonElement>;
  initialize: () => number;
  onAuxClick: () => void;
}) {
  return (
    <MemoryRouter initialEntries={["/patients"]}>
      <LocationProbe />
      <CapabilityProvider value={{ "write.patient.create": state }}>
        <WriteGate capability="write.patient.create">
          <RoutedControl
            ref={anchorRef}
            to="/patients/new"
            onAuxClick={onAuxClick}
          >
            <StatefulLabel ref={labelRef} initialize={initialize} />
          </RoutedControl>
        </WriteGate>
      </CapabilityProvider>
    </MemoryRouter>
  );
}

const UnsupportedCustomLink = forwardRef<
  HTMLAnchorElement,
  { to: string; onRender: () => void }
>(function UnsupportedCustomLink({ onRender, ...props }, ref) {
  onRender();
  const [label] = useState("Custom own render");
  return (
    <Link {...props} ref={ref}>
      {label}
    </Link>
  );
});

const OwningCustomAction = forwardRef<
  HTMLAnchorElement,
  { initialize: () => number; onRender: () => void }
>(function OwningCustomAction({ initialize, onRender }, ref) {
  onRender();
  const [count, setCount] = useState(initialize);
  return (
    <WriteGate capability="write.patient.create">
      <RoutedControl ref={ref} to="/patients/new" data-custom="own-render">
        <button type="button" onDoubleClick={() => setCount((n) => n + 1)}>
          Custom {count}
        </button>
      </RoutedControl>
    </WriteGate>
  );
});

function OwningActionTree({
  state,
  anchorRef,
  initialize,
  onRender,
}: {
  state: CapabilityState;
  anchorRef: RefObject<HTMLAnchorElement>;
  initialize: () => number;
  onRender: () => void;
}) {
  return (
    <MemoryRouter>
      <CapabilityProvider value={{ "write.patient.create": state }}>
        <OwningCustomAction
          ref={anchorRef}
          initialize={initialize}
          onRender={onRender}
        />
      </CapabilityProvider>
    </MemoryRouter>
  );
}

describe("WriteGate", () => {
  it("defaults to disabled without a capability provider", () => {
    render(
      <WriteGate capability="write.patient.create">
        <button type="button">Save</button>
      </WriteGate>,
    );
    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
    expect(screen.getByRole("button")).toHaveAccessibleDescription(
      /ยังไม่เปิดใช้งาน/,
    );
  });

  it.each(["read_only", "blocked_unverified", "error_upstream"] as const)(
    "keeps %s controls visibly disabled with safe bilingual reasons",
    (state) => {
      const { unmount } = render(
        <LocaleProvider initial="en">
          <CapabilityProvider value={{ "write.patient.create": state }}>
            <WriteGate capability="write.patient.create">
              <button type="button">Save</button>
            </WriteGate>
          </CapabilityProvider>
        </LocaleProvider>,
      );
      expect(screen.getByRole("button")).toBeDisabled();
      expect(screen.getByRole("button")).toHaveAccessibleDescription(
        /not available|try again/i,
      );
      unmount();
      render(
        <LocaleProvider initial="th">
          <CapabilityProvider value={{ "write.patient.create": state }}>
            <WriteGate capability="write.patient.create">
              <button type="button">Save</button>
            </WriteGate>
          </CapabilityProvider>
        </LocaleProvider>,
      );
      expect(screen.getByRole("button")).toHaveAccessibleDescription(
        /ยังไม่เปิดใช้งาน|ลองใหม่/,
      );
    },
  );

  it("preserves enabled child attributes and handler unchanged", () => {
    const onClick = vi.fn();
    render(
      <CapabilityProvider value={{ "write.patient.create": "enabled" }}>
        <WriteGate capability="write.patient.create">
          <button type="button" className="original" onClick={onClick}>
            Save
          </button>
        </WriteGate>
      </CapabilityProvider>,
    );
    const button = screen.getByRole("button");
    expect(button).toBeEnabled();
    expect(button).toHaveClass("original");
    expect(button).not.toHaveAttribute("aria-describedby");
    fireEvent.click(button);
    expect(onClick).toHaveBeenCalledOnce();
  });

  it.each(["th", "en"] as const)(
    "replaces denied controls with %s permission-denied text",
    (locale) => {
      render(
        <LocaleProvider initial={locale}>
          <CapabilityProvider value={{ "write.patient.create": "denied" }}>
            <WriteGate capability="write.patient.create">
              <button type="button">Save</button>
            </WriteGate>
          </CapabilityProvider>
        </LocaleProvider>,
      );
      expect(screen.queryByRole("button")).toBeNull();
      expect(
        screen.getByRole("heading", { name: /role not permitted|ไม่มีสิทธิ์/i }),
      ).toBeVisible();
    },
  );

  it("honors custom reason and preserves caller descriptions on Button", () => {
    render(
      <>
        <p id="help">Check details</p>
        <WriteGate capability="write.patient.create" reason="รอการตรวจสอบ">
          <Button aria-describedby="help">Save</Button>
        </WriteGate>
      </>,
    );
    expect(screen.getByRole("button")).toBeDisabled();
    expect(screen.getByRole("button")).toHaveClass("ui-button");
    expect(screen.getByRole("button")).toHaveAccessibleDescription(
      "Check details รอการตรวจสอบ",
    );
  });

  it("blocks link navigation and pointer/keyboard callbacks", () => {
    const onClick = vi.fn();
    const onKeyDown = vi.fn();
    render(
      <WriteGate capability="write.patient.create">
        <a href="/write" onClick={onClick} onKeyDown={onKeyDown}>
          Save
        </a>
      </WriteGate>,
    );
    const link = screen.getByRole("link", { name: "Save" });
    expect(link).toHaveAttribute("aria-disabled", "true");
    expect(link).not.toHaveAttribute("href");
    expect(link).toHaveAttribute("tabindex", "-1");
    fireEvent.click(link);
    fireEvent.keyDown(link, { key: "Enter" });
    expect(fireEvent.keyDown(link, { key: "Tab" })).toBe(true);
    expect(onClick).not.toHaveBeenCalled();
    expect(onKeyDown).not.toHaveBeenCalled();
  });

  it("retains mounted control state across non-denied capability changes", () => {
    const { rerender } = render(<RetentionTree state="enabled" />);
    const control = screen.getByRole("button");
    rerender(<RetentionTree state="read_only" />);
    expect(screen.getByRole("button")).toBe(control);
    expect(control).toBeDisabled();
  });

  it("updates reasons when locale changes", () => {
    render(
      <LocaleProvider initial="th">
        <LocaleSwitch />
        <WriteGate capability="write.patient.create">
          <button type="button">Save</button>
        </WriteGate>
      </LocaleProvider>,
    );
    fireEvent.click(screen.getByRole("button", { name: "English" }));
    expect(
      screen.getByRole("button", { name: "Save" }),
    ).toHaveAccessibleDescription(/not available/i);
  });

  it("removes real router destinations and blocks primary, auxiliary and context-menu activation", () => {
    const onClick = vi.fn();
    const onAuxClick = vi.fn();
    const onContextMenu = vi.fn();
    render(
      <MemoryRouter initialEntries={["/patients"]}>
        <LocationProbe />
        <WriteGate capability="write.patient.create">
          <RoutedControl
            to={{ pathname: "/patients/new" }}
            className="ui-button"
            title="Create patient"
            onClick={onClick}
            onAuxClick={onAuxClick}
            onContextMenu={onContextMenu}
          >
            <span>Save</span>
          </RoutedControl>
        </WriteGate>
      </MemoryRouter>,
    );
    const link = screen.getByRole("link", { name: "Save" });
    expect(link).not.toHaveAttribute("href");
    expect(link).not.toHaveAttribute("to");
    expect(link).toHaveAttribute("aria-disabled", "true");
    expect(link).toHaveAccessibleDescription(/ยังไม่เปิดใช้งาน/);
    expect(link).toHaveClass("ui-button");
    expect(link).toHaveAttribute("title", "Create patient");
    expect(fireEvent.click(link)).toBe(false);
    expect(
      fireEvent(
        link,
        new MouseEvent("auxclick", {
          button: 1,
          bubbles: true,
          cancelable: true,
        }),
      ),
    ).toBe(false);
    expect(fireEvent.contextMenu(link)).toBe(false);
    expect(screen.getByTestId("location")).toHaveTextContent("/patients");
    expect(onClick).not.toHaveBeenCalled();
    expect(onAuxClick).not.toHaveBeenCalled();
    expect(onContextMenu).not.toHaveBeenCalled();
  });

  it("preserves navigation for an enabled explicit routed control", () => {
    render(
      <MemoryRouter initialEntries={["/patients"]}>
        <LocationProbe />
        <CapabilityProvider value={{ "write.patient.create": "enabled" }}>
          <WriteGate capability="write.patient.create">
            <RoutedControl to="/patients/new">Save</RoutedControl>
          </WriteGate>
        </CapabilityProvider>
      </MemoryRouter>,
    );
    expect(screen.getByRole("link")).toHaveAttribute("href", "/patients/new");
    fireEvent.click(screen.getByRole("link"));
    expect(screen.getByTestId("location")).toHaveTextContent("/patients/new");
  });

  it("retains custom render, refs and state through enabled→blocked→enabled", () => {
    const initialize = vi.fn(() => 0);
    const onAuxClick = vi.fn();
    const anchorRef = createRef<HTMLAnchorElement>();
    const labelRef = createRef<HTMLButtonElement>();
    const treeProps = {
      anchorRef,
      labelRef,
      initialize,
      onAuxClick,
    };
    const { rerender } = render(
      <StatefulTree state="enabled" {...treeProps} />,
    );
    const anchor = screen.getByRole("link");
    const label = screen.getByText("Save 0");
    expect(anchorRef.current).toBe(anchor);
    expect(labelRef.current).toBe(label);
    expect(label).toHaveAttribute("data-custom-render", "kept");
    fireEvent.doubleClick(label);
    expect(label).toHaveTextContent("Save 1");
    expect(anchor).toHaveAttribute("href", "/patients/new");
    rerender(<StatefulTree state="error_upstream" {...treeProps} />);
    expect(screen.getByRole("link")).toBe(anchor);
    expect(screen.getByText("Save 1")).toBe(label);
    expect(anchorRef.current).toBe(anchor);
    expect(labelRef.current).toBe(label);
    expect(anchor).not.toHaveAttribute("href");
    expect(
      fireEvent(
        anchor,
        new MouseEvent("auxclick", {
          button: 1,
          bubbles: true,
          cancelable: true,
        }),
      ),
    ).toBe(false);
    expect(fireEvent.contextMenu(anchor)).toBe(false);
    fireEvent.click(anchor);
    expect(onAuxClick).not.toHaveBeenCalled();
    expect(screen.getByTestId("location")).toHaveTextContent("/patients");
    rerender(<StatefulTree state="enabled" {...treeProps} />);
    expect(screen.getByRole("link")).toBe(anchor);
    expect(screen.getByText("Save 1")).toBe(label);
    expect(anchorRef.current).toBe(anchor);
    expect(labelRef.current).toBe(label);
    expect(initialize).toHaveBeenCalledOnce();
    expect(anchor).toHaveAttribute("href", "/patients/new");
    expect(
      fireEvent(
        anchor,
        new MouseEvent("auxclick", {
          button: 1,
          bubbles: true,
          cancelable: true,
        }),
      ),
    ).toBe(true);
    expect(onAuxClick).toHaveBeenCalledOnce();
    fireEvent.click(anchor);
    expect(screen.getByTestId("location")).toHaveTextContent("/patients/new");
  });

  it.each(["enabled", "read_only", "error_upstream"] as const)(
    "fails closed for arbitrary custom routed roots in %s without fabricating their render",
    (state) => {
      const renderCustom = vi.fn();
      const ref = createRef<HTMLAnchorElement>();
      render(
        <MemoryRouter>
          <CapabilityProvider value={{ "write.patient.create": state }}>
            <WriteGate capability="write.patient.create">
              <UnsupportedCustomLink
                to="/unsafe"
                ref={ref}
                onRender={renderCustom}
              />
            </WriteGate>
          </CapabilityProvider>
        </MemoryRouter>,
      );
      expect(renderCustom).not.toHaveBeenCalled();
      expect(ref.current).toBeNull();
      expect(screen.queryByRole("link")).toBeNull();
      expect(screen.queryByText("Custom own render")).toBeNull();
      expect(
        screen.getByRole("heading", {
          name: initI18n("th").t("errors.UNSUPPORTED"),
        }),
      ).toBeVisible();
    },
  );

  it("preserves a custom routed component's own hooks, render and ref when it owns its gate", () => {
    const initialize = vi.fn(() => 0);
    const renderCustom = vi.fn();
    const ref = createRef<HTMLAnchorElement>();
    const { rerender } = render(
      <OwningActionTree
        state="enabled"
        anchorRef={ref}
        initialize={initialize}
        onRender={renderCustom}
      />,
    );
    const anchor = screen.getByRole("link");
    fireEvent.doubleClick(screen.getByText("Custom 0"));
    expect(anchor).toHaveTextContent("Custom 1");
    rerender(
      <OwningActionTree
        state="read_only"
        anchorRef={ref}
        initialize={initialize}
        onRender={renderCustom}
      />,
    );
    expect(ref.current).toBe(anchor);
    expect(anchor).toHaveTextContent("Custom 1");
    expect(anchor).not.toHaveAttribute("href");
    rerender(
      <OwningActionTree
        state="enabled"
        anchorRef={ref}
        initialize={initialize}
        onRender={renderCustom}
      />,
    );
    expect(ref.current).toBe(anchor);
    expect(anchor).toHaveTextContent("Custom 1");
    expect(anchor).toHaveAttribute("data-custom", "own-render");
    expect(anchor).toHaveAttribute("href", "/patients/new");
    expect(initialize).toHaveBeenCalledOnce();
    expect(renderCustom).toHaveBeenCalledTimes(4);
  });

  it.each(["enabled", "read_only"] as const)(
    "requires explicit contract instead of ordinary Link in %s",
    (state) => {
      render(
        <MemoryRouter>
          <CapabilityProvider value={{ "write.patient.create": state }}>
            <WriteGate capability="write.patient.create">
              <Link to="/unsafe">Save</Link>
            </WriteGate>
          </CapabilityProvider>
        </MemoryRouter>,
      );
      expect(screen.queryByRole("link")).toBeNull();
      expect(
        screen.getByRole("heading", {
          name: initI18n("th").t("errors.UNSUPPORTED"),
        }),
      ).toBeVisible();
    },
  );

  it("leaves ordinary ungated Router Link render, ref and navigation alone", () => {
    const ref = createRef<HTMLAnchorElement>();
    render(
      <MemoryRouter initialEntries={["/patients"]}>
        <LocationProbe />
        <Link ref={ref} className="ordinary" to="/patients/new">
          Ordinary link
        </Link>
      </MemoryRouter>,
    );
    const link = screen.getByRole("link");
    expect(ref.current).toBe(link);
    expect(link).toHaveClass("ordinary");
    expect(link).toHaveAttribute("href", "/patients/new");
    fireEvent.click(link);
    expect(screen.getByTestId("location")).toHaveTextContent("/patients/new");
  });

  it.each(["th", "en"] as const)(
    "provides actionable %s recovery without retrying writes",
    (locale) => {
      const onWrite = vi.fn();
      render(
        <LocaleProvider initial={locale}>
          <CapabilityProvider
            value={{ "write.patient.create": "error_upstream" }}
          >
            <WriteGate capability="write.patient.create">
              <Button onClick={onWrite}>Save</Button>
            </WriteGate>
          </CapabilityProvider>
        </LocaleProvider>,
      );
      const instruction = initI18n(locale).t("gate.upstreamRecovery");
      expect(screen.getByText(instruction)).toBeVisible();
      expect(screen.getByRole("button")).toHaveAccessibleDescription(
        locale === "th" ? /โหลดหน้านี้ใหม่/ : /Refresh this page/,
      );
      fireEvent.click(screen.getByRole("button"));
      expect(onWrite).not.toHaveBeenCalled();
    },
  );

  it.each([
    null,
    { "write.patient.create": "invalid" },
    { "write.patient.create": true },
  ])("provides visible accessible fallback for malformed map %j", (map) => {
    render(
      <CapabilityProvider value={map as CapabilityMap}>
        <WriteGate capability="write.patient.create">
          <button type="button">Save</button>
        </WriteGate>
      </CapabilityProvider>,
    );
    expect(screen.getByRole("button")).toBeDisabled();
    expect(screen.getByRole("button")).toHaveAccessibleDescription(
      /ยังไม่เปิดใช้งาน/,
    );
    expect(screen.getByText(/ยังไม่เปิดใช้งาน/)).toBeVisible();
  });

  it("never enables an unknown runtime capability name", () => {
    render(
      <CapabilityProvider
        value={{ "write.patient.delete": "enabled" } as CapabilityMap}
      >
        <WriteGate capability={"write.patient.delete" as Capability}>
          <button type="button">Delete</button>
        </WriteGate>
      </CapabilityProvider>,
    );
    expect(screen.getByRole("button")).toBeDisabled();
    expect(screen.getByRole("button")).toHaveAccessibleDescription(
      /ยังไม่เปิดใช้งาน/,
    );
  });

  it.each([
    ["read_only", "gate.readOnly"],
    ["blocked_unverified", "gate.blockedUnverified"],
    ["error_upstream", "errors.UPSTREAM_UNAVAILABLE"],
    ["denied", "gate.roleTitle"],
  ] as const)("uses i18next catalogue key for %s", (state, key) => {
    const i18n = initI18n("en", {
      th: {},
      en: { [key]: "Catalogue-owned message" },
    });
    render(
      <I18nextProvider i18n={i18n}>
        <CapabilityProvider value={{ "write.patient.create": state }}>
          <WriteGate capability="write.patient.create">
            <button type="button">Save</button>
          </WriteGate>
        </CapabilityProvider>
      </I18nextProvider>,
    );
    expect(screen.getByText("Catalogue-owned message")).toBeVisible();
  });

  it.each(["th", "en"] as const)(
    "has paired non-empty gate catalogue entries in %s",
    (locale) => {
      const i18n = initI18n(locale);
      for (const key of [
        "gate.readOnly",
        "gate.blockedUnverified",
        "gate.roleTitle",
        "errors.UPSTREAM_UNAVAILABLE",
        "gate.upstreamRecovery",
      ]) {
        expect(i18n.getResource(locale, "translation", key)).toEqual(
          expect.any(String),
        );
        expect(i18n.getResource(locale, "translation", key).trim()).not.toBe(
          "",
        );
        expect(i18n.t(key)).not.toBe(key);
      }
    },
  );
});
