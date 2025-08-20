import { Icon, IconSize, Spinner } from "@gouvfr-lasuite/ui-kit";
import { Button, Checkbox, Column, DataGrid, useModal, useModals } from "@openfun/cunningham-react";
import { useTranslation } from "react-i18next";
import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { MailDomainAdmin, MessageTemplateKindChoices, ReadOnlyMessageTemplate, useMessageTemplatesDestroy, useMessageTemplatesList, useMessageTemplatesPartialUpdate } from "@/features/api/gen";
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
    const { data: { data: signatures = [] } = {}, isLoading, error } = useMessageTemplatesList({
        request: {
            params: {
                kind: MessageTemplateKindChoices.signature,
                maildomain_id: domain.id,
            }
        }
    });
    const { mutateAsync: updateSignature, isPending: isUpdating } = useMessageTemplatesPartialUpdate();
    const { mutateAsync: deleteSignature, isPending: isDeleting } = useMessageTemplatesDestroy();
    const [selectedSignature, setSelectedSignature] = useState<ReadOnlyMessageTemplate | undefined>();
    const queryClient = useQueryClient();
    const invalidateMessageTemplates = async () => {
        await queryClient.invalidateQueries({ queryKey: ["/api/v1.0/message-templates/"], exact: false });
    }
    const handleModifyRow = (signature: ReadOnlyMessageTemplate) => {
        setSelectedSignature(signature);
        modal.open();
    }
    const addUpdateSucceededToast = () => {
        addToast(
            <ToasterItem type="info">
                <span>{t("admin_maildomains_signature.toasts.success_update")}</span>
            </ToasterItem>,
        );
    }
    const handleDeleteRow = async (signature: ReadOnlyMessageTemplate) => {
        const decision = await modals.deleteConfirmationModal({
            title: <span className="c__modal__text--centered">{t('admin_maildomains_signature.compose_modal.delete_modal.title', { signature: signature.name })}</span>,
            children: t('admin_maildomains_signature.compose_modal.delete_modal.message'),
        });
        if (decision === 'delete') {
            await deleteSignature({ id: signature.id });
            invalidateMessageTemplates();
            addToast(
                <ToasterItem type="info">
                    <span>{t("admin_maildomains_signature.toasts.delete")}</span>
                </ToasterItem>,
            );
        }
    }
    const toggleActive = async (signature: ReadOnlyMessageTemplate) => {
        await updateSignature({
            id: signature.id,
            data: { is_active: !signature.is_active, maildomain_id: domain.id },
        });
        invalidateMessageTemplates();
        addUpdateSucceededToast();
    }
    const toggleDefault = async (signature: ReadOnlyMessageTemplate) => {
        await updateSignature({
            id: signature.id,
            data: { is_default: !signature.is_default, maildomain_id: domain.id },
        });
        invalidateMessageTemplates();
        addUpdateSucceededToast();
    }
    const columns: Column<ReadOnlyMessageTemplate>[] = [
        {
            id: "is_active",
            headerName: t("admin_maildomains_signature.datagrid_headers.is_active"),
            size: 70,
            renderCell: ({ row }) => (
                <div className="flex-row flex-justify-center">
                    <Checkbox checked={row.is_active} onChange={() => toggleActive(row)} disabled={isUpdating} />
                </div>
            ),
        },
        {
            id: "is_default",
            headerName: t("admin_maildomains_signature.datagrid_headers.is_default"),
            size: 85,
            renderCell: ({ row }) => (
                <div className="flex-row flex-justify-center">
                    <Checkbox checked={row.is_default} onChange={() => toggleDefault(row)} disabled={isUpdating} />
                </div>
            ),
        },
        {
            size: 250,
            id: "name",
            headerName: t("admin_maildomains_signature.datagrid_headers.name"),
            renderCell: ({ row }) => row.name,
        },
        {
            id: "description",
            headerName: t("admin_maildomains_signature.datagrid_headers.description"),
            renderCell: ({ row }) => row.description,
        },
        {
            id: "actions",
            size: 140,
            headerName: t("admin_maildomains_signature.datagrid_headers.actions"),
            renderCell: ({ row }) => (
                <div className="flex-row flex-justify-start" style={{ width: "100%", gap: "1rem" }}>
                    <Button
                        color="secondary"
                        size="small"
                        onClick={() => handleModifyRow(row)}
                    >
                        {t("admin_maildomains_signature.actions.modify")}
                    </Button>
                    <Button
                        color="danger"
                        size="small"
                        icon={isDeleting ? <Spinner size="sm" /> : <Icon name="delete" size={IconSize.SMALL} />}
                        onClick={() => handleDeleteRow(row)}
                        disabled={isDeleting}
                        aria-label={t("admin_maildomains_signature.actions.delete")}
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
                    {t("admin_maildomains_signature.loading")}
                </Banner>
            </div>
        );
    }

    if (error) {
        return (
            <div className="admin-data-grid">
                <Banner type="error">
                    {t("admin_maildomains_signature.errors.failed_to_load_signatures")}
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
                    {t("admin_maildomains_signature.no_signatures")}
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
