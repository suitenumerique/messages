import { createContext, useContext } from "react";

export type MobileToolbarDrawerApi = {
    /**
     * Element to portal a Drawer into. Sits above the toolbar row inside the
     * fixed bar (styled `display: contents`, so the portaled Drawer joins the
     * bar's flex column directly).
     */
    slot: HTMLElement | null;
    /** Identifier of the currently open child drawer, if any. */
    openId: string | null;
    /**
     * Opens the child drawer `id`: folds the other toolbar surfaces and
     * dismisses the keyboard so the drawer takes its place (same behavior as
     * the "Aa" format panel).
     */
    open: (id: string) => void;
    /** Closes the child drawer and hands focus back to the editor. */
    close: () => void;
};

/**
 * Provided by MobileToolbar around its children. Toolbar extras (template /
 * signature selectors…) use it to detect they render inside the mobile bar —
 * a null context means the desktop toolbar — and to open their content as a
 * bottom drawer with large touch targets instead of desktop popovers.
 */
export const MobileToolbarDrawerContext =
    createContext<MobileToolbarDrawerApi | null>(null);

export const useMobileToolbarDrawer = () =>
    useContext(MobileToolbarDrawerContext);
