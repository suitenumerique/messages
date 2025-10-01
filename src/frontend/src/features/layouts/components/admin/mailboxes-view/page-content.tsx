import { useTranslation } from "react-i18next";
import { usePagination } from "@openfun/cunningham-react";
import { Spinner } from "@gouvfr-lasuite/ui-kit";
import { useAdminMailDomain } from "@/features/providers/admin-maildomain";
import { AdminMailboxDataGrid } from "./mailbox-data-grid";

type AdminDomainPageContentProps = {
    pagination: ReturnType<typeof usePagination>;
}

export const AdminDomainPageContent = ({ pagination }: AdminDomainPageContentProps) => {
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
          <div style={{ padding: "2rem", textAlign: "center", color: "var(--c--theme--colors--danger-600)" }}>
            {t("Domain not found")}
          </div>
      );
    }

    return <AdminMailboxDataGrid domain={selectedMailDomain} pagination={pagination} />;
  }
