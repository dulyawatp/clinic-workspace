/**
 * jsdom ships a partial `<dialog>`: `showModal`/`close` are missing, so a test
 * that mounts a `Drawer` installs the two methods it needs. The stub keeps the
 * real `open` attribute state so role/visibility queries stay meaningful.
 */
export function installDialogMethods(): () => void {
  const proto = HTMLDialogElement.prototype;
  const previousShow = Object.getOwnPropertyDescriptor(proto, "showModal");
  const previousClose = Object.getOwnPropertyDescriptor(proto, "close");
  Object.defineProperty(proto, "showModal", {
    configurable: true,
    value: function (this: HTMLDialogElement) {
      this.setAttribute("open", "");
    },
  });
  Object.defineProperty(proto, "close", {
    configurable: true,
    value: function (this: HTMLDialogElement) {
      this.removeAttribute("open");
    },
  });
  return () => {
    if (previousShow) Object.defineProperty(proto, "showModal", previousShow);
    else Reflect.deleteProperty(proto, "showModal");
    if (previousClose) Object.defineProperty(proto, "close", previousClose);
    else Reflect.deleteProperty(proto, "close");
  };
}
