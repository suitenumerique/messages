import { DropdownMenu, DropdownMenuOption } from "@gouvfr-lasuite/ui-kit";
import { Button, ButtonProps } from "@gouvfr-lasuite/cunningham-react";
import { PropsWithChildren, useState } from "react";
import { ChevronDown } from "@gouvfr-lasuite/ui-kit/icons";
import { useTranslation } from "react-i18next";

export type DropdownButtonProps = PropsWithChildren<ButtonProps & {
    /** Optional dropdown menu options */
    dropdownOptions?: DropdownMenuOption[];
    /** Whether the dropdown should be shown */
    showDropdown?: boolean;
}>;

export const DropdownButton = ({
    children,
    dropdownOptions = [],
    showDropdown = true,
    ...buttonProps
}: DropdownButtonProps) => {
    const { t } = useTranslation();
    const [dropdownOpen, setDropdownOpen] = useState(false);

    const hasDropdownOptions = showDropdown && dropdownOptions.length > 0;

    return (
        <div className="dropdown-button">
            <Button {...buttonProps}>
                {children}
            </Button>
            {hasDropdownOptions && (
                <DropdownMenu
                    isOpen={dropdownOpen}
                    onOpenChange={setDropdownOpen}
                    options={dropdownOptions}
                >
                    <Button
                        color={buttonProps.color}
                        disabled={buttonProps.disabled}
                        icon={<ChevronDown size="small" />}
                        aria-label={buttonProps["aria-label"] ?? t("More options")}
                        type="button"
                        onClick={() => setDropdownOpen(open => !open)}
                    />
                </DropdownMenu>
            )}
        </div>
    );
};
