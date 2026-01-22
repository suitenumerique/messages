import { Icon, IconSize, IconType, Spinner } from "@gouvfr-lasuite/ui-kit";
import { Button, Column, DataGrid, useModal, useModals } from "@gouvfr-lasuite/cunningham-react";
import { useTranslation } from "react-i18next";
import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import {
    Mailbox,
    Channel,
    useMailboxesChannelsList,
    useMailboxesChannelsDestroy,
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
    const [selectedChannel, setSelectedChannel] = useState<Channel | undefined>();
    const queryClient = useQueryClient();

    const invalidateChannels = async () => {
        await queryClient.invalidateQueries({ queryKey: [getMailboxesChannelsListUrl(mailbox.id)], exact: false });
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
            renderCell: ({ row }) => (
                <div className="flex-row flex-align-center" style={{ gap: "var(--c--globals--spacings--xs)" }}>
                    <Icon name={getChannelTypeIcon(row.type)} type={IconType.OUTLINED} size={IconSize.SMALL} />
                    <span>{row.name}</span>
                </div>
            ),
        },
        {
            id: "type",
            headerName: t("Type"),
            size: 150,
            renderCell: ({ row }) => getChannelTypeLabel(row.type, t),
        },
        {
            id: "actions",
            size: 154,
            headerName: t("Actions"),
            renderCell: ({ row }) => (
                <div className="flex-row flex-justify-start" style={{ width: "100%", gap: "var(--c--globals--spacings--2xs)" }}>
                    <Button
                        variant="bordered"
                        size="small"
                        onClick={() => handleModifyRow(row)}
                    >
                        {t("Modify")}
                    </Button>
                    <Button
                        color="error"
                        size="small"
                        onClick={() => handleDeleteRow(row)}
                        disabled={isDeleting}
                        icon={isDeleting ? <Spinner size="sm" /> : <Icon name="delete" size={IconSize.SMALL} />}
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
        <section className="admin-page__body">
            <div className="admin-data-grid">
                <DataGrid
                    columns={columns}
                    rows={channels?.data ?? []}
                    onSortModelChange={() => undefined}
                    enableSorting={false}
                    emptyPlaceholderLabel={t("No integration found")}
                />
                <ModalComposeIntegration
                    isOpen={modal.isOpen}
                    onClose={() => {
                        modal.close();
                        setSelectedChannel(undefined);
                    }}
                    channel={selectedChannel}
                    onSuccess={invalidateChannels}
                />
            </div>
        </section>
    );
};
