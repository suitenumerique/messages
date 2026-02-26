import { ReadMessageTemplate, MessageTemplateTypeChoices, useMailboxesMessageTemplatesCreate, useMailboxesMessageTemplatesUpdate, useMailboxesMessageTemplatesRetrieve, getMailboxesMessageTemplatesListUrl } from "@/features/api/gen";
import { RhfInput } from "@/features/forms/components/react-hook-form/rhf-input";
import { RhfCheckbox } from "@/features/forms/components/react-hook-form/rhf-checkbox";
import { useMailboxContext } from "@/features/providers/mailbox";
import { zodResolver } from "@hookform/resolvers/zod";
import { Button, Modal, ModalSize } from "@gouvfr-lasuite/cunningham-react";
import { Spinner } from "@gouvfr-lasuite/ui-kit";
import { FormProvider, useForm } from "react-hook-form";
import { useTranslation } from "react-i18next";
import { z } from "zod";
import { useEffect, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { SignatureComposer } from "@/features/signatures/components/signature-composer";
import { Base64ComposerHandle } from "@/features/blocknote/hooks/use-base64-composer";
import ErrorBoundary from "@/features/errors/error-boundary";
import { Banner } from "@/features/ui/components/banner";
import { addToast, ToasterItem } from "@/features/ui/components/toaster";
import i18n from "@/features/i18n/initI18n";
import { handle } from "@/features/utils/errors";
import { useConfirmBeforeClose } from "@/features/hooks/use-confirm-before-close";

/**
 * Modal component to compose a signature for a mailbox.
 */
type ModalComposeMailboxSignatureProps = {
    isOpen: boolean;
    onClose: () => void;
    signature?: ReadMessageTemplate;
}

export const ModalComposeMailboxSignature = ({ isOpen, onClose, signature }: ModalComposeMailboxSignatureProps) => {
    const { t } = useTranslation();
    const { selectedMailbox } = useMailboxContext();
    const queryClient = useQueryClient();
    const [isDirty, setIsDirty] = useState(false);
    const guardedOnClose = useConfirmBeforeClose(isDirty, onClose);

    if (!selectedMailbox) {
        return null;
    }

    const invalidateSignatures = async () => {
        await queryClient.invalidateQueries({ queryKey: [getMailboxesMessageTemplatesListUrl(selectedMailbox.id)], exact: false });
    }

    const handleSuccess = async () => {
        await invalidateSignatures();
        onClose();
        addToast(
            <ToasterItem type="info">
                <span>{
                    signature ? t("Signature updated!") : t("Signature created!")
                }</span>
            </ToasterItem>,
        );
    }

    return (
        <Modal
            isOpen={isOpen}
            title={signature ? t('Edit signature "{{signature}}"', { signature: signature.name }) : t("Create a new signature")}
            size={ModalSize.LARGE}
            onClose={guardedOnClose}
        >
            <div className="modal-compose-template">
                {signature ? (
                    <SignatureComposeFormWithRetrieve
                        mailboxId={selectedMailbox.id}
                        signature={signature}
                        onSuccess={handleSuccess}
                        onDirtyChange={setIsDirty}
                    />
                ) : (
                    <SignatureComposeForm
                        mailboxId={selectedMailbox.id}
                        onSuccess={handleSuccess}
                        onDirtyChange={setIsDirty}
                    />
                )}
            </div>
        </Modal>
    );
};

type SignatureComposeFormWithRetrieveProps = {
    mailboxId: string;
    signature: ReadMessageTemplate;
    onSuccess?: () => void;
    onDirtyChange?: (isDirty: boolean) => void;
}

const SignatureComposeFormWithRetrieve = ({ mailboxId, signature, onSuccess, onDirtyChange }: SignatureComposeFormWithRetrieveProps) => {
    const { t } = useTranslation();
    const { data, isLoading, isError } = useMailboxesMessageTemplatesRetrieve(mailboxId, signature.id, { bodies: "raw" }, {
        query: { gcTime: 0 },
    });

    if (isLoading) {
        return (
            <div className="flex-row flex-align-center flex-justify-center" style={{ padding: "2rem", gap: "1rem" }}>
                <Spinner />
                <span>{t("Loading signature...")}</span>
            </div>
        );
    }

    if (isError) {
        return (
            <Banner type="error">
                <p>{t("Failed to load signature. Please try again.")}</p>
            </Banner>
        );
    }

    const errorFallback = (
        <Banner type="error">
            <p>{t("Failed to load signature. Please try again.")}</p>
        </Banner>
    );

    return (
        <ErrorBoundary fallback={errorFallback}>
            <SignatureComposeForm
                mailboxId={mailboxId}
                defaultValue={{
                    id: signature.id,
                    name: signature.name,
                    is_default: signature.is_default,
                    raw_body: data?.data?.raw_body ?? undefined,
                }}
                onSuccess={onSuccess}
                onDirtyChange={onDirtyChange}
            />
        </ErrorBoundary>
    );
};

type SignatureComposeFormProps = {
    mailboxId: string;
    defaultValue?: {
        id: string;
        name: string;
        is_default: boolean;
        raw_body?: string | null;
    };
    onSuccess?: () => void;
    onDirtyChange?: (isDirty: boolean) => void;
}

const signatureComposerSchema = () => z.object({
    name: z.string().min(1, { error: i18n.t("Name is required") }),
    is_default: z.boolean(),
    rawBody: z.string().min(1, { error: i18n.t("Content is required") }),
});

type SignatureComposerFormData = z.infer<ReturnType<typeof signatureComposerSchema>>;

const SignatureComposeForm = ({ mailboxId, defaultValue, onSuccess, onDirtyChange }: SignatureComposeFormProps) => {
    const { t } = useTranslation();
    const composerRef = useRef<Base64ComposerHandle>(null);
    const form = useForm<SignatureComposerFormData>({
        resolver: zodResolver(signatureComposerSchema()),
        defaultValues: {
            name: defaultValue?.name ?? "",
            is_default: defaultValue?.is_default ?? false,
            rawBody: defaultValue?.raw_body ?? undefined,
        }
    });
    const { mutateAsync: createSignature, isPending } = useMailboxesMessageTemplatesCreate();
    const { mutateAsync: updateSignature, isPending: isUpdating } = useMailboxesMessageTemplatesUpdate();
    const isSubmitting = isPending || isUpdating;

    useEffect(() => {
        onDirtyChange?.(form.formState.isDirty);
    }, [form.formState.isDirty, onDirtyChange]);

    const onSubmit = async (data: SignatureComposerFormData): Promise<void> => {
        const { htmlBody, textBody } = await composerRef.current!.exportContent();
        if (!textBody) {
            form.setError("rawBody", { message: t("Content is required") });
            return;
        }
        try {
            if (defaultValue?.id) {
                await updateSignature({
                    mailboxId,
                    id: defaultValue.id,
                    data: {
                        name: data.name,
                        type: MessageTemplateTypeChoices.signature,
                        is_default: data.is_default,
                        html_body: htmlBody,
                        text_body: textBody,
                        raw_body: data.rawBody,
                    }
                });
            } else {
                await createSignature({
                    mailboxId,
                    data: {
                        name: data.name,
                        type: MessageTemplateTypeChoices.signature,
                        is_default: data.is_default,
                        html_body: htmlBody,
                        text_body: textBody,
                        raw_body: data.rawBody,
                    }
                });
            }
        } catch (error) {
            handle(error);
            addToast(
                <ToasterItem type="error">
                    <span>{t("Failed to save signature. Please try again.")}</span>
                </ToasterItem>,
            );
            return;
        }
        onSuccess?.();
    }

    return (
        <FormProvider {...form}>
            <form className="composer-form" onSubmit={form.handleSubmit(onSubmit)}>
                <div className="form-field-row">
                    <RhfInput
                        label={t('Name')}
                        name="name"
                        text={form.formState.errors.name?.message && t(form.formState.errors.name.message)}
                        fullWidth
                    />
                </div>
                <div className="form-field-row">
                    <SignatureComposer
                        ref={composerRef}
                        defaultValue={defaultValue?.raw_body}
                        state={form.formState.errors.rawBody?.message ? "error" : "default"}
                        text={form.formState.errors.rawBody?.message && t(form.formState.errors.rawBody.message)}
                        blockNoteOptions={{ autofocus: "end" }}
                    />
                </div>
                <div className="form-field-row">
                    <RhfCheckbox
                        label={t('Default signature')}
                        name="is_default"
                        text={t('The default signature will be automatically loaded when composing a new message.')}
                        fullWidth
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
