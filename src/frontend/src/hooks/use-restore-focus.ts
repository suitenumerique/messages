import { useLayoutEffect } from "react";

/**
 * Hands the focus back to the element that had it when the surface mounted
 * (its opener) once the surface unmounts — unless something else claimed the
 * focus meanwhile, or the opener is gone.
 *
 * The check runs in a microtask after the commit: by then the surface's DOM
 * is detached (the focus it held has dropped to `body`), and any effect of
 * the same commit that focuses elsewhere — a compose sheet grabbing the caret
 * as the overview closes — has already run, so its focus is left alone.
 */
export const useRestoreFocus = () => {
    useLayoutEffect(() => {
        const active = document.activeElement;
        const opener =
            active instanceof HTMLElement && active !== document.body ? active : null;
        if (!opener) return;

        return () => {
            queueMicrotask(() => {
                if (!opener.isConnected) return;
                const current = document.activeElement;
                const focusDropped =
                    !current || current === document.body || !current.isConnected;
                if (focusDropped) opener.focus();
            });
        };
    }, []);
};
