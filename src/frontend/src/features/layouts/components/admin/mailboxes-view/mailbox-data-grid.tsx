import { MailboxAdmin, MailDomainAdmin, useMaildomainsMailboxesDestroy, useMaildomainsMailboxesList } from "@/features/api/gen";
import { ModalMailboxManageAccesses } from "@/features/layouts/components/admin/modal-mailbox-manage-accesses";
import { Banner } from "@/features/ui/components/banner";
import useAbility, { Abilities } from "@/hooks/use-ability";
import { DropdownMenu, Icon, IconSize, Spinner } from "@gouvfr-lasuite/ui-kit";
import { Button, DataGrid, useModals, usePagination } from "@openfun/cunningham-react";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import ModalMailboxResetPassword from "../modal-mailbox-reset-password";
import { addToast, ToasterItem } from "@/features/ui/components/toaster";
import { ModalCreateOrUpdateMailbox } from "../modal-create-update-mailbox";
import MailboxHelper from "@/features/utils/mailbox-helper";

type AdminUserDataGridProps = {
    domain: MailDomainAdmin;
    pagination: ReturnType<typeof usePagination>;
}

enum MailboxEditAction {
    UPDATE = 'update',
    RESET_PASSWORD = 'resetPassword',
    MANAGE_ACCESS = 'manageAccess',
}

export const AdminMailboxDataGrid = ({ domain, pagination }: AdminUserDataGridProps) => {
    const { t } = useTranslation();
    const { data: mailboxesData, isLoading, error, refetch: refetchMailboxes } = useMaildomainsMailboxesList(domain.id, { page: pagination.page });
    const mailboxes = mailboxesData?.data.results || [];
    const [editedMailbox, setEditedMailbox] = useState<MailboxAdmin | null>(null);
    const [editAction, setEditAction] = useState<MailboxEditAction | null>(null);
    const canManageMailboxes = useAbility(Abilities.CAN_MANAGE_MAILDOMAIN_MAILBOXES, domain);
    const deleteMailboxMutation = useMaildomainsMailboxesDestroy();
    const modals = useModals();

    const handleCloseEditUserModal = (refetch: boolean = false) => {
        setEditedMailbox(null);
        setEditAction(null);
        if (refetch) {
            refetchMailboxes();
        }
    }

    const handleResetPassword = (mailbox: MailboxAdmin) => {
        setEditAction(MailboxEditAction.RESET_PASSWORD);
        setEditedMailbox(mailbox);
    }

    const handleManageAccess = (mailbox: MailboxAdmin) => {
        setEditAction(MailboxEditAction.MANAGE_ACCESS);
        setEditedMailbox(mailbox);
    }

    const handleUpdate = (mailbox: MailboxAdmin) => {
        setEditAction(MailboxEditAction.UPDATE);
        setEditedMailbox(mailbox);
    }

    const handleDelete = async (mailbox: MailboxAdmin) => {
        const email = MailboxHelper.toString(mailbox);
        const decision = await modals.deleteConfirmationModal({
            title: <span className="c__modal__text--centered">{t('Delete mailbox {{mailbox}}', { mailbox: email })}</span>,
            children: t('Are you sure you want to delete this mailbox? This action is irreversible!'),
          });

          if (decision === 'delete') {
            deleteMailboxMutation.mutate({ maildomainPk: domain.id, id: mailbox.id }, {
              onSuccess: () => {
                refetchMailboxes();
                addToast(
                    <ToasterItem type="error">
                        <Icon name="delete" size={IconSize.SMALL} />
                        <span>{t('Mailbox {{mailbox}} has been deleted successfully.', { mailbox: email })}</span>
                    </ToasterItem>
                );
              },
            })
          }
    }

    const columns = [
        {
            id: "mailbox_type",
            headerName: t("Type"),
            size: 140,
            renderCell: ({ row }: { row: MailboxAdmin }) => {
                let typeLabel: string;
                let color: string;

                if (row.alias_of) {
                    typeLabel = t("Redirection");
                    color = "var(--c--theme--colors--info-600)";
                } else if (row.is_identity) {
                    typeLabel = t("Personal mailbox");
                    color = "var(--c--theme--colors--success-600)";
                } else {
                    typeLabel = t("Shared mailbox");
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
            headerName: t("Email address"),
            renderCell: ({ row }: { row: MailboxAdmin }) => MailboxHelper.toString(row) ,
        },
        {
            id: "accesses",
            headerName: t("Accesses"),
            renderCell: ({ row }: { row: MailboxAdmin }) => {
                if (row.accesses?.length === 0) {
                    return (
                        <span style={{ color: "var(--c--theme--colors--danger-600)" }}>
                            {t("No accesses")}
                        </span>
                    );
                }

                const otherAccessesCount = row.accesses?.length - 2;
                return row.accesses?.slice(0, 2).map((access) => access.user?.full_name || access.user?.email || t("Unknown user")).join(", ")
                + (otherAccessesCount > 0 ? ` ${
                    t("and {{count}} other users", {
                        count: otherAccessesCount,
                        defaultValue_one: "and 1 other user"
                    })
                }` : "");
            },
        },
        ...(canManageMailboxes ? [{
            id: "actions",
            headerName: t("Actions"),
            size: 160,
            renderCell: ({ row }: { row: MailboxAdmin }) => <ActionsRow
                onManageAccess={() => handleManageAccess(row)}
                onResetPassword={row.can_reset_password ? () => handleResetPassword(row) : undefined}
                onDelete={() => handleDelete(row)}
                onUpdate={() => handleUpdate(row)}
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
                    {t("Loading addresses...")}
                </Banner>
            </div>
        );
    }

    if (error) {
        return (
            <div className="admin-data-grid">
                <Banner type="error">
                    {t("Error while loading addresses")}
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
                    <ModalCreateOrUpdateMailbox
                        isOpen={editAction === MailboxEditAction.UPDATE}
                        mailbox={editedMailbox}
                        onClose={handleCloseEditUserModal}
                        onSuccess={refetchMailboxes}
                    />
                    <ModalMailboxManageAccesses
                        isOpen={editAction === MailboxEditAction.MANAGE_ACCESS}
                        onClose={handleCloseEditUserModal}
                        mailbox={editedMailbox}
                        domainId={domain.id}
                        onAccessChange={refetchMailboxes}
                    />
                    <ModalMailboxResetPassword
                        isOpen={editAction === MailboxEditAction.RESET_PASSWORD}
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
    onManageAccess: () => void;
    onResetPassword?: () => void;
    onDelete: () => void;
    onUpdate: () => void;
};

const ActionsRow = ({ onManageAccess, onResetPassword, onDelete, onUpdate }: ActionsRowProps) => {
    const [isMoreActionsOpen, setMoreActionsOpen] = useState<boolean>(false);
    const { t } = useTranslation();

    return (
        <div className="flex-row" style={{ gap: "var(--c--theme--spacings--2xs)" }}>
            <Button
                color="secondary"
                size="nano"
                onClick={onManageAccess}
                style={{ paddingInline: "var(--c--theme--spacings--xs)" }}
            >
                {t('Manage accesses')}
            </Button>
            <DropdownMenu
                isOpen={isMoreActionsOpen}
                onOpenChange={setMoreActionsOpen}
                options={[
                    {
                        label: t('Edit'),
                        icon: <Icon name="edit" size={IconSize.SMALL} />,
                        callback: onUpdate,
                        showSeparator: !onResetPassword,
                    },
                    ...(onResetPassword ? [{
                        icon: <Icon name="lock" size={IconSize.SMALL} />,
                        label: t('Reset password'),
                        callback: onResetPassword,
                        showSeparator: true,
                    },
                    ] : []),
                    {
                        label: t('Delete'),
                        icon: <Icon name="delete" size={IconSize.SMALL} />,
                        callback: onDelete,
                    }
                ]}
            >
                <Button
                    color="secondary"
                    size="nano"
                    onClick={() => setMoreActionsOpen(true)}
                >
                    <Icon name="more_vert" size={IconSize.SMALL} />
                    <span className="c__offscreen">{t('More')}</span>
                </Button>
            </DropdownMenu>
        </div >
    );
}
