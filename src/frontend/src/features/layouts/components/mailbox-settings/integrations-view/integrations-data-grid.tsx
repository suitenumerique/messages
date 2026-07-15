import { Icon, IconSize, IconType, Spinner } from "@gouvfr-lasuite/ui-kit";
import { Trash } from "@gouvfr-lasuite/ui-kit/icons";
import { Button, Column, DataGrid, Switch, useModal, useModals } from "@gouvfr-lasuite/cunningham-react";
import { useTranslation } from "react-i18next";
import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import {
    Mailbox,
    Channel,
    useMailboxesChannelsList,
    useMailboxesChannelsDestroy,
    useMailboxesChannelsPartialUpdate,
    getMailboxesChannelsListUrl
} from "@/features/api/gen";
import { Banner } from "@/features/ui/components/banner";
import { addToast, ToasterItem } from "@/features/ui/components/toaster";
import { ModalComposeIntegration } from "../modal-compose-integration";
import { handle } from "@/features/utils/errors";

type IntegrationsDataGridProps = {
    mailbox: Mailbox;
}

const getChannelTypeLabel = (type: string | undefined, t: (key: string) => string) => {
    switch (type) {
        case "widget":
            return t("Widget");
        case "api_key":
            return t("API Key");
        case "webhook":
            return t("Webhook");
        default:
            return type;
    }
};

const getChannelTypeIcon = (type: string | undefined) => {
    switch (type) {
        case "widget":
            return "widgets";
        case "api_key":
            return "key";
        case "webhook":
            return "webhook";
        default:
            return "integration_instructions";
    }
};

export const IntegrationsDataGrid = ({ mailbox }: IntegrationsDataGridProps) => {
    const { t } = useTranslation();
    const modals = useModals();
    const modal = useModal();
    const { data: channels, isLoading, error } = useMailboxesChannelsList(
        mailbox.id,
        {
            query: {
                enabled: !!mailbox.id,
            },
        }
    );
    const { mutateAsync: deleteChannel, isPending: isDeleting } = useMailboxesChannelsDestroy();
    const { mutate: updateChannel } = useMailboxesChannelsPartialUpdate();
    const [selectedChannel, setSelectedChannel] = useState<Channel | undefined>();
    // Channels with an in-flight pause/resume request — used to disable the
    // toggle so it can't be double-fired while the PATCH is pending.
    const [pendingActiveIds, setPendingActiveIds] = useState<Set<string>>(new Set());
    const queryClient = useQueryClient();

    const invalidateChannels = async () => {
        await queryClient.invalidateQueries({ queryKey: [getMailboxesChannelsListUrl(mailbox.id)], exact: false });
    }

    const handleToggleActive = (channel: Channel, isActive: boolean) => {
        setPendingActiveIds((prev) => new Set(prev).add(channel.id));
        updateChannel(
            { mailboxId: mailbox.id, id: channel.id, data: { is_active: isActive } },
            {
                onSuccess: async () => {
                    await invalidateChannels();
                    addToast(
                        <ToasterItem type="info">
                            <span>
                                {isActive
                                    ? t('Integration "{{name}}" resumed.', { name: channel.name })
                                    : t('Integration "{{name}}" paused.', { name: channel.name })}
                            </span>
                        </ToasterItem>,
                    );
                },
                onError: (error) => {
                    handle(error);
                    addToast(
                        <ToasterItem type="error">
                            <span>{t("Failed to update integration.")}</span>
                        </ToasterItem>,
                    );
                },
                onSettled: () => setPendingActiveIds((prev) => {
                    const next = new Set(prev);
                    next.delete(channel.id);
                    return next;
                }),
            },
        );
    }

    const handleModifyRow = (channel: Channel) => {
        setSelectedChannel(channel);
        modal.open();
    }

    const handleDeleteRow = async (channel: Channel) => {
        const decision = await modals.deleteConfirmationModal({
            title: <span className="c__modal__text--centered">{t('Delete integration "{{name}}"', { name: channel.name })}</span>,
            children: t('Are you sure you want to delete this integration? This action is irreversible!'),
        });
        if (decision === 'delete') {
            try {
                await deleteChannel({ mailboxId: mailbox.id, id: channel.id });
                await invalidateChannels();
                addToast(
                    <ToasterItem type="info">
                        <span>{t("Integration deleted!")}</span>
                    </ToasterItem>,
                );
            } catch (error) {
                handle(error);
                addToast(
                    <ToasterItem type="error">
                        <span>{t("Failed to delete integration.")}</span>
                    </ToasterItem>,
                );
            }
        }
    }

    const columns: Column<Channel>[] = [
        {
            id: "name",
            headerName: t("Name"),
            // Paused channels are dimmed so an at-a-glance scan tells active
            // from paused. The whole cell content carries the muted opacity.
            renderCell: ({ row }) => (
                <div
                    className="flex-row flex-align-center"
                    style={{ gap: "var(--c--globals--spacings--xs)", opacity: row.is_active === false ? 0.5 : 1 }}
                >
                    <Icon name={getChannelTypeIcon(row.type)} type={IconType.OUTLINED} size={IconSize.SMALL} />
                    <span>{row.name}</span>
                </div>
            ),
        },
        {
            id: "type",
            headerName: t("Type"),
            size: 150,
            renderCell: ({ row }) => (
                <span style={{ opacity: row.is_active === false ? 0.5 : 1 }}>
                    {getChannelTypeLabel(row.type, t)}
                </span>
            ),
        },
        {
            id: "is_active",
            headerName: t("Active"),
            size: 110,
            renderCell: ({ row }) => (
                <Switch
                    checked={row.is_active !== false}
                    disabled={pendingActiveIds.has(row.id)}
                    onChange={(event) => handleToggleActive(row, event.target.checked)}
                    aria-label={row.is_active !== false ? t("Pause integration") : t("Resume integration")}
                />
            ),
        },
        {
            id: "actions",
            size: 130,
            headerName: t("Actions"),
            renderCell: ({ row }) => (
                <div className="flex-row flex-justify-start" style={{ width: "100%", gap: "var(--c--globals--spacings--2xs)" }}>
                    <Button
                        variant="tertiary"
                        size="nano"
                        onClick={() => handleModifyRow(row)}
                    >
                        {t("Modify")}
                    </Button>
                    <Button
                        color="error"
                        variant="tertiary"
                        size="nano"
                        onClick={() => handleDeleteRow(row)}
                        disabled={isDeleting}
                        icon={isDeleting ? <Spinner size="sm" /> : <Trash size="small" />}
                        aria-label={t("Delete")}
                    />
                </div>
            ),
        },
    ];

    if (isLoading) {
        return (
            <div className="admin-data-grid">
                <Banner type="info" icon={<Spinner />}>
                    {t("Loading integrations...")}
                </Banner>
            </div>
        );
    }

    if (error) {
        return (
            <div className="admin-data-grid">
                <Banner type="error">
                    {t("Error while loading integrations")}
                </Banner>
            </div>
        );
    }

    return (
        <div className="admin-data-grid">
            <DataGrid
                columns={columns}
                rows={channels?.data ?? []}
                onSortModelChange={() => undefined}
                enableSorting={false}
                emptyPlaceholderLabel={t("No integrations")}
            />
            <ModalComposeIntegration
                isOpen={modal.isOpen}
                onClose={() => {
                    modal.close();
                    setSelectedChannel(undefined);
                }}
                mailbox={mailbox}
                channel={selectedChannel}
                onSuccess={invalidateChannels}
            />
        </div>
    );
};
