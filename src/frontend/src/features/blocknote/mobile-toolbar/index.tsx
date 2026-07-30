import {
    FileCaptionButton,
    FileDeleteButton,
    FilePreviewButton,
    FileReplaceButton,
    FormattingToolbar,
    useBlockNoteEditor,
    useEditorState,
} from "@blocknote/react";
import { useCallback, useMemo, useState } from "react";
import { createPortal } from "react-dom";
import { useTranslation } from "react-i18next";
import { RiBold, RiItalic, RiLink, RiUnderline } from "react-icons/ri";

import { Drawer } from "@/features/ui/components/drawer";
import { ToolbarSeparator } from "../toolbar-separator";
import {
    BlockTypeButton,
    MobileToolbarButton,
    StyleToggleButton,
    useAvailableBlockTypeItems,
} from "./buttons";
import { MobileToolbarDrawerContext } from "./drawer-context";
import { MobileFormatPanel } from "./format-panel";
import {
    releaseKeyboardToEditor,
    useComposerFocusState,
    useEditLinkOnTap,
    useKeepEditorFocusOnTap,
    useKeyboardSuppression,
} from "./hooks";
import { MobileLinkEditor } from "./link-editor";
import { Icon } from "@/features/ui/components/icon";
import { IconSize } from "@gouvfr-lasuite/ui-kit";
import { FormatText, KeyboardHide } from "@gouvfr-lasuite/ui-kit/icons";

type MobileToolbarProps = {
    children?: React.ReactNode;
};

type MobileToolbarView = "toolbar" | "format" | "link";

/** Dismisses the on-screen keyboard by dropping the DOM focus. */
const dismissKeyboard = () => {
    if (document.activeElement instanceof HTMLElement) {
        document.activeElement.blur();
    }
};

/**
 * The composer toolbar for the native app, pinned right above the on-screen
 * keyboard while the composer is focused (positioning in _index.scss: the
 * webview is resized by the keyboard, so a fixed bottom bar lands on top of
 * it). A short row of large touch targets for the most frequent actions;
 * everything else lives in the "Aa" format panel.
 *
 * Not BlockNote's ExperimentalMobileFormattingToolbarController: it only
 * renders live React while text is *selected* and serves a frozen
 * dangerouslySetInnerHTML snapshot otherwise, which freezes loading states
 * and dead-ends every button.
 */
export const MobileToolbar = ({ children }: MobileToolbarProps) => {
    const { t } = useTranslation();
    const editor = useBlockNoteEditor();
    const focusState = useComposerFocusState(editor);
    const keepEditorFocusOnTap = useKeepEditorFocusOnTap();
    const [view, setView] = useState<MobileToolbarView>("toolbar");
    // Child drawers: toolbar extras (template / signature selectors…) open
    // their content as a bottom drawer through MobileToolbarDrawerContext,
    // portaled into a slot that sits above the toolbar row.
    const [childDrawerId, setChildDrawerId] = useState<string | null>(null);
    const [drawerSlot, setDrawerSlot] = useState<HTMLDivElement | null>(null);

    const blockItems = useAvailableBlockTypeItems();
    const bulletListItem = blockItems.find((item) => item.type === "bulletListItem");
    const quoteItem = blockItems.find((item) => item.type === "quote");
    const hasLink = useEditorState({
        editor,
        selector: ({ editor }) => editor.getSelectedLinkUrl() !== undefined,
    });
    // Tapping a link edits it instead of navigating (the default link
    // toolbar is disabled on native, see BlockNoteViewField).
    const openLinkEditor = useCallback(() => setView("link"), []);
    useEditLinkOnTap(editor, openLinkEditor);
    // Panels replace the keyboard: while one is open, tapping in the text
    // moves the caret but must not resummon the keyboard.
    useKeyboardSuppression(editor, view === "format" || childDrawerId !== null);

    const drawerApi = useMemo(
        () => ({
            slot: drawerSlot,
            openId: childDrawerId,
            open: (id: string) => {
                setView("toolbar");
                setChildDrawerId(id);
                // Apple-Notes style, like the format panel: the drawer takes
                // the keyboard's place.
                dismissKeyboard();
            },
            close: () => {
                setChildDrawerId(null);
                // Closing hands back to typing, keyboard included.
                releaseKeyboardToEditor(editor);
            },
        }),
        [drawerSlot, childDrawerId, editor],
    );

    // React to focus moves (state-from-previous-render pattern, not an
    // effect: https://react.dev/learn/you-might-not-need-an-effect):
    // - focus taken by something else on the page → fold everything;
    // - keyboard dismissed while the link editor is open → plain bar again.
    // Moving the caret in the text deliberately does NOT fold the format
    // panel or the child drawers: they stay open above the returning
    // keyboard, and close through their own Drawer chrome.
    const [prevFocusState, setPrevFocusState] = useState(focusState);
    if (prevFocusState !== focusState) {
        setPrevFocusState(focusState);
        if (focusState !== "composer" && view === "link") setView("toolbar");
        if (focusState === "outside" && view === "format") setView("toolbar");
        // Child drawers follow the same rules as the format panel.
        if (focusState === "outside" && childDrawerId !== null) setChildDrawerId(null);
    }

    // The format panel and child drawers replace the dismissed keyboard, so
    // they must survive the "none" state their own opening produces.
    const isVisible =
        focusState === "composer" ||
        (focusState === "none" && (view === "format" || childDrawerId !== null));

    if (!isVisible) return null;

    // Portaled to <body>: composer containers can carry a CSS transform
    // (e.g. the thread view's reply form), which would silently become the
    // containing block of our position: fixed bar and pin it inside the
    // form instead of the viewport. The bn-root/bn-mantine classes come
    // along because BlockNote scopes its theme variables and component
    // styles under them, and the portal leaves the editor's container.
    return createPortal(
        <div
            className="bn-mobile-formatting-toolbar bn-root bn-mantine light"
            data-color-scheme="light"
            ref={keepEditorFocusOnTap}
        >
            {view === "format" && (
                <Drawer
                    className="mobile-toolbar__format-drawer"
                    title={t("Format")}
                    onClose={() => {
                        setView("toolbar");
                        // Closing the panel hands back to typing, keyboard
                        // included.
                        releaseKeyboardToEditor(editor);
                    }}
                >
                    <MobileFormatPanel onInsert={() => setView("toolbar")} />
                </Drawer>
            )}
            {/* Children portal their own Drawer here (display: contents, so
                it joins the bar's flex column right above the toolbar row). */}
            <div className="mobile-toolbar__drawer-slot" ref={setDrawerSlot} />
            {view === "link" && (
                <MobileLinkEditor onClose={() => setView("toolbar")} />
            )}
            {/* The buttons row folds away while the format panel is open —
                the panel's Drawer chrome (close button, swipe, tap back into
                the text) handles the way back. */}
            {view === "toolbar" && (
                <FormattingToolbar>
                    <MobileToolbarButton
                        icon={<Icon icon={KeyboardHide} size={IconSize.MEDIUM} />}
                        label={t("Hide keyboard")}
                        // The mousedown guard keeps the editor focused through
                        // the tap, so the blur here is what dismisses the
                        // keyboard — and with it the whole toolbar (focus
                        // state becomes "none" with no panel open).
                        onClick={dismissKeyboard}
                    />
                    <ToolbarSeparator />
                    <MobileToolbarButton
                        icon={<Icon icon={FormatText} size={IconSize.MEDIUM} />}
                        label={t("Formatting options")}
                        onClick={() => {
                            setView("format");
                            setChildDrawerId(null);
                            // Apple-Notes style: dismiss the keyboard so the
                            // panel takes its place instead of stacking on
                            // top of it. The selection lives in editor state
                            // and survives the blur.
                            dismissKeyboard();
                        }}
                    />
                    <StyleToggleButton style="bold" icon={<RiBold size={20} />} />
                    <StyleToggleButton style="italic" icon={<RiItalic size={20} />} />
                    <StyleToggleButton
                        style="underline"
                        icon={<RiUnderline size={20} />}
                    />
                    <MobileToolbarButton
                        icon={<RiLink size={20} />}
                        label={editor.dictionary.formatting_toolbar.link.tooltip}
                        isActive={hasLink}
                        onClick={() => setView("link")}
                    />
                    {bulletListItem && <BlockTypeButton item={bulletListItem} />}
                    {quoteItem && <BlockTypeButton item={quoteItem} />}
                    <FileCaptionButton key={"fileCaptionButton"} />
                    <FileReplaceButton key={"fileReplaceButton"} />
                    <FileDeleteButton key={"fileDeleteButton"} />
                    <FilePreviewButton key={"filePreviewButton"} />
                    {children && (
                        <MobileToolbarDrawerContext.Provider value={drawerApi}>
                            <ToolbarSeparator />
                            {children}
                        </MobileToolbarDrawerContext.Provider>
                    )}
                </FormattingToolbar>
            )}
        </div>,
        document.body,
    );
};
