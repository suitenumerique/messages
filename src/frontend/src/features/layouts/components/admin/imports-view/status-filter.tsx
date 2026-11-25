import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Button, DropdownMenu } from "@gouvfr-lasuite/ui-components";
import { ChevronDown } from "@gouvfr-lasuite/ui-components/icons";
import { MaildomainsImportsListStatusItem as ListStatus } from "@/features/api/gen";
import { IMPORT_LIST_STATUSES } from "@/hooks/import-status";

type ImportStatusFilterProps = {
    value: ListStatus[];
    onChange: (statuses: ListStatus[]) => void;
};

/** Picks the statuses of the imports to list: one, several or all of them. */
export const ImportStatusFilter = ({ value, onChange }: ImportStatusFilterProps) => {
    const { t } = useTranslation();
    const [isOpen, setIsOpen] = useState(false);
    const labels: Record<ListStatus, { label: string; subText: string }> = {
        in_progress: {
            label: t("In progress"),
            subText: t("Imports running, and continuous imports checking for new messages."),
        },
        failed: {
            label: t("Failed"),
            subText: t("Imports that stopped on an error."),
        },
        completed: {
            label: t("Completed"),
            subText: t("Finished imports, and continuous imports whose polling is paused."),
        },
    };
    const triggerLabel =
        value.length === IMPORT_LIST_STATUSES.length
            ? t("Status: All")
            : t("Status: {{statuses}}", {
                  statuses: value.map((status) => labels[status].label).join(", "),
              });

    const toggle = (status: ListStatus) => {
        const next = value.includes(status)
            ? value.filter((picked) => picked !== status)
            : [...value, status];
        onChange(IMPORT_LIST_STATUSES.filter((listed) => next.includes(listed)));
    };

    return (
        <div className="admin-imports__status-filter">
            <DropdownMenu
                isOpen={isOpen}
                onOpenChange={setIsOpen}
                selectedValues={value}
                onSelectValue={(picked) => {
                    const status = IMPORT_LIST_STATUSES.find((listed) => listed === picked);
                    if (status) toggle(status);
                }}
                options={IMPORT_LIST_STATUSES.map((status) => ({
                    ...labels[status],
                    value: status,
                    // Statuses are picked several at a time.
                    keepOpen: true,
                    // Unpicking the last status would list nothing.
                    isDisabled: value.length === 1 && value[0] === status,
                }))}
            >
                <Button
                    size="small"
                    color="neutral"
                    variant="bordered"
                    icon={<ChevronDown />}
                    iconPosition="right"
                    aria-haspopup="menu"
                    aria-expanded={isOpen}
                    onClick={() => setIsOpen(!isOpen)}
                >
                    {triggerLabel}
                </Button>
            </DropdownMenu>
        </div>
    );
};
