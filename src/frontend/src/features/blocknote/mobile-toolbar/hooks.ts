import {
    BlockNoteEditor,
    DefaultBlockSchema,
    DefaultInlineContentSchema,
    DefaultStyleSchema,
} from "@blocknote/core";
import { useCallback, useEffect, useState } from "react";

import { NATIVE_LINK_TAP_EVENT } from "../utils";

/**
 * What `useBlockNoteEditor()` returns without explicit generics. The
 * composers use extended schemas, but the mobile toolbar only relies on
 * default blocks/styles, so the default typing is both accurate enough
 * and the least cast-heavy.
 */
export type ComposerEditor = BlockNoteEditor<
    DefaultBlockSchema,
    DefaultInlineContentSchema,
    DefaultStyleSchema
>;

/**
 * Where the document focus currently sits, relative to a composer:
 * - "composer": inside the editor, the fixed toolbar or one of their popovers
 *   — the toolbar is in active use.
 * - "outside": some other focusable took over (subject field, search…) — the
 *   toolbar must fold entirely.
 * - "none": focus was dropped without a successor, i.e. the on-screen
 *   keyboard was dismissed. The "Aa" format panel deliberately puts the
 *   composer in this state (it replaces the keyboard, Apple-Notes style), so
 *   it is distinct from "outside".
 */
export type ComposerFocusState = "composer" | "outside" | "none";

/**
 * The editor's ProseMirror DOM element, or null while the view is not
 * mounted. The toolbar renders before BlockNoteView's effect mounts the
 * view, and tiptap wraps the missing view in a Proxy that throws on any
 * property access — so probing it is the only reliable check.
 */
const getEditorDom = (editor: ComposerEditor): HTMLElement | null => {
    try {
        return editor._tiptapEditor.view.dom as HTMLElement;
    } catch {
        return null;
    }
};

/**
 * Tracks focus around a composer so the mobile keyboard toolbar can decide
 * when to show. Focus is considered "kept" as long as it stays inside the
 * editor's container — which also holds the toolbar — so tapping a toolbar
 * button doesn't tear it down.
 */
export const useComposerFocusState = (
    editor: ComposerEditor,
): ComposerFocusState => {
    const classify = useCallback(
        (node: EventTarget | null): ComposerFocusState => {
            if (!(node instanceof Node)) return "outside";
            // Before the view is mounted, focus cannot be inside the editor;
            // fall through to the portaled-pieces check.
            const editorEl = getEditorDom(editor);
            const container = editorEl
                ? (editorEl.closest(".bn-container") ?? editorEl)
                : null;
            if (container?.contains(node)) return "composer";
            // The fixed toolbar and BlockNote's popovers (mantine dropdowns:
            // template/signature selectors, file caption…) portal outside the
            // editor container; keep the toolbar alive while focus is in any
            // of them.
            const isPortaledToolbarPiece =
                node instanceof Element &&
                !!node.closest('.bn-mobile-formatting-toolbar, [class*="mantine-"][class*="-dropdown"]');
            return isPortaledToolbarPiece ? "composer" : "outside";
        },
        [editor],
    );

    // Seeded from the current focus: the composer may have autofocused
    // before the effect below subscribes.
    const [focusState, setFocusState] = useState<ComposerFocusState>(() =>
        document.activeElement && document.activeElement !== document.body
            ? classify(document.activeElement)
            : "none",
    );

    useEffect(() => {
        const onFocusIn = (event: FocusEvent) => {
            setFocusState(classify(event.target));
        };
        const onFocusOut = (event: FocusEvent) => {
            // No successor: focus was dropped entirely (keyboard dismissed or
            // programmatic blur). When a successor exists, the paired focusin
            // classifies it precisely.
            if (event.relatedTarget === null) setFocusState("none");
        };
        document.addEventListener("focusin", onFocusIn);
        document.addEventListener("focusout", onFocusOut);
        return () => {
            document.removeEventListener("focusin", onFocusIn);
            document.removeEventListener("focusout", onFocusOut);
        };
    }, [classify]);

    return focusState;
};

/**
 * Turns a tap on a link inside the composer into an edit action. Navigation
 * itself is neutralized at editor creation (`links.onClick`, see
 * createNativeLinkOptions): BlockNote's default handler `window.open`s the
 * href from a ProseMirror `handleClick`, out of reach of DOM interception.
 * That handler places the caret at the tap position (ProseMirror skips its
 * own placement for consumed clicks) and re-emits the tap as
 * NATIVE_LINK_TAP_EVENT — the reliable signal on touch — with a plain click
 * listener as fallback (e.g. tap on a link while the ProseMirror click
 * handling is bypassed). Either way the cursor sits inside the link when the
 * link editor mounts, so it opens prefilled with the tapped URL.
 *
 * Document-level listeners with lazy DOM resolution: this effect runs before
 * BlockNoteView's own effect mounts the ProseMirror view, and the contains()
 * check scopes each toolbar to its own editor.
 */
export const useEditLinkOnTap = (
    editor: ComposerEditor,
    openLinkEditor: () => void,
) => {
    useEffect(() => {
        const onLinkTap = (event: Event) => {
            if (!(event.target instanceof Element)) return;
            const link = event.target.closest("a");
            if (!link) return;
            const editorEl = getEditorDom(editor);
            if (!editorEl?.contains(link)) return;
            event.preventDefault();
            if (editor.isEditable) openLinkEditor();
        };
        document.addEventListener(NATIVE_LINK_TAP_EVENT, onLinkTap, true);
        document.addEventListener("click", onLinkTap, true);
        return () => {
            document.removeEventListener(NATIVE_LINK_TAP_EVENT, onLinkTap, true);
            document.removeEventListener("click", onLinkTap, true);
        };
    }, [editor, openLinkEditor]);
};

/**
 * While a panel or drawer replaces the keyboard, taps in the text must move
 * the caret without resummoning it: inputmode="none" keeps real focus on the
 * editor (visible caret, live selection) but tells the OS not to show a
 * keyboard.
 */
export const useKeyboardSuppression = (
    editor: ComposerEditor,
    active: boolean,
) => {
    useEffect(() => {
        if (!active) return;
        // No unmounted-view case to handle: a panel can only open from the
        // toolbar, which requires a focused (thus mounted) editor.
        const dom = getEditorDom(editor);
        if (!dom) return;
        dom.setAttribute("inputmode", "none");
        return () => dom.removeAttribute("inputmode");
    }, [editor, active]);
};

/**
 * Hands the editor back to typing after a keyboard-suppressing panel closes.
 * inputmode must be restored before refocusing (the close handler runs ahead
 * of the suppression effect's cleanup), and a still-focused editor must be
 * blurred first — focus() on an already-focused element is a no-op and would
 * leave the keyboard hidden.
 */
export const releaseKeyboardToEditor = (editor: ComposerEditor) => {
    const dom = getEditorDom(editor);
    if (dom) {
        dom.removeAttribute("inputmode");
        if (
            document.activeElement instanceof HTMLElement &&
            dom.contains(document.activeElement)
        ) {
            document.activeElement.blur();
        }
    }
    editor.focus();
};

/**
 * Keeps the editor focused while tapping around the bar: stolen focus would
 * collapse the selection a command applies to, close the keyboard and tear
 * the toolbar down. A native capture-phase listener (not a React prop) so it
 * runs before the children's own handlers — stopPropagation neutralizes
 * BlockNote's Safari workaround that force-focuses mantine triggers on
 * mousedown. Text fields (link URL…) legitimately take focus and are exempt.
 */
export const useKeepEditorFocusOnTap = () =>
    useCallback((bar: HTMLDivElement | null) => {
        if (!bar) return;
        const onMouseDown = (event: MouseEvent) => {
            if (
                event.target instanceof Element &&
                !event.target.closest("input, textarea, [contenteditable]")
            ) {
                event.preventDefault();
                event.stopPropagation();
            }
        };
        bar.addEventListener("mousedown", onMouseDown, true);
        return () => bar.removeEventListener("mousedown", onMouseDown, true);
    }, []);
