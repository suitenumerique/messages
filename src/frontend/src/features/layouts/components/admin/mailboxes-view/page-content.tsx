import { Spinner } from "@gouvfr-lasuite/ui-kit";
import { AdminMailboxDataGrid } from "./mailbox-data-grid";
import { useAdminMailDomain } from "@/features/providers/admin-maildomain";
import { useTranslation } from "react-i18next";

export const AdminDomainPageContent = () => {
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
            {t("admin_maildomains_details.errors.domain_not_found")}
          </div>
      );
    }

    return <AdminMailboxDataGrid domain={selectedMailDomain} />;
  }
