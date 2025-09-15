import { MailboxAdmin, MailDomainAdmin, useMaildomainsMailboxesList } from "@/features/api/gen";
import { ModalMailboxManageAccesses } from "@/features/layouts/components/admin/modal-mailbox-manage-accesses";
import { Banner } from "@/features/ui/components/banner";
import useAbility, { Abilities } from "@/hooks/use-ability";
import { DropdownMenu, Icon, IconSize, Spinner } from "@gouvfr-lasuite/ui-kit";
import { Button, DataGrid, usePagination } from "@openfun/cunningham-react";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import ModalMailboxResetPassword from "../modal-mailbox-reset-password";

type AdminUserDataGridProps = {
    domain: MailDomainAdmin;
    pagination: ReturnType<typeof usePagination>;
}

export const AdminMailboxDataGrid = ({ domain, pagination }: AdminUserDataGridProps) => {
    const { t } = useTranslation();
    const { data: mailboxesData, isLoading, error, refetch: refetchMailboxes } = useMaildomainsMailboxesList(domain.id, { page: pagination.page });
    const mailboxes = mailboxesData?.data.results || [];
    const [editedMailbox, setEditedMailbox] = useState<MailboxAdmin | null>(null);
    const [editAction, setEditAction] = useState<'edit' | 'resetPassword' | null>(null);
    const canManageMailboxes = useAbility(Abilities.CAN_MANAGE_MAILDOMAIN_MAILBOXES, domain);
    const handleCloseEditUserModal = (refetch: boolean = false) => {
        setEditedMailbox(null);
        setEditAction(null);
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
            id: "accesses",
            headerName: t("admin_maildomains_details.datagrid_headers.accesses"),
            renderCell: ({ row }: { row: MailboxAdmin }) => {
                if (row.accesses?.length === 0) {
                    return (
                        <span style={{ color: "var(--c--theme--colors--danger-600)" }}>
                            {t("admin_maildomains_details.datagrid_row_labels.no_accesses")}
                        </span>
                    );
                }

                const otherAccessesCount = row.accesses?.length - 2;
                return row.accesses?.slice(0, 2).map((access) => {
                    return access.user?.full_name || access.user?.email || t("admin_maildomains_details.datagrid_row_labels.unknown_user");
                }).join(", ") + (otherAccessesCount > 0 ? ` ${t("admin_maildomains_details.datagrid_row_labels.other_user", { count: otherAccessesCount })}` : "");
            },
        },
        ...(canManageMailboxes ? [{
            id: "actions",
            headerName: t("admin_maildomains_details.datagrid_headers.actions"),
            size: 160,
            renderCell: ({ row }: { row: MailboxAdmin }) => <ActionsRow
                onEdit={() => {
                    setEditAction('edit');
                    setEditedMailbox(row);
                }}
                onResetPassword={row.can_reset_password ? (() => {
                    setEditAction('resetPassword')
                    setEditedMailbox(row);
                }) : undefined}
            />,
        }] : []),
    ];

    useEffect(() => {
        if (!pagination.pagesCount && mailboxesData?.data.count) {
            pagination.setPagesCount(Math.ceil(mailboxesData.data.count / pagination.pageSize));
        }
    }, [mailboxesData?.data.count, pagination.pageSize]);

    useEffect(() => {
        if (editedMailbox) {
            const updatedMailbox = mailboxes.find((mailbox) => mailbox.id === editedMailbox.id);
            if (updatedMailbox) setEditedMailbox(updatedMailbox);
        }
    }, [mailboxes, editedMailbox]);

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
                pagination={pagination}
                enableSorting={false}
                onSortModelChange={() => undefined}
            />
            {canManageMailboxes && editedMailbox && (
                <>
                    <ModalMailboxManageAccesses
                        isOpen={editAction === 'edit'}
                        onClose={handleCloseEditUserModal}
                        mailbox={editedMailbox}
                        domainId={domain.id}
                        onAccessChange={refetchMailboxes}
                    />
                    <ModalMailboxResetPassword
                        isOpen={editAction === 'resetPassword'}
                        onClose={handleCloseEditUserModal}
                        mailbox={editedMailbox}
                        domainId={domain.id}
                    />
                </>
            )}
        </div>
    );
}

type ActionsRowProps = {
    onEdit: () => void;
    onResetPassword?: () => void;
};

const ActionsRow = ({ onEdit, onResetPassword }: ActionsRowProps) => {
    const [isMoreActionsOpen, setMoreActionsOpen] = useState<boolean>(false);
    const { t } = useTranslation();

    return (
        <div className="flex-row" style={{ gap: "var(--c--theme--spacings--2xs)" }}>
            <Button
                color="secondary"
                size="nano"
                onClick={onEdit}
                style={{ paddingInline: "var(--c--theme--spacings--xs)" }}
            >
                {t('admin_maildomains_details.actions.manage_accesses')}
            </Button>
            {onResetPassword &&
                <DropdownMenu
                    isOpen={isMoreActionsOpen}
                    onOpenChange={setMoreActionsOpen}
                    options={[
                        {
                            label: t('admin_maildomains_details.actions.reset_password'),
                            callback: onResetPassword,
                        },
                    ]}
                >
                    <Button
                        color="secondary"
                        size="nano"
                        onClick={() => setMoreActionsOpen(true)}
                    >
                        <Icon name="more_vert" size={IconSize.SMALL} />
                        <span className="c__offscreen">{t('admin_maildomains_details.actions.more')}</span>
                    </Button>
                </DropdownMenu>
            }
        </div>
    );
}
