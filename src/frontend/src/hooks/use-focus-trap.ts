import { RefObject, useEffect } from "react";

const TABBABLE_SELECTOR = [
    "a[href]",
    "button:not([disabled])",
    'input:not([disabled]):not([type="hidden"])',
    "select:not([disabled])",
    "textarea:not([disabled])",
    '[contenteditable="true"]',
    '[tabindex]:not([tabindex="-1"])',
].join(",");

/**
 * Tabbable descendants in DOM order. `hidden` subtrees are left out; CSS-only
 * hiding has no layout signal without a browser, so `focusFirst` below deals
 * with it by trying candidates until one takes the focus.
 */
const getTabbables = (root: HTMLElement) =>
    Array.from(root.querySelectorAll<HTMLElement>(TABBABLE_SELECTOR)).filter(
        (element) => element.closest("[hidden]") === null,
    );

const focusFirst = (candidates: HTMLElement[]) => {
    for (const candidate of candidates) {
        candidate.focus();
        if (document.activeElement === candidate) return;
    }
};

/**
 * Keeps Tab and Shift+Tab cycling inside `ref` while `active`: leaving by the
 * last tabbable wraps to the first, and vice versa.
 *
 * Deliberately a Tab-only trap, not a focus fence: it never pulls the focus
 * back on `focusin`. The surfaces using it (compose window, mobile overview)
 * open popovers and modals portaled outside their subtree — BlockNote's
 * Mantine menus, Cunningham modals — which must keep the focus while open
 * and run their own trap. A native listener on the root, so a key pressed in
 * one of those portals (not a DOM descendant) is never seen here.
 */
export const useFocusTrap = (ref: RefObject<HTMLElement | null>, active: boolean) => {
    useEffect(() => {
        const root = ref.current;
        if (!active || !root) return;

        const onKeyDown = (event: KeyboardEvent) => {
            if (event.key !== "Tab" || event.defaultPrevented) return;
            const tabbables = getTabbables(root);
            if (tabbables.length === 0) {
                event.preventDefault();
                return;
            }
            const index = tabbables.indexOf(document.activeElement as HTMLElement);
            if (event.shiftKey) {
                // Also when the root itself holds the focus (index -1): the
                // browser would leave by the top.
                if (index > 0) return;
                event.preventDefault();
                focusFirst([...tabbables].reverse());
            } else {
                if (index !== tabbables.length - 1) return;
                event.preventDefault();
                focusFirst(tabbables);
            }
        };

        root.addEventListener("keydown", onKeyDown);
        return () => root.removeEventListener("keydown", onKeyDown);
    }, [ref, active]);
};
