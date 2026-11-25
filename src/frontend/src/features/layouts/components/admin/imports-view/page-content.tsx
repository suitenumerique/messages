import { useTranslation } from "react-i18next";
import { IconType, Spinner, usePagination } from "@gouvfr-lasuite/ui-components";
import { MaildomainsImportsListStatusItem as ListStatus } from "@/features/api/gen";
import { useAdminMailDomain } from "@/features/providers/admin-maildomain";
import { AdminSearchInput } from "@/features/forms/components/admin-search-input";
import { Banner } from "@/features/ui/components/banner";
import { Icon } from "@/features/ui/components/icon";
import useAbility, { Abilities } from "@/hooks/use-ability";
import { AdminImportsDataGrid } from "./imports-data-grid";
import { ImportStatusFilter } from "./status-filter";

type AdminImportsViewPageContentProps = {
    pagination: ReturnType<typeof usePagination>;
    searchQuery: string;
    onSearchChange: (query: string) => void;
    statuses: ListStatus[];
    onStatusesChange: (statuses: ListStatus[]) => void;
};

export const AdminImportsViewPageContent = ({
    pagination,
    searchQuery,
    onSearchChange,
    statuses,
    onStatusesChange,
}: AdminImportsViewPageContentProps) => {
    const { t } = useTranslation();
    const { selectedMailDomain, isLoading } = useAdminMailDomain();
    const canImport = useAbility(Abilities.CAN_IMPORT_MAILDOMAIN_MESSAGES, selectedMailDomain);

    if (isLoading) {
        return (
            <div className="admin-page__loading">
                <Spinner />
            </div>
        );
    }

    if (!selectedMailDomain) {
        return (
            <Banner type="error" icon={<Icon name="search_off" type={IconType.OUTLINED} />}>
                {t("Domain not found")}
            </Banner>
        );
    }

    if (!canImport) {
        return (
            <Banner type="error">
                {t("You cannot import messages into the mailboxes of this domain.")}
            </Banner>
        );
    }

    return (
        <>
            <div className="admin-page__search admin-imports__toolbar">
                <ImportStatusFilter value={statuses} onChange={onStatusesChange} />
                <AdminSearchInput
                    label={t("Search an import")}
                    placeholder={t("Search by mailbox name or address…")}
                    initialValue={searchQuery}
                    onChange={onSearchChange}
                />
            </div>
            <AdminImportsDataGrid
                domain={selectedMailDomain}
                pagination={pagination}
                searchQuery={searchQuery}
                statuses={statuses}
            />
        </>
    );
};
