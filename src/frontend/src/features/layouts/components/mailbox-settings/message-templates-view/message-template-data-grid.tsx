import { Icon, IconSize, Spinner } from "@gouvfr-lasuite/ui-kit";
import { Button, Column, DataGrid, useModal, useModals } from "@gouvfr-lasuite/cunningham-react";
import { useTranslation } from "react-i18next";
import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Mailbox, MessageTemplate, MessageTemplateTypeChoices, useMailboxesMessageTemplatesList, useMailboxesMessageTemplatesDestroy, getMailboxesMessageTemplatesListUrl } from "@/features/api/gen";
import { Banner } from "@/features/ui/components/banner";
import { addToast, ToasterItem } from "@/features/ui/components/toaster";
import { ModalComposeTemplate } from "../modal-compose-template";
import { handle } from "@/features/utils/errors";

type MessageTemplateDataGridProps = {
    mailbox: Mailbox;
}

export const MessageTemplateDataGrid = ({ mailbox }: MessageTemplateDataGridProps) => {
    const { t } = useTranslation();
    const modals = useModals();
    const modal = useModal();
    const { data: templates, isLoading, error } = useMailboxesMessageTemplatesList(
        mailbox.id,
        {
            type: [MessageTemplateTypeChoices.message],
        },
        {
            query: {
                enabled: !!mailbox.id,
            },
        }
    );
    const { mutateAsync: deleteTemplate, isPending: isDeleting } = useMailboxesMessageTemplatesDestroy();
    const [selectedTemplate, setSelectedTemplate] = useState<MessageTemplate | undefined>();
    const queryClient = useQueryClient();

    const invalidateMessageTemplates = async () => {
        await queryClient.invalidateQueries({ queryKey: [getMailboxesMessageTemplatesListUrl(mailbox.id)], exact: false });
    }

    const handleModifyRow = (template: MessageTemplate) => {
        setSelectedTemplate(template);
        modal.open();
    }

    const handleDeleteRow = async (template: MessageTemplate) => {
        const decision = await modals.deleteConfirmationModal({
            title: <span className="c__modal__text--centered">{t('Delete template "{{template}}"', { template: template.name })}</span>,
            children: t('Are you sure you want to delete this template? This action is irreversible!'),
        });
        if (decision === 'delete') {
            try {
                await deleteTemplate({ mailboxId: mailbox.id, id: template.id });
                await invalidateMessageTemplates();
                addToast(
                    <ToasterItem type="info">
                        <span>{t("Template deleted!")}</span>
                    </ToasterItem>,
                );
            } catch (error) {
                handle(error);
                addToast(
                    <ToasterItem type="error">
                        <span>{t("Failed to delete template.")}</span>
                    </ToasterItem>,
                );
            }
        }
    }

    const columns: Column<MessageTemplate>[] = [
        {
            id: "name",
            headerName: t("Name"),
            renderCell: ({ row }) => row.name,
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
                    {t("Loading templates...")}
                </Banner>
            </div>
        );
    }

    if (error) {
        return (
            <div className="admin-data-grid">
                <Banner type="error">
                    {t("Error while loading templates")}
                </Banner>
            </div>
        );
    }

    return (
        <section className="admin-page__body">
            <div className="admin-data-grid">
                <DataGrid
                    columns={columns}
                    rows={templates?.data ?? []}
                    onSortModelChange={() => undefined}
                    enableSorting={false}
                    emptyPlaceholderLabel={t("No template found")}
                />
                <ModalComposeTemplate
                    isOpen={modal.isOpen}
                    onClose={() => {
                        modal.close();
                        setSelectedTemplate(undefined);
                    }}
                    template={selectedTemplate}
                />
            </div>
        </section>
    );
};
