import { useEffect, useRef, useState } from "react";
import { Button, Tooltip } from "@gouvfr-lasuite/cunningham-react";
import { DropdownMenu, IconType, useResponsive } from "@gouvfr-lasuite/ui-kit";
import { ChevronUp, Maximize, Minimize, Minus, Send, Shortcut, XMark } from "@gouvfr-lasuite/ui-kit/icons";
import { Icon } from "@/features/ui/components/icon";
import { useTranslation } from "react-i18next";
import clsx from "clsx";
import { MessageFormHandle } from "@/features/forms/components/message-form";
import { PREFER_SEND_MODE_KEY, PreferSendMode } from "@/features/config/constants";
import { useComposeWindows } from "@/features/providers/compose-windows";
import { ComposeWindowDescriptor } from "@/features/providers/compose-windows/types";
import { isNativePlatform } from "@/features/native/platform";
import { useDragGesture } from "@/hooks/use-drag-gesture";
import { useLongPress } from "@/hooks/use-long-press";
import { ComposeWindowForm } from "./compose-window-form";
import { CloseConfirmModal } from "./close-confirm-modal";

type ComposeWindowProps = {
    descriptor: ComposeWindowDescriptor;
    /** Tab folded into the "+X" dropdown: hidden but kept mounted. */
    isOverflowed?: boolean;
};

/**
 * A floating compose window: header with minimize/expand/close controls and a
 * compact message form as body. Minimize and expand are pure CSS state changes
 * so the form (and its unsaved content) is never unmounted.
 */
export const ComposeWindow = ({ descriptor, isOverflowed = false }: ComposeWindowProps) => {
    const { t } = useTranslation();
    const { closeWindow, minimizeWindow, restoreWindow, setPresentation, registerWindowHandle } = useComposeWindows();
    const formRef = useRef<MessageFormHandle>(null);
    const rootRef = useRef<HTMLElement>(null);
    const [showCloseConfirm, setShowCloseConfirm] = useState(false);
    const [isClosing, setIsClosing] = useState(false);
    const { windowId, presentation, isMinimized } = descriptor;
    // Mobile ignores the presentation: the expanded window is always a
    // full-screen bottom sheet, minimized ones live behind the stack bar.
    const { isMobile } = useResponsive();
    const isSheet = isMobile && !isMinimized;
    // Swipe-to-minimize is reserved for the native shells. In a browser — a
    // narrow desktop viewport included — the pointer is a mouse, and the
    // gesture would capture it on pointerdown and swallow the clicks meant
    // for the header controls (title, close).
    const isSheetDraggable = isSheet && isNativePlatform();
    const isFloating = !isMobile && !isMinimized && presentation === "floating";
    const title = descriptor.title?.trim() || t("New message");
    const toggleMinimize = () => (isMinimized ? restoreWindow(windowId) : minimizeWindow(windowId));

    // Swipe-down on the sheet header minimizes, like the mobile drawers.
    const sheetDrag = useDragGesture({
        axis: "y",
        direction: "positive",
        commitDistance: () => Math.max(60, (rootRef.current?.offsetHeight ?? 0) / 3),
        onCommit: () => minimizeWindow(windowId),
        // Only the Close/Send CTAs opt out: the title, although a <button>
        // (tap to minimize), must stay part of the drag surface — excluding
        // every button left almost nothing to grab.
        excludeSelector: ".c__button",
        disabled: !isSheetDraggable,
    });

    // Long-press on the sheet's Send CTA opens the send-mode menu (send and
    // archive, set as default) — the touch counterpart of the desktop
    // split-button dropdown. Archiving a brand new thread is meaningless, so
    // plain "new" windows keep the simple tap.
    const [isSendMenuOpen, setIsSendMenuOpen] = useState(false);
    const canArchiveOnSend = descriptor.mode !== "new";
    // Mirrors the form's preferred send mode (same storage key, same
    // new-thread override) so the CTA icon reflects the default action
    // before the form handle is even mounted.
    const [sheetSendMode, setSheetSendMode] = useState<PreferSendMode>(() =>
        canArchiveOnSend
            ? (localStorage.getItem(PREFER_SEND_MODE_KEY) as PreferSendMode ?? PreferSendMode.SEND)
            : PreferSendMode.SEND,
    );
    const suppressSendTapRef = useRef(false);
    const { handlers: sendLongPressHandlers } = useLongPress(() => {
        if (!canArchiveOnSend) return;
        // The click fired on finger release must not also send.
        suppressSendTapRef.current = true;
        setSheetSendMode(formRef.current?.getPreferredSendMode() ?? PreferSendMode.SEND);
        setIsSendMenuOpen(true);
    });

    const handleSendTap = () => {
        if (suppressSendTapRef.current) {
            suppressSendTapRef.current = false;
            return;
        }
        formRef.current?.requestSend();
    };

    const toggleSendAndArchiveDefault = () => {
        const next = sheetSendMode === PreferSendMode.SEND_AND_ARCHIVE
            ? PreferSendMode.SEND
            : PreferSendMode.SEND_AND_ARCHIVE;
        formRef.current?.setPreferredSendMode(next);
        setSheetSendMode(next);
    };

    const handleKeyDown = (event: React.KeyboardEvent<HTMLElement>) => {
        if (event.key !== "Escape" || event.defaultPrevented) return;
        event.stopPropagation();
        if (isFloating) {
            setPresentation(windowId, "docked");
        } else if (!isMinimized) {
            minimizeWindow(windowId);
        }
    };

    useEffect(() => {
        if (descriptor.focusTick === 0) return;
        const root = rootRef.current;
        if (!root) return;
        // The caret lands in the composer; the window itself is the fallback
        // while the form is still loading (BlockNote's own mount autofocus
        // then takes over).
        const composer = root.querySelector<HTMLElement>(".ProseMirror");
        (composer ?? root).focus();
    }, [descriptor.focusTick]);

    const handleCloseRequest = async () => {
        const handle = formRef.current;
        if (!handle) {
            closeWindow(windowId);
            return;
        }
        setIsClosing(true);
        try {
            if (descriptor.openedOnExistingDraft) {
                // The draft pre-existed this window (detached reply, restored
                // session): closing keeps it, we just flush pending changes.
                await handle.saveDraftNow();
                closeWindow(windowId);
            } else if (handle.hasUnsavedContent()) {
                setShowCloseConfirm(true);
            } else if (handle.getDraftId()) {
                // Materialized draft emptied by the user: drop it silently.
                await handle.discardDraft({ notify: false });
                closeWindow(windowId);
            } else {
                closeWindow(windowId);
            }
        } finally {
            setIsClosing(false);
        }
    };

    const handlePopOut = async () => {
        // The standalone page loads the draft by id, so it must exist
        // server-side before the tab opens.
        const draftId = await formRef.current?.ensureDraftId();
        if (!draftId) return;
        window.open(`/mailbox/${descriptor.mailboxId}/draft/${draftId}`, "_blank", "noopener");
        closeWindow(windowId);
    };

    const handleSaveAndClose = async () => {
        await formRef.current?.saveDraftNow();
        setShowCloseConfirm(false);
        closeWindow(windowId);
    };

    const handleDeleteAndClose = async () => {
        await formRef.current?.discardDraft();
        setShowCloseConfirm(false);
        closeWindow(windowId);
    };

    // Expose the close flow and edited state to the provider registry (mobile
    // overview, draft-window recycling); the ref indirection keeps the
    // registration stable while the flow closure is recreated every render.
    const closeRequestRef = useRef(handleCloseRequest);
    useEffect(() => {
        closeRequestRef.current = handleCloseRequest;
    });
    useEffect(
        () => registerWindowHandle(windowId, {
            requestClose: () => closeRequestRef.current(),
            wasUserEdited: () => formRef.current?.wasUserEdited() ?? false,
        }),
        [registerWindowHandle, windowId],
    );

    return (
        <>
            {isFloating && (
                <div
                    className="compose-window-backdrop"
                    onClick={() => setPresentation(windowId, "docked")}
                    aria-hidden="true"
                />
            )}
            <section
                ref={rootRef}
                tabIndex={-1}
                className={clsx(
                    "compose-window",
                    `compose-window--${isMinimized ? "minimized" : isSheet ? "sheet" : presentation}`,
                    { "compose-window--overflow": isOverflowed },
                )}
                style={
                    isSheetDraggable && sheetDrag.isDragging
                        ? { transform: `translateY(${sheetDrag.offset}px)` }
                        : undefined
                }
                aria-label={title}
                role={isFloating || isSheet ? "dialog" : undefined}
                aria-modal={isFloating || isSheet ? true : undefined}
                onKeyDown={handleKeyDown}
            >
                <header
                    className="compose-window__header"
                    {...(isSheetDraggable ? sheetDrag.handlers : {})}
                >
                    {isSheet && (
                        <Tooltip content={t("Close")}>
                            <Button
                                type="button"
                                color="neutral"
                                variant="tertiary"
                                size="small"
                                aria-label={t("Close")}
                                icon={<Icon icon={XMark} />}
                                onClick={handleCloseRequest}
                                disabled={isClosing}
                            />
                        </Tooltip>
                    )}
                    <button
                        type="button"
                        className="compose-window__title"
                        title={title}
                        onClick={toggleMinimize}
                    >
                        {title}
                    </button>
                    {isSheet && (
                        <DropdownMenu
                            isOpen={isSendMenuOpen}
                            onOpenChange={setIsSendMenuOpen}
                            options={[
                                {
                                    label: sheetSendMode === PreferSendMode.SEND_AND_ARCHIVE ? t("Send") : t("Send and archive"),
                                    icon: sheetSendMode === PreferSendMode.SEND_AND_ARCHIVE
                                        ? <Icon icon={Send} />
                                        : <Icon name="send_and_archive" type={IconType.OUTLINED} />,
                                    callback: () => formRef.current?.requestSend({
                                        archive: sheetSendMode !== PreferSendMode.SEND_AND_ARCHIVE,
                                    }),
                                    showSeparator: true,
                                },
                                {
                                    label: t("Use \"Send and archive\" by default"),
                                    icon: (
                                        <Icon
                                            name={sheetSendMode === PreferSendMode.SEND_AND_ARCHIVE ? "check_box" : "check_box_outline_blank"}
                                            type={IconType.OUTLINED}
                                        />
                                    ),
                                    callback: toggleSendAndArchiveDefault,
                                },
                            ]}
                        >
                            <Tooltip content={sheetSendMode === PreferSendMode.SEND_AND_ARCHIVE ? t("Send and archive") : t("Send")}>
                                <Button
                                    type="button"
                                    color="brand"
                                    variant="tertiary"
                                    size="small"
                                    aria-label={sheetSendMode === PreferSendMode.SEND_AND_ARCHIVE ? t("Send and archive") : t("Send")}
                                    icon={
                                        sheetSendMode === PreferSendMode.SEND_AND_ARCHIVE
                                            ? <Icon name="send_and_archive" type={IconType.OUTLINED} />
                                            : <Icon icon={Send} />
                                    }
                                    onClick={handleSendTap}
                                    {...sendLongPressHandlers}
                                />
                            </Tooltip>
                        </DropdownMenu>
                    )}
                    {!isSheet && (
                        <div className="compose-window__actions">
                            {!isMinimized && !isSheet && (
                                <>
                                    <Tooltip content={t("Open in new tab")}>
                                        <Button
                                            type="button"
                                            variant="tertiary"
                                            size="small"
                                            aria-label={t("Open in new tab")}
                                            icon={<Icon icon={Shortcut} />}
                                            onClick={handlePopOut}
                                        />
                                    </Tooltip>
                                    <Tooltip content={isFloating ? t("Dock") : t("Detach")}>
                                        <Button
                                            type="button"
                                            variant="tertiary"
                                            size="small"
                                            aria-label={isFloating ? t("Dock") : t("Detach")}
                                            icon={<Icon icon={isFloating ? Minimize : Maximize} />}
                                            onClick={() => setPresentation(windowId, isFloating ? "docked" : "floating")}
                                        />
                                    </Tooltip>
                                </>
                            )}
                            <Tooltip content={isMinimized ? t("Open") : t("Minimize")}>
                                <Button
                                    type="button"
                                    variant="tertiary"
                                    size="small"
                                    aria-label={isMinimized ? t("Open") : t("Minimize")}
                                    icon={<Icon icon={isMinimized ? ChevronUp : Minus} />}
                                    onClick={toggleMinimize}
                                />
                            </Tooltip>
                            <Tooltip content={t("Close")}>
                                <Button
                                    type="button"
                                    variant="tertiary"
                                    size="small"
                                    aria-label={t("Close")}
                                    icon={<Icon name="close" type={IconType.OUTLINED} />}
                                    onClick={handleCloseRequest}
                                    disabled={isClosing}
                                />
                            </Tooltip>
                        </div>
                    )}
                </header>
                <div className="compose-window__body" hidden={isMinimized}>
                    <ComposeWindowForm descriptor={descriptor} formRef={formRef} />
                </div>
            </section>
            <CloseConfirmModal
                isOpen={showCloseConfirm}
                onSave={handleSaveAndClose}
                onDelete={handleDeleteAndClose}
                onCancel={() => setShowCloseConfirm(false)}
            />
        </>
    );
};
