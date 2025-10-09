import { DropdownMenu, DropdownMenuOption, Icon, IconType } from "@gouvfr-lasuite/ui-kit";
import { Button, ButtonProps } from "@openfun/cunningham-react";
import { PropsWithChildren, useState } from "react";

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
                        icon={<Icon name="arrow_drop_down" type={IconType.OUTLINED} />}
                        type="button"
                        onClick={() => setDropdownOpen(open => !open)}
                    />
                </DropdownMenu>
            )}
        </div>
    );
};
