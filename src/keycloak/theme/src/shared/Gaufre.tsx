import { useEffect, useRef, type MouseEvent } from "react";
import type { Lagaufre } from "./settings";

declare global {
  interface Window {
    _lasuite_widget?: unknown[];
  }
}

type Props = Lagaufre & {
  label: string;
  closeLabel: string;
};

const DIALOG_WIDTH = 340;
const DIALOG_GAP = 10;
const MENU_MODAL_SELECTOR = ".fr-header__menu";
const CLOSED_EVENT = "lasuite-widget-lagaufre-closed";
const INITIALIZED_EVENT = "lasuite-widget-lagaufre-initialized";
const DESKTOP_BUTTON_SELECTOR = ".fr-header__tools-links .lst-gaufre-btn";

function pushCommand(command: unknown[]) {
  window._lasuite_widget = window._lasuite_widget ?? [];
  window._lasuite_widget.push(command);
}

function loadScript(src: string) {
  const href = new URL(src).href;
  if (Array.from(document.scripts).some((script) => script.src === href)) {
    return;
  }
  const script = document.createElement("script");
  script.async = true;
  script.src = href;
  // A failed script stays in document.scripts, so remove it to allow a retry.
  script.onerror = () => script.remove();
  document.head.appendChild(script);
}

// A hidden button has an empty rect, so the dialog goes to the top right.
function positionDialog(button: HTMLElement) {
  const rect = button.getBoundingClientRect();
  const style: Record<string, string | number> = { position: "fixed" };
  if (rect.width === 0) {
    style.top = DIALOG_GAP;
    style.right = DIALOG_GAP;
    return style;
  }
  if (rect.top > window.innerHeight / 2) {
    style.bottom = window.innerHeight - rect.top + DIALOG_GAP;
  } else {
    style.top = rect.bottom + DIALOG_GAP;
  }
  if (rect.right >= DIALOG_WIDTH) {
    style.right = window.innerWidth - rect.right;
  } else {
    style.left = rect.left;
  }
  return style;
}

// Same contract as LaGaufreV2 of @gouvfr-lasuite/ui-components, which needs
// React 19 and cannot load in this React 18 theme. The widget script runs
// the commands queued on window._lasuite_widget once it loads.
//
// The DSFR header renders the quick access list twice: in the desktop bar
// and in the mobile menu modal. Only the desktop copy starts the widget,
// because two instances answer the same document events. The mobile copy
// closes the modal, which covers the widget dialog, then clicks the desktop copy.
export function Gaufre({ apiUrl, widgetPath, label, closeLabel }: Props) {
  const buttonRef = useRef<HTMLButtonElement>(null);
  const readyRef = useRef(false);
  const openRequestedRef = useRef(false);

  useEffect(() => {
    const button = buttonRef.current;
    if (!button || button.closest(MENU_MODAL_SELECTOR)) return;
    pushCommand([
      "lagaufre",
      "init",
      {
        api: apiUrl,
        label,
        closeLabel,
        buttonElement: button,
        position: () => positionDialog(button),
      },
    ]);
    loadScript(widgetPath);
    const onInitialized = () => {
      readyRef.current = true;
      if (!openRequestedRef.current) return;
      openRequestedRef.current = false;
      pushCommand(["lagaufre", "open"]);
    };
    // On close the widget focuses the desktop button, which is hidden on a
    // mobile viewport, so the focus goes back to the menu button instead.
    const onClosed = () => {
      if (button.getBoundingClientRect().width > 0) return;
      document
        .querySelector<HTMLButtonElement>(".fr-header .fr-btn--menu")
        ?.focus();
    };
    document.addEventListener(CLOSED_EVENT, onClosed);
    document.addEventListener(INITIALIZED_EVENT, onInitialized);
    return () => {
      document.removeEventListener(CLOSED_EVENT, onClosed);
      document.removeEventListener(INITIALIZED_EVENT, onInitialized);
      readyRef.current = false;
      pushCommand(["lagaufre", "destroy"]);
    };
  }, [apiUrl, widgetPath, label, closeLabel]);

  const onClick = (event: MouseEvent<HTMLButtonElement>) => {
    const modal = event.currentTarget.closest(MENU_MODAL_SELECTOR);
    if (modal) {
      modal.querySelector<HTMLButtonElement>(".fr-btn--close")?.click();
      document
        .querySelector<HTMLButtonElement>(DESKTOP_BUTTON_SELECTOR)
        ?.click();
      return;
    }
    if (readyRef.current) return;
    // The widget binds the click after its API call, so the click waits for
    // the initialized event. The load runs again after a failed request.
    openRequestedRef.current = true;
    loadScript(widgetPath);
  };

  return (
    <button
      type="button"
      className="fr-btn lst-gaufre-btn"
      ref={buttonRef}
      title={label}
      onClick={onClick}
    >
      <svg
        xmlns="http://www.w3.org/2000/svg"
        width="24"
        height="24"
        fill="none"
        viewBox="0 0 18 18"
        aria-hidden="true"
      >
        <defs>
          <path
            id="lst-gaufre-cell"
            fill="currentColor"
            d="M2.796.5c.469 0 .704 0 .892.064.351.12.627.397.748.748.064.188.064.423.064.892v.592c0 .469 0 .704-.064.892-.12.351-.397.627-.748.748-.188.064-.423.064-.892.064h-.592c-.469 0-.704 0-.892-.064a1.2 1.2 0 0 1-.748-.748C.5 3.5.5 3.265.5 2.796v-.592c0-.469 0-.704.064-.892.12-.351.397-.627.748-.748C1.5.5 1.735.5 2.204.5z"
          />
        </defs>
        <use href="#lst-gaufre-cell" />
        <use href="#lst-gaufre-cell" transform="translate(6.5)" />
        <use href="#lst-gaufre-cell" transform="translate(13)" />
        <use href="#lst-gaufre-cell" transform="translate(0 6.5)" />
        <use href="#lst-gaufre-cell" transform="translate(6.5 6.5)" />
        <use href="#lst-gaufre-cell" transform="translate(13 6.5)" />
        <use href="#lst-gaufre-cell" transform="translate(0 13)" />
        <use href="#lst-gaufre-cell" transform="translate(6.5 13)" />
        <use href="#lst-gaufre-cell" transform="translate(13 13)" />
      </svg>
      <span className="lst-gaufre-btn__label">{label}</span>
    </button>
  );
}
