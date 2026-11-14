import {
  cloneElement,
  forwardRef,
  type HTMLAttributes,
  type KeyboardEvent,
  type ReactElement,
  type SyntheticEvent,
  useContext,
  useId,
} from "react";
import { I18nContext, useTranslation } from "react-i18next";
import { type LinkProps, useHref, useLinkClickHandler } from "react-router-dom";
import type { Capability } from "../domain/capability";
import { useCapabilities } from "../features/shell/CapabilityProvider";
import { initI18n } from "../i18n";
import { Button } from "./Button";
import { StatePanel } from "./StatePanel";

const fallbackI18n = initI18n("th");
const reasonKeys = {
  read_only: "gate.readOnly",
  blocked_unverified: "gate.blockedUnverified",
  error_upstream: "errors.UPSTREAM_UNAVAILABLE",
  denied: "gate.roleTitle",
};

type ControlProps = HTMLAttributes<HTMLElement> & {
  disabled?: boolean;
  href?: string;
  to?: unknown;
};

function blockActivation(event: SyntheticEvent) {
  event.preventDefault();
  event.stopPropagation();
}

function blockKeyboardActivation(event: KeyboardEvent<HTMLElement>) {
  if (event.key === "Enter" || event.key === " ") blockActivation(event);
}

export type RoutedControlProps = LinkProps & { disabled?: boolean };

/**
 * Explicit gated-navigation contract. Use directly beneath WriteGate; compose
 * custom stateful/ref-bearing content inside it, not an arbitrary link wrapper.
 * Disabled removes href without replacing component, anchor or descendants.
 */
export const RoutedControl = forwardRef<HTMLAnchorElement, RoutedControlProps>(
  function RoutedControl(
    {
      to,
      disabled = false,
      replace,
      state,
      target,
      relative,
      preventScrollReset,
      reloadDocument,
      viewTransition,
      discover: _discover,
      prefetch: _prefetch,
      onClick,
      onAuxClick,
      onContextMenu,
      onKeyDown,
      ...props
    },
    ref,
  ) {
    const href = useHref(to, { relative });
    const navigate = useLinkClickHandler<HTMLAnchorElement>(to, {
      replace,
      state,
      target,
      relative,
      preventScrollReset,
      viewTransition,
    });

    return (
      <a
        {...props}
        ref={ref}
        href={disabled ? undefined : href}
        target={target}
        role={disabled ? "link" : props.role}
        aria-disabled={disabled ? true : props["aria-disabled"]}
        tabIndex={disabled ? -1 : props.tabIndex}
        onClick={(event) => {
          if (disabled) return blockActivation(event);
          onClick?.(event);
          if (!event.defaultPrevented && !reloadDocument) navigate(event);
        }}
        onAuxClick={disabled ? blockActivation : onAuxClick}
        onContextMenu={disabled ? blockActivation : onContextMenu}
        onKeyDown={disabled ? blockKeyboardActivation : onKeyDown}
      />
    );
  },
);

function PermissionDenied({ reason }: { reason: string }) {
  return <StatePanel kind="permission-denied" title={reason} />;
}

/**
 * Supported roots: RoutedControl, Button, native button/a/input/select/textarea.
 * Ordinary Router Link and arbitrary custom roots are unsupported and fail
 * closed, even when capability is enabled. Never infer a contract from `to`.
 * Custom routed components may own a WriteGate internally, forwarding their
 * ref to its direct RoutedControl child; their hooks/render then stay intact.
 */
export function WriteGate({
  capability,
  reason,
  children,
}: {
  capability: Capability;
  reason?: string;
  children: ReactElement<ControlProps>;
}) {
  const { get } = useCapabilities();
  const reasonId = useId();
  const context = useContext(I18nContext);
  const { t } = useTranslation(undefined, {
    i18n: context?.i18n ?? fallbackI18n,
  });
  const state = get(capability);
  const routed = children.type === RoutedControl;
  const supported =
    routed ||
    children.type === Button ||
    (typeof children.type === "string" &&
      ["button", "a", "input", "select", "textarea"].includes(children.type));

  if (state === "denied")
    return <PermissionDenied reason={reason ?? t(reasonKeys.denied)} />;
  if (!supported)
    return (
      <StatePanel
        kind="empty"
        title={t("errors.UNSUPPORTED")}
        detail={t("gate.readOnly")}
      />
    );
  if (state === "enabled") return children;

  const isLink = children.type === "a" || routed;
  const description = [children.props["aria-describedby"], reasonId]
    .filter(Boolean)
    .join(" ");
  const disabledProps: ControlProps = {
    "aria-describedby": description,
    "aria-disabled": true,
    onClick: blockActivation,
    onClickCapture: blockActivation,
    onAuxClick: blockActivation,
    onAuxClickCapture: blockActivation,
    onContextMenu: blockActivation,
    onContextMenuCapture: blockActivation,
    onKeyDown: blockKeyboardActivation,
    onKeyDownCapture: blockKeyboardActivation,
  };
  if (isLink) {
    disabledProps.href = undefined;
    disabledProps.role = "link";
    disabledProps.tabIndex = -1;
    disabledProps.style = {
      ...children.props.style,
      color: "var(--muted)",
      cursor: "not-allowed",
    };
    if (routed) disabledProps.disabled = true;
  } else {
    disabledProps.disabled = true;
  }

  return (
    <>
      {cloneElement(children, disabledProps)}
      <span id={reasonId} className="ui-button-reason">
        <span>{reason ?? t(reasonKeys[state])}</span>
        {state === "error_upstream" && (
          <>
            {" "}
            <span>{t("gate.upstreamRecovery")}</span>
          </>
        )}
      </span>
    </>
  );
}
