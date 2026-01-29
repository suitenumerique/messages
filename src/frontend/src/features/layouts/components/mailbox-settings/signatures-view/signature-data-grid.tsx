import { Icon, IconSize, Spinner } from "@gouvfr-lasuite/ui-kit";
import { Button, Checkbox, Column, DataGrid, useModal, useModals } from "@gouvfr-lasuite/cunningham-react";
import { useTranslation } from "react-i18next";
import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Mailbox, MessageTemplate, MessageTemplateTypeChoices, useMailboxesMessageTemplatesList, useMailboxesMessageTemplatesDestroy, useMailboxesMessageTemplatesPartialUpdate, getMailboxesMessageTemplatesListUrl } from "@/features/api/gen";
import { Banner } from "@/features/ui/components/banner";
import { addToast, ToasterItem } from "@/features/ui/components/toaster";
import { ModalComposeMailboxSignature } from "../modal-compose-mailbox-signature";

type SignatureDataGridProps = {
    mailbox: Mailbox;
}

export const SignatureDataGrid = ({ mailbox }: SignatureDataGridProps) => {
    const { t } = useTranslation();
    const modals = useModals();
    const modal = useModal();
    const { data: signatures, isLoading, error } = useMailboxesMessageTemplatesList(
        mailbox.id,
        {
            type: [MessageTemplateTypeChoices.signature],
        },
        {
            query: {
                enabled: !!mailbox.id,
            },
        }
    );
    const { mutateAsync: updateSignature, isPending: isUpdating } = useMailboxesMessageTemplatesPartialUpdate();
    const { mutateAsync: deleteSignature, isPending: isDeleting } = useMailboxesMessageTemplatesDestroy();
    const [selectedSignature, setSelectedSignature] = useState<MessageTemplate | undefined>();
    const queryClient = useQueryClient();

    const invalidateSignatures = async () => {
        await queryClient.invalidateQueries({ queryKey: [getMailboxesMessageTemplatesListUrl(mailbox.id)], exact: false });
    }

    const handleModifyRow = (signature: MessageTemplate) => {
        setSelectedSignature(signature);
        modal.open();
    }

    const addUpdateSucceededToast = () => {
        addToast(
            <ToasterItem type="info">
                <span>{t("Signature updated!")}</span>
            </ToasterItem>,
        );
    }

    const handleDeleteRow = async (signature: MessageTemplate) => {
        const decision = await modals.deleteConfirmationModal({
            title: <span className="c__modal__text--centered">{t('Delete signature "{{signature}}"', { signature: signature.name })}</span>,
            children: t('Are you sure you want to delete this signature? This action is irreversible!'),
        });
        if (decision === 'delete') {
            try {
                await deleteSignature({ mailboxId: mailbox.id, id: signature.id });
                await invalidateSignatures();
                addToast(
                    <ToasterItem type="info">
                        <span>{t("Signature deleted!")}</span>
                    </ToasterItem>,
                );
            } catch {
                addToast(
                    <ToasterItem type="error">
                        <span>{t("Failed to delete signature.")}</span>
                    </ToasterItem>,
                );
            }
        }
    }

    const toggleDefault = async (signature: MessageTemplate) => {
        try {
            await updateSignature({
                mailboxId: mailbox.id,
                id: signature.id,
                data: { is_default: !signature.is_default },
            });
            invalidateSignatures();
            addUpdateSucceededToast();
        } catch {
            addToast(
                <ToasterItem type="error">
                    <span>{t("Failed to update signature.")}</span>
                </ToasterItem>,
            );
        }
    }

    const columns: Column<MessageTemplate>[] = [
        {
            id: "is_default",
            headerName: t("Default"),
            size: 75,
            renderCell: ({ row }) => (
                <div className="flex-row flex-justify-center">
                    <Checkbox
                        checked={row.is_default}
                        onChange={() => toggleDefault(row)}
                        disabled={isUpdating}
                        aria-label={t("Default")}
                    />
                </div>
            ),
        },
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
                    {t("Loading signatures...")}
                </Banner>
            </div>
        );
    }

    if (error) {
        return (
            <div className="admin-data-grid">
                <Banner type="error">
                    {t("Error while loading signatures")}
                </Banner>
            </div>
        );
    }

    return (
        <section className="admin-page__body">
            <div className="admin-data-grid">
                <DataGrid
                    columns={columns}
                    rows={signatures?.data ?? []}
                    onSortModelChange={() => undefined}
                    enableSorting={false}
                    emptyPlaceholderLabel={t("No signatures found")}
                />
                <ModalComposeMailboxSignature
                    isOpen={modal.isOpen}
                    onClose={
                        () => {
                            modal.close();
                            if (selectedSignature) {
                                setSelectedSignature(undefined);
                            }
                        }
                    }
                    signature={selectedSignature}
                />
            </div>
        </section>
    );
};
