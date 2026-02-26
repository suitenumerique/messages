import { ReadMessageTemplate, MessageTemplateTypeChoices, useMailboxesMessageTemplatesCreate, useMailboxesMessageTemplatesUpdate, useMailboxesMessageTemplatesRetrieve, getMailboxesMessageTemplatesListUrl } from "@/features/api/gen";
import { RhfInput } from "@/features/forms/components/react-hook-form/rhf-input";
import { useMailboxContext } from "@/features/providers/mailbox";
import { zodResolver } from "@hookform/resolvers/zod";
import { Button, Modal, ModalSize } from "@gouvfr-lasuite/cunningham-react";
import { Spinner } from "@gouvfr-lasuite/ui-kit";
import { FormProvider, useForm } from "react-hook-form";
import { useTranslation } from "react-i18next";
import { z } from "zod";
import { useEffect, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { TemplateComposer } from "./template-composer";
import { Base64ComposerHandle } from "@/features/blocknote/hooks/use-base64-composer";
import ErrorBoundary from "@/features/errors/error-boundary";
import { Banner } from "@/features/ui/components/banner";
import { addToast, ToasterItem } from "@/features/ui/components/toaster";
import i18n from "@/features/i18n/initI18n";
import { handle } from "@/features/utils/errors";
import { useConfirmBeforeClose } from "@/features/hooks/use-confirm-before-close";
import { extractSignatureId } from "../utils";

/**
 * Modal component to compose a template for a mailbox.
 */
type ModalComposeTemplateProps = {
    isOpen: boolean;
    onClose: () => void;
    template?: ReadMessageTemplate;
}

export const ModalComposeTemplate = ({ isOpen, onClose, template }: ModalComposeTemplateProps) => {
    const { t } = useTranslation();
    const { selectedMailbox } = useMailboxContext();
    const queryClient = useQueryClient();
    const [isDirty, setIsDirty] = useState(false);
    const guardedOnClose = useConfirmBeforeClose(isDirty, onClose);
    const invalidateMessageTemplates = async () => {
        await queryClient.invalidateQueries({ queryKey: [getMailboxesMessageTemplatesListUrl(selectedMailbox!.id)], exact: false });
    }

    const handleSuccess = async () => {
        await invalidateMessageTemplates();
        onClose();
        addToast(
            <ToasterItem type="info">
                <span>{
                    template ? t("Template updated!") : t("Template created!")
                }</span>
            </ToasterItem>,
        );
    }

    return (
        <Modal
            isOpen={isOpen}
            title={template ? t('Edit template "{{template}}"', { template: template.name }) : t("Create a new template")}
            size={ModalSize.LARGE}
            onClose={guardedOnClose}
        >
            <div className="modal-compose-template">
                {template ? (
                    <TemplateComposeFormWithRetrieve
                        mailboxId={selectedMailbox!.id}
                        templateId={template.id}
                        templateName={template.name}
                        onSuccess={handleSuccess}
                        onDirtyChange={setIsDirty}
                    />
                ) : (
                    <TemplateComposeForm
                        mailboxId={selectedMailbox!.id}
                        onSuccess={handleSuccess}
                        onDirtyChange={setIsDirty}
                    />
                )}
            </div>
        </Modal>
    );
};

type TemplateComposeFormWithRetrieveProps = {
    mailboxId: string;
    templateId: string;
    templateName: string;
    onSuccess?: () => void;
    onDirtyChange?: (isDirty: boolean) => void;
}

const TemplateComposeFormWithRetrieve = ({ mailboxId, templateId, templateName, onSuccess, onDirtyChange }: TemplateComposeFormWithRetrieveProps) => {
    const { t } = useTranslation();
    const { data, isLoading, isError } = useMailboxesMessageTemplatesRetrieve(mailboxId, templateId, { bodies: "raw" }, {
        query: { gcTime: 0 },
    });

    if (isLoading) {
        return (
            <div className="flex-row flex-align-center flex-justify-center" style={{ padding: "2rem", gap: "1rem" }}>
                <Spinner />
                <span>{t("Loading template...")}</span>
            </div>
        );
    }

    if (isError) {
        return (
            <Banner type="error">
                <p>{t("Failed to load template. Please try again.")}</p>
            </Banner>
        );
    }

    const errorFallback = (
        <Banner type="error">
            <p>{t("Failed to load template. Please try again.")}</p>
        </Banner>
    );

    return (
        <ErrorBoundary fallback={errorFallback}>
            <TemplateComposeForm
                mailboxId={mailboxId}
                defaultValue={{
                    id: templateId,
                    name: templateName,
                    raw_body: data?.data?.raw_body ?? undefined,
                }}
                onSuccess={onSuccess}
                onDirtyChange={onDirtyChange}
            />
        </ErrorBoundary>
    );
};

type TemplateComposeFormProps = {
    mailboxId: string;
    defaultValue?: {
        id: string;
        name: string;
        raw_body?: string | null;
    };
    onSuccess?: () => void;
    onDirtyChange?: (isDirty: boolean) => void;
}

const templateComposerSchema = () => z.object({
    name: z.string().min(1, { error: i18n.t("Name is required") }),
    rawBody: z.string().min(1, { error: i18n.t("Content is required") }),
});

type TemplateComposerFormData = z.infer<ReturnType<typeof templateComposerSchema>>;

const TemplateComposeForm = ({ mailboxId, defaultValue, onSuccess, onDirtyChange }: TemplateComposeFormProps) => {
    const { t } = useTranslation();
    const composerRef = useRef<Base64ComposerHandle>(null);
    const form = useForm<TemplateComposerFormData>({
        resolver: zodResolver(templateComposerSchema()),
        defaultValues: {
            name: defaultValue?.name ?? "",
            rawBody: defaultValue?.raw_body ?? undefined,
        }
    });
    const { mutateAsync: createTemplate, isPending } = useMailboxesMessageTemplatesCreate();
    const { mutateAsync: updateTemplate, isPending: isUpdating } = useMailboxesMessageTemplatesUpdate();
    const isSubmitting = isPending || isUpdating;

    useEffect(() => {
        onDirtyChange?.(form.formState.isDirty);
    }, [form.formState.isDirty, onDirtyChange]);

    const onSubmit = async (data: TemplateComposerFormData): Promise<void> => {
        const { htmlBody, textBody } = await composerRef.current!.exportContent();
        if (!textBody) {
            form.setError("rawBody", { message: t("Content is required") });
            return;
        }

        try {
            const signatureId = extractSignatureId(data.rawBody);
            const payload = {
                name: data.name,
                type: MessageTemplateTypeChoices.message,
                html_body: htmlBody,
                text_body: textBody,
                raw_body: data.rawBody,
                signature_id: signatureId,
            };
            if (defaultValue?.id) {
                await updateTemplate({
                    mailboxId,
                    id: defaultValue.id,
                    data: payload,
                });
            } else {
                await createTemplate({
                    mailboxId,
                    data: payload,
                });
            }
        } catch (error) {
            handle(error);
            addToast(
                <ToasterItem type="error">
                    <span>{t("Failed to save template. Please try again.")}</span>
                </ToasterItem>,
            );
            return;
        }
        onSuccess?.();
    }

    return (
        <FormProvider {...form}>
            <form className="template-composer-form" onSubmit={form.handleSubmit(onSubmit)}>
                <div className="form-field-row">
                    <RhfInput
                        label={t('Name')}
                        name="name"
                        text={form.formState.errors.name?.message && t(form.formState.errors.name.message)}
                        fullWidth
                    />
                </div>
                <div className="form-field-row">
                    <TemplateComposer
                        ref={composerRef}
                        defaultValue={defaultValue?.raw_body}
                        state={form.formState.errors.rawBody?.message ? "error" : "default"}
                        text={form.formState.errors.rawBody?.message && t(form.formState.errors.rawBody.message)}
                        blockNoteOptions={{ autofocus: "end" }}
                    />
                </div>
                <div className="form-actions">
                    <Button type="submit" disabled={isSubmitting}>
                        {isSubmitting ? t('Saving...') : t('Save')}
                    </Button>
                </div>
            </form>
        </FormProvider>
    );
};
