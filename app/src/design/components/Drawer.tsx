import { useEffect, useId, useRef } from "react";
import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";

type DrawerProps = {
  title: string;
  onClose: () => void;
  children: ReactNode;
  /** Drawer-level actions, e.g. Save / Cancel. */
  footer?: ReactNode;
  /** Mount the drawer open: the component owns `showModal()` and focus. */
  open?: boolean;
};

/**
 * Modal drawer built on `<dialog>`: the platform supplies the backdrop, focus
 * trap, inert background and Escape handling, and focus returns to whatever
 * opened it. Escape and backdrop clicks route through `onClose` so the owner
 * controls state instead of the element hiding itself.
 */
export function Drawer({
  title,
  onClose,
  children,
  footer,
  open = true,
}: DrawerProps) {
  const { t } = useTranslation();
  const titleId = useId();
  const dialogRef = useRef<HTMLDialogElement>(null);
  const closeRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog || !open) return;
    const previous =
      document.activeElement instanceof HTMLElement
        ? document.activeElement
        : null;
    if (!dialog.open) dialog.showModal();
    closeRef.current?.focus();
    return () => {
      // jsdom installs a partial `<dialog>`; never assume every method exists.
      if (dialog.open && typeof dialog.close === "function") dialog.close();
      if (previous?.isConnected) previous.focus();
    };
  }, [open]);

  return (
    <dialog
      ref={dialogRef}
      className="pc-drawer"
      aria-labelledby={titleId}
      onCancel={(event) => {
        event.preventDefault();
        onClose();
      }}
      onClick={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <div className="pc-drawer__head">
        <h2 className="pc-drawer__title" id={titleId}>
          {title}
        </h2>
        <button
          ref={closeRef}
          type="button"
          className="pc-button pc-button--ghost pc-button--sm"
          aria-label={t("action.close")}
          onClick={onClose}
        >
          {t("action.close")}
        </button>
      </div>
      <div className="pc-drawer__body">{children}</div>
      {footer && <div className="pc-drawer__foot">{footer}</div>}
    </dialog>
  );
}
