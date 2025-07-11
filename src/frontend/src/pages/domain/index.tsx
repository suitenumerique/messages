import { useEffect } from "react";
import { DataGrid } from "@openfun/cunningham-react";
import { useRouter } from "next/router";
import { useTranslation } from "react-i18next";
import { Spinner } from "@gouvfr-lasuite/ui-kit";
import { AdminLayout } from "@/features/layouts/components/admin/admin-layout";
import Bar from "@/features/ui/components/bar";
import { useMaildomainsList } from "@/features/api/gen/maildomains/maildomains";
import { MailDomainAdmin } from "@/features/api/gen";
import { useAdminMailDomain } from "@/features/providers/admin-maildomain";

function AdminDataGrid() {
  const router = useRouter();
  const { t, i18n } = useTranslation();
  const { data: maildomainsData, error } = useMaildomainsList();

  const domains = maildomainsData?.data.results || [];

  const columns = [
    {
      id: "name",
      headerName: t("admin_maildomains_list.datagrid_headers.name"),
      renderCell: ({ row }: { row: MailDomainAdmin }) => (
        <span
          style={{ cursor: "pointer", color: "var(--c--theme--colors--primary-600)" }}
          onClick={() => router.push(`/domain/${row.id}`)}
        >
          {row.name}
        </span>
      ),
    },
    {
      id: "created_at",
      headerName: t("admin_maildomains_list.datagrid_headers.created_at"),
      renderCell: ({ row }: { row: MailDomainAdmin }) => new Date(row.created_at).toLocaleDateString(i18n.resolvedLanguage),
    },
    {
      id: "updated_at",
      headerName: t("admin_maildomains_list.datagrid_headers.updated_at"),
      renderCell: ({ row }: { row: MailDomainAdmin }) => new Date(row.updated_at).toLocaleDateString(i18n.resolvedLanguage),
    },
  ];

  if (error) {
    return (
      <div className="admin-data-grid">
        <div style={{ padding: "2rem", textAlign: "center", color: "var(--c--theme--colors--danger-600)" }}>
          {t("admin_maildomains_list.loading_error")}
        </div>
      </div>
    );
  }

  return (
    <div className="admin-data-grid">
      <DataGrid
        columns={columns}
        rows={domains}
      />
    </div>
  );
}

const AdminPageContent = () => {
  const router = useRouter();
  const { t } = useTranslation();
  const { mailDomains, isLoading } = useAdminMailDomain();

  /**
   * Auto-navigate to first domain if there's only one.
   */
  useEffect(() => {
    if (!isLoading && mailDomains) {
      if (mailDomains.length === 1) {
        router.replace(`/domain/${mailDomains[0].id}`);
      }
    }
  }, [router, mailDomains, isLoading]);

  if (isLoading || mailDomains?.length === 1) {
    return (
        <div className="admin-page__loading">
          <Spinner />
        </div>
    )
  }

  return (
    <>
      <Bar className="admin-page__bar">
        <h1>{t("admin_maildomains_list.title")}</h1>
      </Bar>
      <AdminDataGrid />
    </>
  )
}

/**
 * Admin page which list all mail domains.
 */
export default function AdminPage() {
  return (
    <AdminLayout>
      <AdminPageContent />
    </AdminLayout>
  );
}
