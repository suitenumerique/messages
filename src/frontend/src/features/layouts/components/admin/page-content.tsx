import { useAdminMailDomain } from "@/features/providers/admin-maildomain";
import { Spinner } from "@gouvfr-lasuite/ui-components";
import { PropsWithChildren } from "react"
import { useTranslation } from "react-i18next";

type AdminPageContentProps = PropsWithChildren;

/**
 * Generic admin page content component
 * which check if a selected mail domain is set before rendering children
 */
export const AdminPageContent = ({ children }: AdminPageContentProps) => {
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
          <div style={{ padding: "var(--c--globals--spacings--lg)", textAlign: "center", color: "var(--c--contextuals--background--semantic--error--primary)" }}>
            {t("Domain not found")}
          </div>
      );
    }

    return children;
}
