import { ReadMessageTemplate, MessageTemplateTypeChoices, useMailboxesMessageTemplatesCreate, useMailboxesMessageTemplatesUpdate, useMailboxesMessageTemplatesRetrieve, useMailboxesMessageTemplatesList } from "@/features/api/gen";
import { RhfInput } from "@/features/forms/components/react-hook-form/rhf-input";
import { RhfCheckbox } from "@/features/forms/components/react-hook-form/rhf-checkbox";
import { RhfSelect } from "@/features/forms/components/react-hook-form/rhf-select";
import { useMailboxContext } from "@/features/providers/mailbox";
import { zodResolver } from "@hookform/resolvers/zod";
import { Button, Modal, ModalSize } from "@gouvfr-lasuite/cunningham-react";
import { Spinner } from "@gouvfr-lasuite/ui-kit";
import { FormProvider, useForm, useWatch } from "react-hook-form";
import { useTranslation } from "react-i18next";
import { z } from "zod";
import { useEffect, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Base64ComposerHandle } from "@/features/blocknote/hooks/use-base64-composer";
import ErrorBoundary from "@/features/errors/error-boundary";
import { Banner } from "@/features/ui/components/banner";
import { addToast, ToasterItem } from "@/features/ui/components/toaster";
import i18n from "@/features/i18n/initI18n";
import { handle } from "@/features/utils/errors";
import { useConfirmBeforeClose } from "@/features/hooks/use-confirm-before-close";
import { extractSignatureId } from "../utils";
import { TemplateComposer } from "../modal-compose-template/template-composer";

type ModalComposeMailboxAutoreplyProps = {
    isOpen: boolean;
    onClose: () => void;
    autoreply?: ReadMessageTemplate;
}

export const ModalComposeMailboxAutoreply = ({ isOpen, onClose, autoreply }: ModalComposeMailboxAutoreplyProps) => {
    const { t } = useTranslation();
    const { selectedMailbox } = useMailboxContext();
    const queryClient = useQueryClient();
    const [isDirty, setIsDirty] = useState(false);
    const guardedOnClose = useConfirmBeforeClose(isDirty, onClose);
    const { queryKey } = useMailboxesMessageTemplatesList(
        selectedMailbox?.id ?? "",
        { type: [MessageTemplateTypeChoices.autoreply] },
        { query: { enabled: false } }
    );

    if (!selectedMailbox) {
        return null;
    }

    const invalidateAutoreplies = async () => {
        await queryClient.invalidateQueries({ queryKey, exact: true });
    }

    const handleSuccess = async () => {
        await invalidateAutoreplies();
        onClose();
        addToast(
            <ToasterItem type="info">
                <span>{
                    autoreply ? t("Auto-reply updated!") : t("Auto-reply created!")
                }</span>
            </ToasterItem>,
        );
    }

    return (
        <Modal
            isOpen={isOpen}
            title={autoreply ? t('Edit auto-reply "{{autoreply}}"', { autoreply: autoreply.name }) : t("Create a new auto-reply")}
            size={ModalSize.LARGE}
            onClose={guardedOnClose}
        >
            <div className="modal-compose-template">
                {autoreply ? (
                    <AutoreplyComposeFormWithRetrieve
                        mailboxId={selectedMailbox.id}
                        autoreply={autoreply}
                        onSuccess={handleSuccess}
                        onDirtyChange={setIsDirty}
                    />
                ) : (
                    <AutoreplyComposeForm
                        mailboxId={selectedMailbox.id}
                        onSuccess={handleSuccess}
                        onDirtyChange={setIsDirty}
                    />
                )}
            </div>
        </Modal>
    );
};

type AutoreplyComposeFormWithRetrieveProps = {
    mailboxId: string;
    autoreply: ReadMessageTemplate;
    onSuccess?: () => void;
    onDirtyChange?: (isDirty: boolean) => void;
}

const AutoreplyComposeFormWithRetrieve = ({ mailboxId, autoreply, onSuccess, onDirtyChange }: AutoreplyComposeFormWithRetrieveProps) => {
    const { t } = useTranslation();
    const { data, isLoading, isError } = useMailboxesMessageTemplatesRetrieve(mailboxId, autoreply.id, { bodies: "raw" }, {
        query: { gcTime: 0 },
    });

    if (isLoading) {
        return (
            <div className="flex-row flex-align-center flex-justify-center" style={{ padding: "2rem", gap: "1rem" }}>
                <Spinner />
                <span>{t("Loading auto-reply...")}</span>
            </div>
        );
    }

    if (isError) {
        return (
            <Banner type="error">
                <p>{t("Failed to load auto-reply. Please try again.")}</p>
            </Banner>
        );
    }

    const errorFallback = (
        <Banner type="error">
            <p>{t("Failed to load auto-reply. Please try again.")}</p>
        </Banner>
    );

    return (
        <ErrorBoundary fallback={errorFallback}>
            <AutoreplyComposeForm
                mailboxId={mailboxId}
                defaultValue={{
                    id: autoreply.id,
                    name: autoreply.name,
                    is_active: autoreply.is_active,
                    raw_body: data?.data?.raw_body ?? undefined,
                    metadata: data?.data?.metadata ?? autoreply.metadata,
                }}
                onSuccess={onSuccess}
                onDirtyChange={onDirtyChange}
            />
        </ErrorBoundary>
    );
};

type AutoreplyComposeFormProps = {
    mailboxId: string;
    defaultValue?: {
        id: string;
        name: string;
        is_active: boolean;
        raw_body?: string | null;
        metadata?: Record<string, unknown>;
    };
    onSuccess?: () => void;
    onDirtyChange?: (isDirty: boolean) => void;
}

const SCHEDULE_TYPE_OPTIONS = [
    { label: i18n.t("Always"), value: "always" },
    { label: i18n.t("Date range"), value: "date_range" },
    { label: i18n.t("Recurring weekly"), value: "recurring_weekly" },
];

const WEEKDAY_OPTIONS = [
    { label: i18n.t("Monday"), value: "1" },
    { label: i18n.t("Tuesday"), value: "2" },
    { label: i18n.t("Wednesday"), value: "3" },
    { label: i18n.t("Thursday"), value: "4" },
    { label: i18n.t("Friday"), value: "5" },
    { label: i18n.t("Saturday"), value: "6" },
    { label: i18n.t("Sunday"), value: "7" },
];

const TIMEZONE_OPTIONS = Intl.supportedValuesOf("timeZone").map((tz) => ({
    label: tz.replace(/_/g, " "),
    value: tz,
}));

const autoreplyComposerSchema = () => z.object({
    name: z.string().min(1, { error: i18n.t("Name is required") }),
    is_active: z.boolean(),
    rawBody: z.string().min(1, { error: i18n.t("Content is required") }),
    schedule_type: z.enum(["always", "date_range", "recurring_weekly"]),
    start_at: z.string().optional(),
    end_at: z.string().optional(),
    interval_start_day: z.string().optional(),
    interval_start_time: z.string().optional(),
    interval_end_day: z.string().optional(),
    interval_end_time: z.string().optional(),
    timezone: z.string().optional(),
}).superRefine((data, ctx) => {
    if (data.schedule_type === "date_range") {
        if (!data.start_at) {
            ctx.addIssue({ code: z.ZodIssueCode.custom, message: i18n.t("Start date is required"), path: ["start_at"] });
        }
        if (!data.end_at) {
            ctx.addIssue({ code: z.ZodIssueCode.custom, message: i18n.t("End date is required"), path: ["end_at"] });
        }
        if (data.start_at && data.end_at && data.start_at >= data.end_at) {
            ctx.addIssue({ code: z.ZodIssueCode.custom, message: i18n.t("Start date must be before end date"), path: ["start_at"] });
        }
    }
    if (data.schedule_type === "recurring_weekly") {
        if (!data.interval_start_day) {
            ctx.addIssue({ code: z.ZodIssueCode.custom, message: i18n.t("Start day is required"), path: ["interval_start_day"] });
        }
        if (!data.interval_start_time) {
            ctx.addIssue({ code: z.ZodIssueCode.custom, message: i18n.t("Start time is required"), path: ["interval_start_time"] });
        }
        if (!data.interval_end_day) {
            ctx.addIssue({ code: z.ZodIssueCode.custom, message: i18n.t("End day is required"), path: ["interval_end_day"] });
        }
        if (!data.interval_end_time) {
            ctx.addIssue({ code: z.ZodIssueCode.custom, message: i18n.t("End time is required"), path: ["interval_end_time"] });
        }
    }
});

type AutoreplyComposerFormData = z.infer<ReturnType<typeof autoreplyComposerSchema>>;

const AutoreplyComposeForm = ({ mailboxId, defaultValue, onSuccess, onDirtyChange }: AutoreplyComposeFormProps) => {
    const { t } = useTranslation();
    const composerRef = useRef<Base64ComposerHandle>(null);
    const metadata = defaultValue?.metadata;

    const intervals = metadata?.intervals as Array<Record<string, unknown>> | undefined;
    const interval0 = intervals?.[0];

    const form = useForm<AutoreplyComposerFormData>({
        resolver: zodResolver(autoreplyComposerSchema()),
        defaultValues: {
            name: defaultValue?.name ?? "",
            is_active: defaultValue?.is_active ?? true,
            rawBody: defaultValue?.raw_body ?? undefined,
            schedule_type: (metadata?.schedule_type as "always" | "date_range" | "recurring_weekly") ?? "always",
            start_at: (metadata?.start_at as string) ?? "",
            end_at: (metadata?.end_at as string) ?? "",
            interval_start_day: interval0?.start_day != null ? String(interval0.start_day) : "",
            interval_start_time: (interval0?.start_time as string) ?? "",
            interval_end_day: interval0?.end_day != null ? String(interval0.end_day) : "",
            interval_end_time: (interval0?.end_time as string) ?? "",
            timezone: (metadata?.timezone as string) ?? Intl.DateTimeFormat().resolvedOptions().timeZone,
        }
    });
    const { mutateAsync: createAutoreply, isPending } = useMailboxesMessageTemplatesCreate();
    const { mutateAsync: updateAutoreply, isPending: isUpdating } = useMailboxesMessageTemplatesUpdate();
    const isSubmitting = isPending || isUpdating;

    const scheduleType = useWatch({ control: form.control, name: "schedule_type" });

    useEffect(() => {
        onDirtyChange?.(form.formState.isDirty);
    }, [form.formState.isDirty, onDirtyChange]);

    const buildMetadata = (data: AutoreplyComposerFormData): Record<string, unknown> => {
        const meta: Record<string, unknown> = {
            schedule_type: data.schedule_type,
        };
        if (data.schedule_type === "date_range") {
            meta.start_at = data.start_at;
            meta.end_at = data.end_at;
            if (data.timezone) meta.timezone = data.timezone;
        }
        if (data.schedule_type === "recurring_weekly") {
            meta.intervals = [
                {
                    start_day: Number(data.interval_start_day),
                    start_time: data.interval_start_time,
                    end_day: Number(data.interval_end_day),
                    end_time: data.interval_end_time,
                },
            ];
            if (data.timezone) meta.timezone = data.timezone;
        }
        return meta;
    };

    const onSubmit = async (data: AutoreplyComposerFormData): Promise<void> => {
        if (!composerRef.current) return;
        const { htmlBody, textBody } = await composerRef.current.exportContent();
        if (!textBody) {
            form.setError("rawBody", { message: t("Content is required") });
            return;
        }
        try {
            const signatureId = extractSignatureId(data.rawBody);
            const payload = {
                name: data.name,
                type: MessageTemplateTypeChoices.autoreply,
                is_active: data.is_active,
                html_body: htmlBody,
                text_body: textBody,
                raw_body: data.rawBody,
                metadata: buildMetadata(data),
                signature_id: signatureId,
            };
            if (defaultValue?.id) {
                await updateAutoreply({
                    mailboxId,
                    id: defaultValue.id,
                    data: payload,
                });
            } else {
                await createAutoreply({
                    mailboxId,
                    data: payload,
                });
            }
        } catch (error) {
            handle(error);
            addToast(
                <ToasterItem type="error">
                    <span>{t("Failed to save auto-reply. Please try again.")}</span>
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
                    <TemplateComposer
                        ref={composerRef}
                        defaultValue={defaultValue?.raw_body}
                        state={form.formState.errors.rawBody?.message ? "error" : "default"}
                        text={form.formState.errors.rawBody?.message && t(form.formState.errors.rawBody.message)}
                        blockNoteOptions={{ autofocus: "end" }}
                        allowVariables={false}
                    />
                </div>
                <div className="form-field-row">
                    <RhfCheckbox
                        label={t('Active')}
                        name="is_active"
                        text={t('While the auto-reply is disabled, it will not be sent.')}
                        fullWidth
                    />
                </div>
                <div className="form-field-row">
                    <RhfSelect
                        label={t('Schedule')}
                        name="schedule_type"
                        options={SCHEDULE_TYPE_OPTIONS}
                        fullWidth
                    />
                </div>
                {scheduleType === "date_range" && (
                    <>
                        <div className="form-field-row" style={{ display: "flex", gap: "1rem", alignItems: "flex-end" }}>
                            <RhfInput
                                label={t('Start date')}
                                name="start_at"
                                type="datetime-local"
                                placeholder=" "
                                text={form.formState.errors.start_at?.message && t(form.formState.errors.start_at.message)}
                                fullWidth
                            />
                            <span style={{ paddingBottom: "0.7rem", flexShrink: 0, fontSize: "1.25rem", fontWeight: 700 }}>{"\u2192"}</span>
                            <RhfInput
                                label={t('End date')}
                                name="end_at"
                                type="datetime-local"
                                placeholder=" "
                                text={form.formState.errors.end_at?.message && t(form.formState.errors.end_at.message)}
                                fullWidth
                            />
                        </div>
                        <div className="form-field-row">
                            <RhfSelect
                                label={t('Timezone')}
                                name="timezone"
                                options={TIMEZONE_OPTIONS}
                                searchable
                                fullWidth
                            />
                        </div>
                    </>
                )}
                {scheduleType === "recurring_weekly" && (
                    <>
                        <div className="form-field-row" style={{ display: "flex", gap: "1rem", alignItems: "flex-end" }}>
                            <RhfSelect
                                label={t('Start day')}
                                name="interval_start_day"
                                options={WEEKDAY_OPTIONS}
                                fullWidth
                            />
                            <RhfInput
                                label={t('Start time')}
                                name="interval_start_time"
                                type="time"
                                fullWidth
                            />
                            <span style={{ paddingBottom: "0.7rem", flexShrink: 0, fontSize: "1.25rem", fontWeight: 700 }}>{"\u2192"}</span>
                            <RhfSelect
                                label={t('End day')}
                                name="interval_end_day"
                                options={WEEKDAY_OPTIONS}
                                fullWidth
                            />
                            <RhfInput
                                label={t('End time')}
                                name="interval_end_time"
                                type="time"
                                fullWidth
                            />
                        </div>
                        <div className="form-field-row">
                            <RhfSelect
                                label={t('Timezone')}
                                name="timezone"
                                options={TIMEZONE_OPTIONS}
                                searchable
                                fullWidth
                            />
                        </div>
                    </>
                )}
                <div className="form-actions">
                    <Button type="submit" disabled={isSubmitting}>
                        {isSubmitting ? t('Saving...') : t('Save')}
                    </Button>
                </div>
            </form>
        </FormProvider>
    );
};
