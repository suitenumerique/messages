import { createContext, useContext, useEffect } from "react";

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
    /**
     * Forgets `id` if it is the open drawer, without touching the keyboard —
     * the exit of an owner that unmounts while its drawer is up.
     */
    release: (id: string) => void;
};

/**
 * Provided by MobileToolbar around its children. Toolbar extras (template /
 * signature selectors…) use it to detect they render inside the mobile bar —
 * a null context means the desktop toolbar — and to open their content as a
 * bottom drawer with large touch targets instead of desktop popovers.
 */
export const MobileToolbarDrawerContext =
    createContext<MobileToolbarDrawerApi | null>(null);

/**
 * Owner-side binding of the child drawer `id`. The bar swaps its extras
 * (a selected file block replaces them with the file buttons), so an owner
 * can unmount with its drawer open: nothing would render the drawer anymore,
 * yet the bar would stay visible and keep the keyboard suppressed for it.
 */
export const useMobileToolbarChildDrawer = (id: string) => {
    const api = useContext(MobileToolbarDrawerContext);
    const release = api?.release;
    useEffect(() => () => release?.(id), [release, id]);
    return api;
};
