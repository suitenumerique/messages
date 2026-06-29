// FORK: reproduced from @gouvfr-lasuite/ui-kit AccessRoleDropdown since the
// original component is not publicly exported. Used by the invitation bar
// and member rows to pick a role via DropdownMenu.
import { useMemo } from "react";
import { Button, useCunningham } from "@gouvfr-lasuite/cunningham-react";
import {
    DropdownMenu,
    type DropdownMenuItem,
    type DropdownMenuOption,
    type DropdownMenuProps,
} from "@gouvfr-lasuite/ui-kit";
import { ChevronDown, ChevronUp } from "@gouvfr-lasuite/ui-kit/icons";

type AccessRoleDropdownProps = {
    selectedRole: string;
    roles: DropdownMenuOption[];
    onSelect: (role: string) => void;
    canUpdate?: boolean;
    isOpen?: boolean;
    onOpenChange?: (isOpen: boolean) => void;
    roleTopMessage?: DropdownMenuProps["topMessage"];
    onDelete?: () => void;
    canDelete?: boolean;
};

export const AccessRoleDropdown = ({
    roles,
    onSelect,
    canUpdate = true,
    selectedRole,
    isOpen,
    onOpenChange,
    roleTopMessage,
    onDelete,
    canDelete = true,
}: AccessRoleDropdownProps) => {
    // Aliased to `tc` so the i18next-cli parser does not extract Cunningham's
    // own translation keys (e.g. `components.share.*`) into our locale files.
    const { t: tc } = useCunningham();

    const currentRoleString = roles.find((role) => role.value === selectedRole);

    const options: DropdownMenuItem[] = useMemo(() => {
        if (!onDelete) {
            return roles;
        }
        return [
            ...roles,
            { type: "separator" as const },
            {
                label: tc("components.share.access.delete"),
                callback: onDelete,
                isDisabled: !canDelete,
            },
        ];
    }, [roles, onDelete, tc, canDelete]);

    if (!canUpdate) {
        return (
            <span className="c__access-role-dropdown__role-label-can-not-update">
                {currentRoleString?.label}
            </span>
        );
    }

    return (
        <DropdownMenu
            isOpen={isOpen}
            shouldCloseOnInteractOutside={(element) => {
                const isAccessRoleDropdown = element.closest(".c__access-role-dropdown");
                if (isAccessRoleDropdown) return false;
                return true;
            }}
            onOpenChange={onOpenChange}
            options={options}
            selectedValues={[selectedRole]}
            onSelectValue={onSelect}
            topMessage={roleTopMessage}
        >
            <Button
                className="c__access-role-dropdown"
                data-testid="access-role-dropdown-button"
                size="small"
                color="neutral"
                variant="tertiary"
                icon={isOpen ? <ChevronUp /> : <ChevronDown />}
                iconPosition="right"
                onClick={() => {
                    onOpenChange?.(!isOpen);
                }}
            >
                {currentRoleString?.label}
            </Button>
        </DropdownMenu>
    );
};
