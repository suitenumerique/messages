import { Icon, IconSize, Spinner } from "@gouvfr-lasuite/ui-kit";
import { Button, Checkbox, Column, DataGrid, useModal, useModals } from "@openfun/cunningham-react";
import { useTranslation } from "react-i18next";
import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { MailDomainAdmin, MessageTemplate, MessageTemplateTypeChoices, useMaildomainsMessageTemplatesList, useMaildomainsMessageTemplatesDestroy, useMaildomainsMessageTemplatesPartialUpdate } from "@/features/api/gen";
import { Banner } from "@/features/ui/components/banner";
import { addToast, ToasterItem } from "@/features/ui/components/toaster";
import { ModalComposeSignature } from "../modal-compose-signature";

type SignatureDataGridProps = {
    domain: MailDomainAdmin;
}

export const SignatureDataGrid = ({ domain }: SignatureDataGridProps) => {
    const { t } = useTranslation();
    const modals = useModals();
    const modal = useModal();
    const { data: { data: signatures = [] } = {}, isLoading, error } = useMaildomainsMessageTemplatesList(
        domain.id,
        {
            type: MessageTemplateTypeChoices.signature,
        },
        {
            query: {
                enabled: !!domain.id,
            },
        }
    );
    const { mutateAsync: updateSignature, isPending: isUpdating } = useMaildomainsMessageTemplatesPartialUpdate();
    const { mutateAsync: deleteSignature, isPending: isDeleting } = useMaildomainsMessageTemplatesDestroy();
    const [selectedSignature, setSelectedSignature] = useState<MessageTemplate | undefined>();
    const queryClient = useQueryClient();
    const invalidateMessageTemplates = async () => {
        await queryClient.invalidateQueries({ queryKey: [`/api/v1.0/maildomains/${domain.id}/message-templates/`], exact: false });
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
            await deleteSignature({ maildomainPk: domain.id, id: signature.id });
            invalidateMessageTemplates();
            addToast(
                <ToasterItem type="info">
                    <span>{t("Signature deleted!")}</span>
                </ToasterItem>,
            );
        }
    }
    const toggleActive = async (signature: MessageTemplate) => {
        await updateSignature({
            maildomainPk: domain.id,
            id: signature.id,
            data: { is_active: !signature.is_active },
        });
        invalidateMessageTemplates();
        addUpdateSucceededToast();
    }
    const toggleDefault = async (signature: MessageTemplate) => {
        await updateSignature({
            maildomainPk: domain.id,
            id: signature.id,
            data: { is_forced: !signature.is_forced },
        });
        invalidateMessageTemplates();
        addUpdateSucceededToast();
    }
    const columns: Column<MessageTemplate>[] = [
        {
            id: "is_active",
            headerName: t("Active"),
            size: 75,
            renderCell: ({ row }) => (
                <div className="flex-row flex-justify-center">
                    <Checkbox checked={row.is_active} onChange={() => toggleActive(row)} disabled={isUpdating} />
                </div>
            ),
        },
        {
            id: "is_forced",
            headerName: t("Forced"),
            size: 100,
            renderCell: ({ row }) => (
                <div className="flex-row flex-justify-center">
                    <Checkbox
                        checked={row.is_forced}
                        onChange={() => toggleDefault(row)}
                        disabled={!row.is_active || isUpdating}
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
            size: 150,
            headerName: t("Actions"),
            renderCell: ({ row }) => (
                <div className="flex-row flex-justify-start" style={{ width: "100%", gap: "1rem" }}>
                    <Button
                        color="secondary"
                        size="small"
                        onClick={() => handleModifyRow(row)}
                    >
                        {t("Modify")}
                    </Button>
                    <Button
                        color="danger"
                        size="small"
                        icon={isDeleting ? <Spinner size="sm" /> : <Icon name="delete" size={IconSize.SMALL} />}
                        onClick={() => handleDeleteRow(row)}
                        disabled={isDeleting}
                        aria-label={t("Delete")}
                    >
                    </Button>
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
        <div className="admin-data-grid">
            {signatures.length > 0 ? (
                <DataGrid
                    columns={columns}
                    rows={signatures}
                    onSortModelChange={() => undefined}
                    enableSorting={false}
                />
            ) : (
                <Banner type="info">
                    {t("No signatures found")}
                </Banner>
            )}
            <ModalComposeSignature
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
    );
}
