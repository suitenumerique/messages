import { MailboxAdmin, MailDomainAdmin, useMaildomainsMailboxesDestroy, useMaildomainsMailboxesList, useMaildomainsMailboxesResetTotp, useMaildomainsMailboxesSetMandatoryTotp } from "@/features/api/gen";
import { ModalMailboxManageAccesses } from "@/features/layouts/components/admin/modal-mailbox-manage-accesses";
import { Banner } from "@/features/ui/components/banner";
import useAbility, { Abilities } from "@/hooks/use-ability";
import { IconType, DropdownMenu, Icon, IconSize, Spinner, DropdownMenuItem } from "@gouvfr-lasuite/ui-kit";
import { Button, DataGrid, Switch, Tooltip, useModals, usePagination } from "@gouvfr-lasuite/cunningham-react";
import { keepPreviousData } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import ModalMailboxResetPassword from "../modal-mailbox-reset-password";
import { addToast, ToasterItem } from "@/features/ui/components/toaster";
import { ModalCreateOrUpdateMailbox } from "../modal-create-update-mailbox";
import MailboxHelper from "@/features/utils/mailbox-helper";
import { FEATURE_KEYS, useFeatureFlag } from "@/hooks/use-feature";
import { EmptyCell } from "@/features/ui/components/empty-cell";

type AdminUserDataGridProps = {
    domain: MailDomainAdmin;
    pagination: ReturnType<typeof usePagination>;
    searchQuery?: string;
}

enum MailboxEditAction {
    UPDATE = 'update',
    RESET_PASSWORD = 'resetPassword',
    MANAGE_ACCESS = 'manageAccess',
}

export const AdminMailboxDataGrid = ({ domain, pagination, searchQuery }: AdminUserDataGridProps) => {
    const { t, i18n } = useTranslation();
    const trimmedQuery = (searchQuery ?? "").trim();
    const { data: mailboxesData, isLoading, error, refetch: refetchMailboxes } = useMaildomainsMailboxesList(domain.id, {
        page: pagination.page,
        ...(trimmedQuery ? { q: trimmedQuery } : {}),
    }, {
        query: { placeholderData: keepPreviousData },
    });
    const mailboxes = mailboxesData?.data.results || [];
    const [editedMailbox, setEditedMailbox] = useState<MailboxAdmin | null>(null);
    const [editAction, setEditAction] = useState<MailboxEditAction | null>(null);
    // Tracks which mailbox rows are mid-toggle so we only disable those switches
    // (a single global `isPending` would lock every row when any toggle is in flight,
    // and a scalar would lose the first id when a second toggle starts).
    const [pendingTotpMailboxIds, setPendingTotpMailboxIds] = useState<Set<string>>(new Set());
    const canManageMailboxes = useAbility(Abilities.CAN_MANAGE_MAILDOMAIN_MAILBOXES, domain);
    const deleteMailboxMutation = useMaildomainsMailboxesDestroy();
    const setMandatoryTotpMutation = useMaildomainsMailboxesSetMandatoryTotp();
    const resetTotpMutation = useMaildomainsMailboxesResetTotp();
    const isMandatoryTotpEnabled = useFeatureFlag(FEATURE_KEYS.MAILDOMAIN_MANAGE_TOTP);
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

    const handleToggleMandatoryTotp = (mailbox: MailboxAdmin, enabled: boolean) => {
        setPendingTotpMailboxIds((prev) => new Set(prev).add(mailbox.id));
        setMandatoryTotpMutation.mutate(
            { maildomainPk: domain.id, id: mailbox.id, data: { enabled } },
            {
                onSuccess: () => {
                    refetchMailboxes();
                    addToast(
                        <ToasterItem>
                            <Icon name="security" size={IconSize.SMALL} />
                            <span>
                                {enabled
                                    ? t('Mandatory 2FA enabled for {{mailbox}}.', { mailbox: MailboxHelper.toString(mailbox) })
                                    : t('Mandatory 2FA disabled for {{mailbox}}.', { mailbox: MailboxHelper.toString(mailbox) })}
                            </span>
                        </ToasterItem>
                    );
                },
                onSettled: () => setPendingTotpMailboxIds((prev) => {
                    const next = new Set(prev);
                    next.delete(mailbox.id);
                    return next;
                }),
            }
        );
    }

    const handleResetTotp = async (mailbox: MailboxAdmin) => {
        const email = MailboxHelper.toString(mailbox);
        const decision = await modals.confirmationModal({
            title: <span className="c__modal__text--centered">{t('Reset 2FA for {{mailbox}}', { mailbox: email })}</span>,
            children: t('Existing 2FA credentials will be removed. The user will be asked to re-enroll on next login.'),
        });
        if (decision !== 'yes') return;

        resetTotpMutation.mutate(
            { maildomainPk: domain.id, id: mailbox.id },
            {
                onSuccess: () => {
                    addToast(
                        <ToasterItem>
                            <Icon name="security" size={IconSize.SMALL} />
                            <span>{t('2FA has been reset for {{mailbox}}.', { mailbox: email })}</span>
                        </ToasterItem>
                    );
                },
            }
        );
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
            id: "last_accessed_at",
            headerName: t("Last access"),
            size: 160,
            renderCell: ({ row }: { row: MailboxAdmin }) =>
                row.last_accessed_at
                    ? new Date(row.last_accessed_at).toLocaleDateString(i18n.resolvedLanguage)
                    : <EmptyCell />,
        },
        ...(isMandatoryTotpEnabled && domain.identity_sync ? [{
            id: "mandatory_totp",
            headerName: t("Mandatory 2FA"),
            size: 160,
            renderCell: ({ row }: { row: MailboxAdmin }) => {
                if (row.has_mandatory_totp === null || row.has_mandatory_totp === undefined) {
                    return <EmptyCell tooltip={t('Only available for personal mailboxes in identity-synced domains.')} />;
                }
                return (
                    <Switch
                        checked={Boolean(row.has_mandatory_totp)}
                        disabled={!canManageMailboxes || pendingTotpMailboxIds.has(row.id)}
                        onChange={(event) => handleToggleMandatoryTotp(row, event.target.checked)}
                        aria-label={t('Mandatory 2FA')}
                    />
                );
            },
        }] : []),
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
                onResetTotp={isMandatoryTotpEnabled && domain.identity_sync && row.has_mandatory_totp !== null && row.has_mandatory_totp !== undefined
                    ? () => handleResetTotp(row)
                    : undefined}
                onDelete={() => handleDelete(row)}
                onUpdate={() => handleUpdate(row)}
            />,
        }] : []),
    ];

    useEffect(() => {
        if (mailboxesData?.data.count !== undefined) {
            pagination.setPagesCount(
                Math.max(1, Math.ceil(mailboxesData.data.count / pagination.pageSize))
            );
        }
    }, [mailboxesData?.data.count, pagination.pageSize, pagination.setPagesCount]);

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
                emptyPlaceholderLabel={t("No addresses")}
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
    onResetTotp?: () => void;
    onDelete: () => void;
    onUpdate: () => void;
};

const ActionsRow = ({ onManageAccess, onResetPassword, onResetTotp, onDelete, onUpdate }: ActionsRowProps) => {
    const [isMoreActionsOpen, setMoreActionsOpen] = useState<boolean>(false);
    const { t } = useTranslation();

    // Build options in display order, then put a separator before the last item
    // (Delete) so the destructive action is visually grouped on its own.
    const secondaryActions: DropdownMenuItem[] = [
        { icon: <Icon name="group" size={IconSize.SMALL} />, label: t('Manage accesses'), callback: onManageAccess },
        ...(onResetPassword ? [{ icon: <Icon name="lock" size={IconSize.SMALL} />, label: t('Reset password'), callback: onResetPassword }] : []),
        ...(onResetTotp ? [{ icon: <Icon name="security" size={IconSize.SMALL} />, label: t('Reset 2FA'), callback: onResetTotp }] : []),
    ];
    const destructive: DropdownMenuItem = {
      icon: <Icon name="delete" size={IconSize.SMALL} />, label: t('Delete'), callback: onDelete, variant: 'danger'
    };
    const options = [
        ...secondaryActions.map((opt, i) => ({
            ...opt,
            showSeparator: i === secondaryActions.length - 1,
        })),
        destructive,
    ];

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
                options={options}
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
