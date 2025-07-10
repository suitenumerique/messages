import { AdminLayout } from "@/features/layouts/components/admin/admin-layout";
import { useState } from "react";
import { Button, DataGrid, useModal } from "@openfun/cunningham-react";
import { useMaildomainsMailboxesList } from "@/features/api/gen/maildomains/maildomains";
import { useTranslation } from "react-i18next";
import { Spinner } from "@gouvfr-lasuite/ui-kit";
import { useAdminMailDomain } from "@/features/providers/admin-maildomain";
import { MailboxAdmin, MailDomainAdmin } from "@/features/api/gen";
import { Banner } from "@/features/ui/components/banner";
import { ModalMailboxManageAccesses } from "@/features/layouts/components/admin/modal-manage-accesses";
import { ModalCreateAddress } from "@/features/layouts/components/admin/modal-create-address";

type AdminUserDataGridProps = {
  domain: MailDomainAdmin;
}

function AdminUserDataGrid({ domain }: AdminUserDataGridProps) {
  const { t } = useTranslation();
  const { data: mailboxesData, isLoading, error, refetch: refetchMailboxes } = useMaildomainsMailboxesList(domain.id);
  const mailboxes = mailboxesData?.data.results || [];
  const [editedMailboxId, setEditedMailboxId] = useState<string | null>(null);
  const editedMailbox = mailboxes.find((mailbox) => mailbox.id === editedMailboxId);
  const handleCloseEditUserModal = (refetch: boolean = false) => {
    setEditedMailboxId(null);
    if (refetch) {
      refetchMailboxes();
    }
  }

  const columns = [
    {
      id: "alias_status",
      headerName: t("admin_maildomains_details.datagrid_headers.type"),
      size: 100,
      renderCell: ({ row }: { row: MailboxAdmin }) => (
        <span style={{
          color: row.alias_of ? "var(--c--theme--colors--info-600)" : "var(--c--theme--colors--success-600)"
        }}>
          {row.alias_of ? t("admin_maildomains_details.datagrid_row_labels.alias") : t("admin_maildomains_details.datagrid_row_labels.mailbox")}
        </span>
      ),
    },
    {
      id: "email",
      headerName: t("admin_maildomains_details.datagrid_headers.email"),
      renderCell: ({ row }: { row: MailboxAdmin }) => `${row.local_part}@${row.domain_name}`,
    },
    {
      id: "user_name",
      headerName: t("admin_maildomains_details.datagrid_headers.accesses"),
      renderCell: ({ row }: { row: MailboxAdmin }) => {
        if (row.accesses?.length === 0) return t("admin_maildomains_details.datagrid_row_labels.no_accesses");

        return row.accesses?.map((access) => {
          return access.user?.full_name || access.user?.short_name || t("admin_maildomains_details.datagrid_row_labels.unknown_user");
        }).join(", ");
      },
    },
    {
      id: "actions",
      headerName: t("admin_maildomains_details.datagrid_headers.actions"),
      size: 133,
      renderCell: ({ row }: { row: MailboxAdmin }) => (
        <>
          <Button
            color="secondary"
            size="small"
            onClick={() => {
              setEditedMailboxId(row.id);
            }}
          >
            {t('admin_maildomains_details.actions.manage_accesses')}
          </Button>
        </>
      ),
    },
  ];

  if (isLoading) {
    return (
      <div className="admin-data-grid">
        <Banner type="info" icon={<Spinner />}>
          {t("admin_maildomains_details.loading")}
        </Banner>
      </div>
    );
  }

  if (error) {
    return (
      <div className="admin-data-grid">
        <Banner type="error">
          {t("admin_maildomains_details.errors.failed_to_load_adresses")}
        </Banner>
      </div>
    );
  }

  return (
    <div className="admin-data-grid">
      <DataGrid
        columns={columns}
        rows={mailboxes}
      />
      <ModalMailboxManageAccesses
        isOpen={!!editedMailbox}
        onClose={handleCloseEditUserModal}
        mailbox={editedMailbox}
        domainId={domain.id}
        onAccessChange={refetchMailboxes}
      />
    </div>
  );
}

const AdminDomainPageContent = () => {
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

  return <AdminUserDataGrid domain={selectedMailDomain} />;
}

/**
 * Admin page which list all mailboxes for a given domain and allow to manage them.
 */
export default function AdminDomainPage() {
  const modal = useModal();
  const { t } = useTranslation();

  return (
    <AdminLayout
      currentTab="addresses"
      actions={
        <>
          <Button color="primary" onClick={modal.open}>
            {t("admin_maildomains_details.actions.new_address")}
          </Button>
          <ModalCreateAddress
            isOpen={modal.isOpen}
            onClose={modal.close}
          />
        </>
      }
    >
      <AdminDomainPageContent />
    </AdminLayout>
  );
}
