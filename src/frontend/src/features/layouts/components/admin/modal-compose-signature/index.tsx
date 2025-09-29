import { MailDomainAdmin, MessageTemplate, MessageTemplateTypeChoices, useMaildomainsMessageTemplatesCreate, useMaildomainsMessageTemplatesUpdate } from "@/features/api/gen";
import { RhfCheckbox } from "@/features/forms/components/react-hook-form/rhf-checkbox";
import { RhfInput } from "@/features/forms/components/react-hook-form/rhf-input";
import { useAdminMailDomain } from "@/features/providers/admin-maildomain";
import { zodResolver } from "@hookform/resolvers/zod";
import { Button, Modal, ModalSize } from "@openfun/cunningham-react";
import { FormProvider, useForm } from "react-hook-form";
import { useTranslation } from "react-i18next";
import z from "zod";
import { useQueryClient } from "@tanstack/react-query";
import { SignatureComposer } from "./signature-composer";
import { addToast, ToasterItem } from "@/features/ui/components/toaster";

/**
 * Modal component to compose a signature for a mail domain.
 */
type ModalComposeSignatureProps = {
    isOpen: boolean;
    onClose: () => void;
    signature?: MessageTemplate;
}

export const ModalComposeSignature = ({ isOpen, onClose, signature }: ModalComposeSignatureProps) => {
    const { t } = useTranslation();
    const { selectedMailDomain } = useAdminMailDomain();
    const domainName = selectedMailDomain?.name || "";
    const queryClient = useQueryClient();
    const invalidateMessageTemplates = async () => {
        await queryClient.invalidateQueries({ queryKey: [`/api/v1.0/maildomains/${selectedMailDomain?.id}/message-templates/`], exact: false });
    }

    const handleSuccess = async () => {
        await invalidateMessageTemplates();
        onClose();
        addToast(
            <ToasterItem type="info">
                <span>{t(
                    signature ? "admin_maildomains_signature.toasts.success_update" : "admin_maildomains_signature.toasts.success_create"
                )}</span>
            </ToasterItem>,
        );
    }

    return (
        <Modal
            isOpen={isOpen}
            title={t('admin_maildomains_signature.compose_modal.title', { domain: domainName })}
            size={ModalSize.LARGE}
            onClose={onClose}
        >
            <div className="modal-compose-signature">
                <SignatureComposeForm domain={selectedMailDomain!} defaultValue={signature} onSuccess={handleSuccess} />
            </div>
        </Modal>
    );
};

type SignatureComposerFormProps = {
    domain: MailDomainAdmin;
    defaultValue?: MessageTemplate;
    onSuccess?: () => void;
}

const signatureComposerSchema = z.object({
    name: z.string().min(1, { error: "admin_maildomains_signature.compose_modal.form.errors.name" }),
    is_active: z.boolean(),
    is_forced: z.boolean(),
    htmlBody: z.string().min(1, { error: "admin_maildomains_signature.compose_modal.form.errors.content" }),
    textBody: z.string().min(1, { error: "admin_maildomains_signature.compose_modal.form.errors.content" }),
    rawBody: z.string().min(1, { error: "admin_maildomains_signature.compose_modal.form.errors.content" }),
});

type SignatureComposerFormData = z.infer<typeof signatureComposerSchema>;

const SignatureComposeForm = ({ domain, defaultValue, onSuccess }: SignatureComposerFormProps) => {
    const { t } = useTranslation();
    const form = useForm<SignatureComposerFormData>({
        resolver: zodResolver(signatureComposerSchema),
        defaultValues: {
            name: defaultValue?.name ?? "",
            is_active: defaultValue?.is_active ?? true,
            is_forced: defaultValue?.is_forced ?? false,
            htmlBody: defaultValue?.html_body,
            textBody: defaultValue?.text_body,
            rawBody: defaultValue?.raw_body ?? undefined,
        }
    });
    const { mutateAsync: createSignature, isPending } = useMaildomainsMessageTemplatesCreate();
    const { mutateAsync: updateSignature, isPending: isUpdating } = useMaildomainsMessageTemplatesUpdate();
    const isSubmitting = isPending || isUpdating;

    const onSubmit = async (data: SignatureComposerFormData) => {
        try {
            if (defaultValue?.id) {
                await updateSignature({
                    maildomainPk: domain.id,
                    id: defaultValue.id,
                    data: {
                        name: data.name,
                        type: MessageTemplateTypeChoices.signature,
                        is_active: data.is_active,
                        is_forced: data.is_forced,
                        html_body: data.htmlBody,
                        text_body: data.textBody,
                        raw_body: data.rawBody,
                    }
                });
            } else {
                await createSignature({
                    maildomainPk: domain.id,
                    data: {
                        name: data.name,
                        type: MessageTemplateTypeChoices.signature,
                        is_active: data.is_active,
                        is_forced: data.is_forced,
                        html_body: data.htmlBody,
                        text_body: data.textBody,
                        raw_body: data.rawBody,
                    }
                });
            }
        } catch (error) {
            console.error(error);
        }
        onSuccess?.();
    }

    return (
        <FormProvider {...form}>
            <form className="signature-composer-form" onSubmit={form.handleSubmit(onSubmit)}>
                <div className="form-field-row">
                    <RhfInput
                        label={t('admin_maildomains_signature.compose_modal.form.labels.name')}
                        name="name"
                        text={form.formState.errors.name?.message && t(form.formState.errors.name.message)}
                        fullWidth
                    />
                </div>
                <div className="form-field-row">
                    <SignatureComposer
                        defaultValue={defaultValue?.raw_body}
                        state={form.formState.errors.textBody?.message ? "error" : "default"}
                        text={form.formState.errors.textBody?.message && t(form.formState.errors.textBody.message)}
                    />
                </div>
                <div className="form-field-row">
                    <RhfCheckbox
                        label={t('admin_maildomains_signature.compose_modal.form.labels.is_active')}
                        name="is_active"
                        text={t('admin_maildomains_signature.compose_modal.form.helper_text.is_active')}
                        fullWidth
                    />
                    <RhfCheckbox
                        label={t('admin_maildomains_signature.compose_modal.form.labels.is_forced')}
                        name="is_forced"
                        text={t('admin_maildomains_signature.compose_modal.form.helper_text.is_forced')}
                        fullWidth
                    />
                </div>
                <div className="form-actions">
                    <Button type="submit" disabled={isSubmitting}>
                        {isSubmitting ? t('actions.saving') : t('actions.save')}
                    </Button>
                </div>

            </form>
        </FormProvider>
    );
};
