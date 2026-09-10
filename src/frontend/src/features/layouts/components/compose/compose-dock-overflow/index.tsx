import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Button } from "@gouvfr-lasuite/ui-components";
import { DropdownMenu } from "@gouvfr-lasuite/ui-components";
import { useComposeWindows } from "@/features/providers/compose-windows";
import { ComposeWindowDescriptor } from "@/features/providers/compose-windows/types";
import { Icon } from "@/features/ui/components/icon";
import { getIconProps } from "../compose-overview";
import { useComposeSender } from "../use-compose-sender";

type ComposeDockOverflowProps = {
    /** Windows hidden from the dock, oldest first (array order). */
    windows: readonly ComposeWindowDescriptor[];
};

/**
 * "+X" pill listing the compose windows that no longer fit in the dock.
 * Picking one restores it, which moves it back among the visible tabs.
 */
export const ComposeDockOverflow = ({ windows }: ComposeDockOverflowProps) => {
    const { t } = useTranslation();
    const { focusWindow } = useComposeWindows();
    const getSender = useComposeSender();
    const [isOpen, setIsOpen] = useState(false);

    const options = windows
        .map((window) => ({
            label: window.title?.trim() || t("New message"),
            // The folded tabs are off screen entirely, so unlike a docked
            // tab — where an avatar is enough of a reminder — this list spells
            // the sender out. The icon slot already carries the mode.
            subText: getSender(window.mailboxId)?.email,
            callback: () => focusWindow(window.windowId, { moveToEnd: true }),
            icon: <Icon {...getIconProps(window.mode)} />,
        }))
        .reverse();

    return (
        <div className="compose-dock-overflow">
            <DropdownMenu isOpen={isOpen} onOpenChange={setIsOpen} options={options}>
                <Button
                    type="button"
                    color="neutral"
                    variant="bordered"
                    size="medium"
                    aria-label={t("{{count}} more compose window", { count: windows.length })}
                    onClick={() => setIsOpen(true)}
                >
                    +{windows.length}
                </Button>
            </DropdownMenu>
        </div>
    );
};
