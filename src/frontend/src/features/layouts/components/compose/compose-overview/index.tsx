import { useEffect } from "react";
import { useTranslation } from "react-i18next";
import { Button } from "@gouvfr-lasuite/cunningham-react";
import { IconSize, IconType } from "@gouvfr-lasuite/ui-kit";
import { MessageFormMode } from "@/features/forms/components/message-form";
import { useComposeWindows } from "@/features/providers/compose-windows";
import { ComposeWindowDescriptor } from "@/features/providers/compose-windows/types";
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

    useEffect(() => {
        const handleKeyDown = (event: KeyboardEvent) => {
            if (event.key === "Escape") onClose();
        };
        document.addEventListener("keydown", handleKeyDown);
        return () => document.removeEventListener("keydown", handleKeyDown);
    }, [onClose]);

    return (
        <div className="compose-overview" role="dialog" aria-modal="true" aria-label={t("Compose windows")}>
            <div className="compose-overview__backdrop" onClick={onClose} aria-hidden="true" />
            <ul className="compose-overview__cards">
                {windows.map((window, index) => (
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
                            <span className="compose-overview__card-title">
                                {window.title?.trim() || t("New message")}
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
                ))}
            </ul>
        </div>
    );
};
