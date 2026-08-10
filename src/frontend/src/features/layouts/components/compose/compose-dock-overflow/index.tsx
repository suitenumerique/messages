import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Button } from "@gouvfr-lasuite/cunningham-react";
import { DropdownMenu } from "@gouvfr-lasuite/ui-kit";
import { useComposeWindows } from "@/features/providers/compose-windows";
import { ComposeWindowDescriptor } from "@/features/providers/compose-windows/types";
import { Icon } from "@/features/ui/components/icon";
import { getIconProps } from "../compose-overview";

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
    const [isOpen, setIsOpen] = useState(false);

    const options = windows
        .map((window) => ({
            label: window.title?.trim() || t("New message"),
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
