import { useEffect } from "react";

// react-modal tags every open modal's content with `--after-open` and mounts
// each modal in a `.ReactModalPortal` appended directly under <body>. We rely on
// both: the portal lets us see a modal being removed via a (cheap) body-level
// childList observer, the `--after-open` class lets us tell which modals are
// still open (and find the one on top).
const OPEN_MODAL_SELECTOR = ".ReactModal__Content--after-open";

/**
 * Restores keyboard focus to a Cunningham modal when a modal stacked above it
 * closes.
 *
 * react-modal is supposed to hand focus back to the opener on close
 * (`shouldReturnFocusAfterClose`), but with stacked modals the restore is
 * unreliable and focus ends up stranded on `<body>`. A modal with no focus is a
 * dead end for keyboard and screen-reader users, and it also kills Escape, whose
 * handler react-modal binds to the (now unfocused) modal content.
 *
 * While `isOpen`, this watches for a stacked modal being unmounted and, only when
 * focus was left outside every modal, moves it into the topmost remaining one —
 * mirroring react-modal's own open-time `focusContent`. Leaving focus untouched
 * whenever react-modal restored it correctly keeps this a safety net, not an
 * override.
 */
export const useRestoreFocusOnNestedModalClose = (isOpen: boolean) => {
  useEffect(() => {
    if (!isOpen) {
      return;
    }

    let openModalCount = document.querySelectorAll(OPEN_MODAL_SELECTOR).length;
    let rafId = 0;

    const observer = new MutationObserver(() => {
      const previousCount = openModalCount;
      openModalCount = document.querySelectorAll(OPEN_MODAL_SELECTOR).length;

      // Only react when a stacked modal was just removed, leaving at least one
      // modal still open to hand focus back to.
      if (openModalCount >= previousCount || openModalCount === 0) {
        return;
      }

      // Defer past react-modal's own focus restoration, then step in only if it
      // failed and left focus stranded outside every modal.
      cancelAnimationFrame(rafId);
      rafId = requestAnimationFrame(() => {
        const openModals =
          document.querySelectorAll<HTMLElement>(OPEN_MODAL_SELECTOR);
        const activeElement = document.activeElement;

        // react-modal already restored focus to a still-open modal: leave it.
        // We only step in when focus is stranded outside every open modal (e.g.
        // on `<body>`, or inside a modal still mid-close-animation).
        if (
          activeElement &&
          Array.from(openModals).some((modal) => modal.contains(activeElement))
        ) {
          return;
        }
        openModals[openModals.length - 1]?.focus({ preventScroll: true });
      });
    });

    // react-modal portals are direct children of <body>, so a shallow childList
    // observer is enough to catch a nested modal mounting or unmounting.
    observer.observe(document.body, { childList: true });

    return () => {
      observer.disconnect();
      cancelAnimationFrame(rafId);
    };
  }, [isOpen]);
};
