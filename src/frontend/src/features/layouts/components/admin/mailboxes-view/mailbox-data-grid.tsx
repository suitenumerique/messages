import { MailboxAdmin, MailDomainAdmin, useMaildomainsMailboxesList } from "@/features/api/gen";
import { ModalMailboxManageAccesses } from "@/features/layouts/components/admin/modal-manage-accesses";
import { Banner } from "@/features/ui/components/banner";
import useAbility, { Abilities } from "@/hooks/use-ability";
import { Spinner } from "@gouvfr-lasuite/ui-kit";
import { Button, DataGrid } from "@openfun/cunningham-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";

type AdminUserDataGridProps = {
    domain: MailDomainAdmin;
}

export const AdminMailboxDataGrid = ({ domain }: AdminUserDataGridProps) => {
    const { t } = useTranslation();
    const { data: mailboxesData, isLoading, error, refetch: refetchMailboxes } = useMaildomainsMailboxesList(domain.id);
    const mailboxes = mailboxesData?.data.results || [];
    const [editedMailboxId, setEditedMailboxId] = useState<string | null>(null);
    const editedMailbox = mailboxes.find((mailbox) => mailbox.id === editedMailboxId);
    const canManageMailboxes = useAbility(Abilities.CAN_MANAGE_MAILDOMAIN_MAILBOXES, domain);
    const handleCloseEditUserModal = (refetch: boolean = false) => {
        setEditedMailboxId(null);
        if (refetch) {
            refetchMailboxes();
        }
    }

    const columns = [
        {
            id: "mailbox_type",
            headerName: t("admin_maildomains_details.datagrid_headers.type"),
            size: 140,
            renderCell: ({ row }: { row: MailboxAdmin }) => {
                let typeLabel: string;
                let color: string;

                if (row.alias_of) {
                    typeLabel = t("admin_maildomains_details.datagrid_row_labels.alias");
                    color = "var(--c--theme--colors--info-600)";
                } else if (row.is_identity) {
                    typeLabel = t("admin_maildomains_details.datagrid_row_labels.personal_mailbox");
                    color = "var(--c--theme--colors--success-600)";
                } else {
                    typeLabel = t("admin_maildomains_details.datagrid_row_labels.shared_mailbox");
                    color = "var(--c--theme--colors--success-600)";
                }

                return (
                    <span style={{ color }}>
                        {typeLabel}
                    </span>
                );
            },
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
                if (row.accesses?.length === 0) {
                    return (
                        <span style={{ color: "var(--c--theme--colors--danger-600)" }}>
                            {t("admin_maildomains_details.datagrid_row_labels.no_accesses")}
                        </span>
                    );
                }

                return row.accesses?.map((access) => {
                    return access.user?.full_name || access.user?.email || t("admin_maildomains_details.datagrid_row_labels.unknown_user");
                }).join(", ");
            },
        },
        ...(canManageMailboxes ? [{
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
        }] : []),
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
            {canManageMailboxes && (
                <ModalMailboxManageAccesses
                    isOpen={!!editedMailbox}
                    onClose={handleCloseEditUserModal}
                    mailbox={editedMailbox}
                    domainId={domain.id}
                    onAccessChange={refetchMailboxes}
                />
            )}
        </div>
    );
}
