import { useTranslation } from "react-i18next";
import { usePagination } from "@gouvfr-lasuite/cunningham-react";
import { Icon, IconType, Spinner } from "@gouvfr-lasuite/ui-kit";
import { useAdminMailDomain } from "@/features/providers/admin-maildomain";
import { AdminMailboxDataGrid } from "./mailbox-data-grid";
import { Banner } from "@/features/ui/components/banner";
import { AdminSearchInput } from "@/features/forms/components/admin-search-input";

type AdminDomainPageContentProps = {
    pagination: ReturnType<typeof usePagination>;
    searchQuery: string;
    onSearchChange: (query: string) => void;
}

export const AdminDomainPageContent = ({ pagination, searchQuery, onSearchChange }: AdminDomainPageContentProps) => {
    const { t } = useTranslation();
    const { selectedMailDomain, isLoading } = useAdminMailDomain();

    if (isLoading) {
      return (
          <div className="admin-page__loading">
            <Spinner />
          </div>
      )
    }

    if (!selectedMailDomain) {
      return (
          <Banner type="error" icon={<Icon name="search_off" type={IconType.OUTLINED} />}>
            {t("Domain not found")}
          </Banner>
      );
    }

    return (
        <>
            <div className="admin-page__search">
                <AdminSearchInput
                    label={t("Search a mailbox")}
                    placeholder={t("Search by name or address…")}
                    initialValue={searchQuery}
                    onChange={onSearchChange}
                />
            </div>
            <AdminMailboxDataGrid
                domain={selectedMailDomain}
                pagination={pagination}
                searchQuery={searchQuery}
            />
        </>
    );
  }
