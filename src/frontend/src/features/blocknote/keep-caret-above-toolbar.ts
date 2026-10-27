import type { BlockNoteEditor } from "@blocknote/core";
import { useBlockNoteEditor } from "@blocknote/react";
import { useEffect } from "react";

/**
 * Room kept between the caret and the top of the surface it would slip under.
 */
export const CARET_TOOLBAR_MARGIN = 16;

/** ProseMirror's own default `scrollMargin`, kept on the other sides. */
const PROSEMIRROR_SCROLL_MARGIN = 5;

const PINNED_POSITIONS = new Set(["sticky", "fixed"]);

/**
 * Surfaces a bar anchors to the viewport instead of laying them out in its
 * own box: the drawers of the native bar (format panel, template / signature
 * selectors) are `position: fixed` and leave the bar itself empty while they
 * cover the bottom of the screen (see mobile-toolbar/_index.scss).
 */
const ANCHORED_SURFACE_SELECTOR = ".drawer";

/**
 * Height of the band `surface` paints over the bottom of its scrollport: its
 * box plus the `bottom` offset it sticks at. Nothing for a surface laid out
 * in the flow, which covers nothing.
 */
const getPinnedHeight = (surface: HTMLElement): number => {
    const { position, bottom } = getComputedStyle(surface);
    if (!PINNED_POSITIONS.has(position)) return 0;
    return surface.offsetHeight + (parseFloat(bottom) || 0);
};

/**
 * Reserves `height` px over the bottom edge of every scroller for ProseMirror's
 * scroll-into-view, so the selection lands above the surfaces painted there.
 * Handed to Tiptap's editorProps rather than straight to the view: BlockNote
 * recreates the view when the editor toggles `editable`, and Tiptap builds
 * the new one from these props.
 */
export const setPinnedBottom = (editor: BlockNoteEditor, height: number): void => {
    const tiptap = editor._tiptapEditor;
    const bottom = height + CARET_TOOLBAR_MARGIN;
    // Every side is needed: ProseMirror reads `value[side]` and an undefined
    // side disables that edge's check altogether. Without a pinned surface,
    // the props go back to ProseMirror's defaults.
    const scroll = height > 0
        ? {
            scrollThreshold: { top: 0, right: 0, bottom, left: 0 },
            scrollMargin: {
                top: PROSEMIRROR_SCROLL_MARGIN,
                right: PROSEMIRROR_SCROLL_MARGIN,
                bottom,
                left: PROSEMIRROR_SCROLL_MARGIN,
            },
        }
        : { scrollThreshold: undefined, scrollMargin: undefined };
    tiptap.setOptions({ editorProps: { ...tiptap.options.editorProps, ...scroll } });
};

/**
 * Reports the height `bar` and the surfaces anchored from it pin over the
 * bottom of the screen, now and whenever they resize, move, mount or unmount.
 * Returns the function that stops watching.
 */
export const observePinnedBottom = (bar: HTMLElement, onChange: (height: number) => void): () => void => {
    let surfaces: HTMLElement[] = [];
    const resizeObserver = new ResizeObserver(() => {
        onChange(Math.max(...surfaces.map(getPinnedHeight)));
    });
    // Observing an element reports its size right away, which measures the
    // whole set again.
    const observe = () => {
        resizeObserver.disconnect();
        surfaces = [bar, ...bar.querySelectorAll<HTMLElement>(ANCHORED_SURFACE_SELECTOR)];
        surfaces.forEach((surface) => resizeObserver.observe(surface));
    };
    const mutationObserver = new MutationObserver(observe);
    mutationObserver.observe(bar, { childList: true, subtree: true });
    // The bar's sticky offset follows custom properties its form sets inline
    // (--form-footer-height, see message-form/index.tsx): a change there
    // moves the bar without resizing it.
    const form = bar.closest(".message-form");
    if (form) mutationObserver.observe(form, { attributes: true, attributeFilter: ["style"] });
    observe();

    return () => {
        mutationObserver.disconnect();
        resizeObserver.disconnect();
    };
};

/**
 * Keeps the caret in sight while typing above a formatting toolbar pinned
 * over the editor: the desktop one sticks to the bottom of the scrolling
 * composer (see message-form/_index.scss), the native one is a fixed bar
 * portaled to <body> (see mobile-toolbar).
 *
 * ProseMirror scrolls the selection into its scroller's bounding box, but
 * knows nothing of what is painted over it: once a long message makes the
 * composer scroll, each new line lands behind the toolbar and the user has
 * to scroll by hand to see what they type. ProseMirror's `scrollThreshold`
 * and `scrollMargin` props shrink that box from the bottom; the bar's height
 * varies (it wraps in narrow windows, the native drawers replace it), so it
 * is measured whenever it changes, never at scroll time.
 */
export const useKeepCaretAboveToolbar = (bar: HTMLElement | null): void => {
    const editor = useBlockNoteEditor();
    useEffect(() => {
        if (!bar) return;
        const stop = observePinnedBottom(bar, (height) => setPinnedBottom(editor, height));
        return () => {
            stop();
            setPinnedBottom(editor, 0);
        };
    }, [editor, bar]);
};
