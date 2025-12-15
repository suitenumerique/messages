import { MailboxAdmin, MailDomainAdmin, useMaildomainsMailboxesDestroy, useMaildomainsMailboxesList } from "@/features/api/gen";
import { ModalMailboxManageAccesses } from "@/features/layouts/components/admin/modal-mailbox-manage-accesses";
import { ModalMailboxManageSettings } from "@/features/layouts/components/admin/modal-mailbox-manage-settings";
import { Banner } from "@/features/ui/components/banner";
import useAbility, { Abilities } from "@/hooks/use-ability";
import { IconType, DropdownMenu, Icon, IconSize, Spinner } from "@gouvfr-lasuite/ui-kit";
import { Button, DataGrid, Tooltip, useModals, usePagination } from "@gouvfr-lasuite/cunningham-react";
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
    MANAGE_SETTINGS = 'manageSettings',
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

    const handleManageSettings = (mailbox: MailboxAdmin) => {
        setEditAction(MailboxEditAction.MANAGE_SETTINGS);
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
            size: 200,
            renderCell: ({ row }: { row: MailboxAdmin }) => {
                let typeLabel: string;
                let color: string;

                if (row.alias_of) {
                    typeLabel = t("Redirection");
                    color = "var(--c--contextuals--content--semantic--info--tertiary)";
                } else if (row.is_identity) {
                    typeLabel = t("Personal mailbox");
                    color = "var(--c--contextuals--content--semantic--success--tertiary)";
                } else {
                    typeLabel = t("Shared mailbox");
                    color = "var(--c--contextuals--content--semantic--warning--tertiary)";
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
            renderCell: ({ row }: { row: MailboxAdmin }) => <strong>{MailboxHelper.toString(row)}</strong>,
        },
        {
            id: "accesses",
            headerName: t("Accesses"),
            size: 150,
            align: "right",
            renderCell: ({ row }: { row: MailboxAdmin }) => {
                const otherAccessesCount = row.accesses?.length - 2;
                const accessesTooltip = row.accesses?.slice(0, 2).map((access) => access.user?.full_name || access.user?.email || t("Unknown user")).join(", ")
                    + (otherAccessesCount > 0 ? ` ${t("and {{count}} other users", {
                        count: otherAccessesCount,
                        defaultValue_one: "and 1 other user"
                    })
                            }` : "");
                return (
                    <Tooltip content={row.accesses.length ? accessesTooltip : t("Click to add accesses")} placement="right">
                        <Button
                            size="nano"
                            variant="tertiary"
                            color={row.accesses.length ? "brand" : "warning"}
                            icon={<Icon name="group" type={IconType.FILLED} />}
                            onClick={() => handleManageAccess(row)}
                            style={{ paddingInline: "var(--c--globals--spacings--xs)" }}
                        >
                            {row.accesses.length ? row.accesses.length : t("No accesses")}
                        </Button>
                    </Tooltip>
                );


            },
        },
        ...(canManageMailboxes ? [{
            id: "actions",
            size: 150,
            renderCell: ({ row }: { row: MailboxAdmin }) => <ActionsRow
                onManageAccess={() => handleManageAccess(row)}
                onResetPassword={row.can_reset_password ? () => handleResetPassword(row) : undefined}
                onDelete={() => handleDelete(row)}
                onUpdate={() => handleUpdate(row)}
                onManageSettings={() => handleManageSettings(row)}
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
                emptyPlaceholderLabel={t("No addresses found")}
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
                    <ModalMailboxManageSettings
                        key={`${editedMailbox.id}-${editedMailbox.updated_at}`}
                        isOpen={editAction === MailboxEditAction.MANAGE_SETTINGS}
                        onClose={() => handleCloseEditUserModal(false)}
                        onSuccess={refetchMailboxes}
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
    onManageSettings: () => void;
};

const ActionsRow = ({ onManageAccess, onResetPassword, onDelete, onUpdate, onManageSettings }: ActionsRowProps) => {
    const [isMoreActionsOpen, setMoreActionsOpen] = useState<boolean>(false);
    const { t } = useTranslation();

    return (
        <div className="flex-row" style={{ gap: "var(--c--globals--spacings--2xs)" }}>
            <Button
                variant="bordered"
                size="nano"
                onClick={onUpdate}
                style={{ paddingInline: "var(--c--globals--spacings--xs)" }}
            >
                {t('Edit')}
            </Button>
            <DropdownMenu
                isOpen={isMoreActionsOpen}
                onOpenChange={setMoreActionsOpen}
                options={[
                    {
                        icon: <Icon name="group" size={IconSize.SMALL} />,
                        label: t('Manage accesses'),
                        callback: onManageAccess,
                        showSeparator: onResetPassword ? false : true,
                    },
                    {
                        label: t('Settings'),
                        icon: <Icon name="tune" size={IconSize.SMALL} />,
                        callback: onManageSettings,
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
                <Tooltip content={t('More options')} placement="left">
                    <Button
                        color="brand"
                        variant="tertiary"
                        size="nano"
                        onClick={() => setMoreActionsOpen(true)}
                        style={{ paddingInline: "var(--c--globals--spacings--3xs)" }}
                    >
                        <Icon name="more_horiz" size={IconSize.SMALL} />
                        <span className="c__offscreen">{t('More')}</span>
                    </Button>
                </Tooltip>
            </DropdownMenu>
        </div >
    );
}
