import { useEffect, useRef, useState } from "react";
import { Button, Tooltip } from "@gouvfr-lasuite/cunningham-react";
import { Icon, IconType } from "@gouvfr-lasuite/ui-kit";
import { useTranslation } from "react-i18next";
import clsx from "clsx";
import { MessageFormHandle } from "@/features/forms/components/message-form";
import { useComposeWindows } from "@/features/providers/compose-windows";
import { ComposeWindowDescriptor } from "@/features/providers/compose-windows/types";
import { ComposeWindowForm } from "./compose-window-form";
import { CloseConfirmModal } from "./close-confirm-modal";

type ComposeWindowProps = {
    descriptor: ComposeWindowDescriptor;
};

/**
 * A floating compose window: header with minimize/expand/close controls and a
 * compact message form as body. Minimize and expand are pure CSS state changes
 * so the form (and its unsaved content) is never unmounted.
 */
export const ComposeWindow = ({ descriptor }: ComposeWindowProps) => {
    const { t } = useTranslation();
    const { closeWindow, setWindowState } = useComposeWindows();
    const formRef = useRef<MessageFormHandle>(null);
    const rootRef = useRef<HTMLElement>(null);
    const [showCloseConfirm, setShowCloseConfirm] = useState(false);
    const [isClosing, setIsClosing] = useState(false);
    const { windowId, state } = descriptor;
    const isMinimized = state === "minimized";
    const isExpanded = state === "expanded";
    const title = descriptor.title?.trim() || t("New message");

    useEffect(() => {
        if (descriptor.focusTick > 0) {
            rootRef.current?.focus();
        }
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

    return (
        <>
            {isExpanded && (
                <div
                    className="compose-window-backdrop"
                    onClick={() => setWindowState(windowId, "open")}
                    aria-hidden="true"
                />
            )}
            <section
                ref={rootRef}
                tabIndex={-1}
                className={clsx("compose-window", `compose-window--${state}`)}
                aria-label={title}
            >
                <header className="compose-window__header">
                    <button
                        type="button"
                        className="compose-window__title"
                        title={title}
                        onClick={() => setWindowState(windowId, isMinimized ? "open" : "minimized")}
                    >
                        {title}
                    </button>
                    <div className="compose-window__actions">
                        <Tooltip content={isMinimized ? t("Restore") : t("Minimize")}>
                            <Button
                                type="button"
                                variant="tertiary"
                                size="small"
                                aria-label={isMinimized ? t("Restore") : t("Minimize")}
                                icon={<Icon name={isMinimized ? "expand_less" : "minimize"} type={IconType.OUTLINED} />}
                                onClick={() => setWindowState(windowId, isMinimized ? "open" : "minimized")}
                            />
                        </Tooltip>
                        {!isMinimized && (
                            <>
                                <Tooltip content={isExpanded ? t("Exit full screen") : t("Expand")}>
                                    <Button
                                        type="button"
                                        variant="tertiary"
                                        size="small"
                                        aria-label={isExpanded ? t("Exit full screen") : t("Expand")}
                                        icon={<Icon name={isExpanded ? "close_fullscreen" : "open_in_full"} type={IconType.OUTLINED} />}
                                        onClick={() => setWindowState(windowId, isExpanded ? "open" : "expanded")}
                                    />
                                </Tooltip>
                                <Tooltip content={t("Open in new tab")}>
                                    <Button
                                        type="button"
                                        variant="tertiary"
                                        size="small"
                                        aria-label={t("Open in new tab")}
                                        icon={<Icon name="open_in_new" type={IconType.OUTLINED} />}
                                        onClick={handlePopOut}
                                    />
                                </Tooltip>
                            </>
                        )}
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
