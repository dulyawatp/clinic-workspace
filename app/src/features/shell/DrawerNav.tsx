import { useEffect, useRef } from "react";
import { useTranslation } from "react-i18next";
import type { NavItem } from "./nav";
import { NavIcon, ShellBrand, SidebarNav } from "./SidebarNav";

export function DrawerNav({
  items,
  onClose,
}: {
  items: readonly NavItem[];
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const dialogRef = useRef<HTMLDialogElement>(null);
  const closeRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    const dialog = dialogRef.current;
    const previous =
      document.activeElement instanceof HTMLElement
        ? document.activeElement
        : null;
    const overflow = document.body.style.overflow;
    dialog?.showModal();
    document.body.style.overflow = "hidden";
    closeRef.current?.focus();
    return () => {
      dialog?.close();
      document.body.style.overflow = overflow;
      if (previous?.isConnected) previous.focus();
    };
  }, []);

  return (
    <dialog
      ref={dialogRef}
      id="shell-drawer"
      className="shell-drawer"
      aria-label={t("nav.primary")}
      onCancel={(event) => {
        event.preventDefault();
        onClose();
      }}
      onClick={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
      onKeyDown={(event) => {
        if (event.key === "Escape") {
          event.preventDefault();
          onClose();
          return;
        }
        if (event.key !== "Tab") return;
        const focusable = Array.from(
          event.currentTarget.querySelectorAll<HTMLElement>(
            "button:not([disabled]), a[href], summary",
          ),
        ).filter(
          (element) =>
            element.tagName === "SUMMARY" ||
            !element.closest("details:not([open])"),
        );
        const first = focusable[0];
        const last = focusable[focusable.length - 1];
        if (event.shiftKey && document.activeElement === first) {
          event.preventDefault();
          last?.focus();
        }
        if (!event.shiftKey && document.activeElement === last) {
          event.preventDefault();
          first?.focus();
        }
      }}
    >
      <div className="shell-drawer-heading">
        <ShellBrand />
        <button
          ref={closeRef}
          type="button"
          className="shell-icon-control"
          aria-label={t("action.close")}
          onClick={onClose}
        >
          <NavIcon name="close" />
        </button>
      </div>
      <SidebarNav variant="drawer" items={items} onNavigate={onClose} />
    </dialog>
  );
}
