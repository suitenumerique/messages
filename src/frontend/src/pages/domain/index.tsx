import { useEffect } from "react";
import { DataGrid, usePagination } from "@openfun/cunningham-react";
import { useRouter } from "next/router";
import { Trans, useTranslation } from "react-i18next";
import { Spinner } from "@gouvfr-lasuite/ui-kit";
import { AdminLayout } from "@/features/layouts/components/admin/admin-layout";
import Bar from "@/features/ui/components/bar";
import { getMaildomainsListQueryOptions, MailDomainAdmin, MailDomainAdminWrite } from "@/features/api/gen";
import { useAdminMailDomain } from "@/features/providers/admin-maildomain";
import useAbility, { Abilities } from "@/hooks/use-ability";
import { Banner } from "@/features/ui/components/banner";
import { CreateDomainAction } from "@/features/layouts/components/admin/domains-view/create-domain-action";
import { useQueryClient } from "@tanstack/react-query";
import { addToast, ToasterItem } from "@/features/ui/components/toaster";

type AdminDataGridProps = {
  pagination: ReturnType<typeof usePagination>;
  domains: MailDomainAdmin[];
}

function AdminDataGrid({ domains, pagination }: AdminDataGridProps) {
  const router = useRouter();
  const { t, i18n } = useTranslation();
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

  return (
    <div className="admin-data-grid">
      <DataGrid
        columns={columns}
        rows={domains}
        pagination={pagination}
        enableSorting={false}
        onSortModelChange={() => undefined}
      />
    </div>
  );
}

const AdminPageContent = () => {
  const router = useRouter();
  const { t } = useTranslation();
  const { mailDomains, isLoading, error, pagination } = useAdminMailDomain();
  const canCreateMaildomain = useAbility(Abilities.CAN_CREATE_MAILDOMAINS);
  const shouldRedirect = !canCreateMaildomain && !isLoading && mailDomains.length === 1;

  /**
   * Auto-navigate to first domain if there's only one and the
   * user has no ability to create maildomains.
   */
  useEffect(() => {
    if (shouldRedirect) {
        router.replace(`/domain/${mailDomains[0].id}`);
    }
  }, [router, shouldRedirect]);

  if (isLoading || shouldRedirect) {
    return (
        <div className="admin-page__loading">
          <Spinner />
        </div>
    )
  }

  if (error) {
    return (
      <Banner type="error">
          {t("admin_maildomains_list.loading_error")}
      </Banner>
    );
  }

  return (
    <>
      <Bar className="admin-page__bar">
        <h1>{t("admin_maildomains_list.title")}</h1>
      </Bar>
      <AdminDataGrid domains={mailDomains} pagination={pagination} />
    </>
  )
}

/**
 * Admin page which list all mail domains.
 */
export default function AdminPage() {
  const queryClient = useQueryClient();

  const handleCreateDomain = (domain: MailDomainAdminWrite) => {
    queryClient.invalidateQueries({
      queryKey: getMaildomainsListQueryOptions().queryKey,
      exact: false,
    });
    addToast(
      <ToasterItem>
        <Trans i18nKey="admin_maildomains_list.creation_success" values={{ domain: domain.name }} components={{ strong: <strong /> }} />
      </ToasterItem>, {
        toastId: `create-domain-success:${domain.id}`,
      }
    )
  };

  return (
    <AdminLayout actions={<CreateDomainAction onCreate={handleCreateDomain} />}>
      <AdminPageContent />
    </AdminLayout>
  );
}
