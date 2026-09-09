import { useEffect, useRef } from "react";
import { useTranslation } from "react-i18next";
import { Button } from "@gouvfr-lasuite/cunningham-react";
import { IconSize, IconType } from "@gouvfr-lasuite/ui-kit";
import { MessageFormMode } from "@/features/forms/components/message-form";
import { useComposeWindows } from "@/features/providers/compose-windows";
import { ComposeWindowDescriptor } from "@/features/providers/compose-windows/types";
import { useFocusTrap } from "@/hooks/use-focus-trap";
import { useRestoreFocus } from "@/hooks/use-restore-focus";
import { useComposeSender } from "../use-compose-sender";
import { Edit } from "@gouvfr-lasuite/ui-kit/icons";
import { Icon } from "@/features/ui/components/icon";

type ComposeOverviewProps = {
    /** All tracked windows, MRU order (most recent last). */
    windows: readonly ComposeWindowDescriptor[];
    onClose: () => void;
    /** Runs the window's close flow (save/confirm/discard). */
    onRequestCloseWindow: (windowId: string) => void;
};

export const getIconProps = (mode: MessageFormMode) => {
    if (mode === "forward") return { name: "forward" };
    if (mode === "new") return { icon: Edit, size: IconSize.SMALL };
    return { name: "reply" };
};

/**
 * Mobile exploded view of the compose pile (like Apple Mail): one card per
 * window, tap to resume it as a sheet, or close it individually. Cards are
 * summaries on purpose — miniaturizing the live forms would be fragile.
 */
export const ComposeOverview = ({ windows, onClose, onRequestCloseWindow }: ComposeOverviewProps) => {
    const { t } = useTranslation();
    const { focusWindow } = useComposeWindows();
    // Cards have the room a docked tab lacks, so the sender is written out
    // rather than reduced to an avatar.
    const getSender = useComposeSender();
    const rootRef = useRef<HTMLDivElement>(null);

    // A modal dialog for real: focus moves in on open, Tab cycles inside, and
    // closing gives it back to the stack bar that opened it. When a card is
    // picked, the sheet grabs the caret itself (focusTick) and wins.
    useFocusTrap(rootRef, true);
    useRestoreFocus();
    useEffect(() => {
        rootRef.current?.querySelector<HTMLElement>(".compose-overview__card")?.focus();
    }, []);

    const handleKeyDown = (event: React.KeyboardEvent<HTMLDivElement>) => {
        if (event.key !== "Escape" || event.defaultPrevented) return;
        event.stopPropagation();
        onClose();
    };

    return (
        <div
            ref={rootRef}
            className="compose-overview"
            role="dialog"
            aria-modal="true"
            aria-label={t("Compose windows")}
            onKeyDown={handleKeyDown}
        >
            <div className="compose-overview__backdrop" onClick={onClose} aria-hidden="true" />
            <ul className="compose-overview__cards">
                {windows.map((window, index) => {
                    const senderEmail = getSender(window.mailboxId)?.email;
                    return (
                    <li
                        key={window.windowId}
                        className="compose-overview__item"
                        // Stagger the cards' entrance, most recent first.
                        style={{ animationDelay: `${(windows.length - 1 - index) * 40}ms` }}
                    >
                        <button
                            type="button"
                            className="compose-overview__card"
                            onClick={() => {
                                focusWindow(window.windowId);
                                onClose();
                            }}
                        >
                            <Icon {...getIconProps(window.mode)} />
                            <span className="compose-overview__card-text">
                                <span className="compose-overview__card-title">
                                    {window.title?.trim() || t("New message")}
                                </span>
                                {senderEmail && (
                                    <span className="compose-overview__card-sender">
                                        {senderEmail}
                                    </span>
                                )}
                            </span>
                        </button>
                        <Button
                            type="button"
                            variant="tertiary"
                            size="small"
                            className="compose-overview__close"
                            aria-label={t("Close")}
                            icon={<Icon name="close" type={IconType.OUTLINED} />}
                            onClick={() => onRequestCloseWindow(window.windowId)}
                        />
                    </li>
                    );
                })}
            </ul>
        </div>
    );
};
