import { FocusEventHandler, useEffect, useMemo, useState } from "react";
import z from "zod";
import { zodResolver } from "@hookform/resolvers/zod";
import { FormProvider, useForm, useWatch } from "react-hook-form";
import { Button } from "@openfun/cunningham-react";
import { Spinner } from "@gouvfr-lasuite/ui-kit";
import { useTranslation } from "react-i18next";
import { useRouter } from "next/router";
import { useImportFileCreate, useImportImapCreate } from "@/features/api/gen";
import MailHelper from "@/features/utils/mail-helper";
import { RhfInput } from "../../forms/components/react-hook-form";
import { RhfFileUploader } from "../../forms/components/react-hook-form/rhf-file-uploader";
import { RhfCheckbox } from "../../forms/components/react-hook-form/rhf-checkbox";
import { Banner } from "@/features/ui/components/banner";


const usernameSchema = z
    .string()
    .nonempty('message_importer_modal.form.errors.username_required')
    .email('message_importer_modal.form.errors.username_invalid');
const importerFormSchema = z.object({
    archive_file: z.array(z.instanceof(File)),
    username: usernameSchema.optional(),
    imap_server: z
        .string()
        .nonempty('message_importer_modal.form.errors.imap_server_required')
        .optional(),
    imap_port: z
        .number()
        .min(1)
        .max(65535)
        .optional(),
    use_ssl: z
        .boolean()
        .optional(),
    password: z
        .string()
        .nonempty('message_importer_modal.form.errors.password_required')
        .optional(),
})

type FormFields = z.infer<typeof importerFormSchema>;

type StepFormProps = {
    onSuccess: (taskId: string) => void;
    onError: (error: string) => void;

}
export const StepForm = ({ onSuccess, onError }: StepFormProps) => {
    const { t } = useTranslation();
    const router = useRouter();
    const [showAdvancedImapFields, setShowAdvancedImapFields] = useState(false);
    const [emailDomain, setEmailDomain] = useState<string | undefined>(undefined);
    const imapMutation = useImportImapCreate({
        mutation: {
            meta: { noGlobalError: true },
            onError: () => {
                onError('message_importer_modal.api_errors.default');
            },
            onSuccess: (data) => onSuccess(data.data.task_id!)
        }
    });
    const archiveMutation = useImportFileCreate({
        mutation: {
            meta: { noGlobalError: true },
            onError: () => {
                onError('message_importer_modal.api_errors.default');
            },
            onSuccess: (data) => onSuccess(data.data.task_id!)
        }
    });
    const isPending = imapMutation.isPending || archiveMutation.isPending;

    const defaultValues = {
        imap_server: '',
        imap_port: 993,
        use_ssl: true,
        username: '',
        password: '',
        archive_file: [],
    }

    const form = useForm({
        resolver: zodResolver(importerFormSchema),
        mode: "onBlur",
        reValidateMode: "onBlur",
        shouldFocusError: false,
        defaultValues
    });
    const archiveFileInputValue = useWatch({
        control: form.control,
        name: 'archive_file'
    });
    const showImapForm = useMemo(() => archiveFileInputValue.length === 0, [archiveFileInputValue]);

    /**
     * Try to guess the imap server from the email address
     * If it fails, show all the form fields to invite the user to fill them manually
     */
    const discoverImapServer:FocusEventHandler<HTMLInputElement> = async () => {
        const email = form.getValues("username")!;
        const result = usernameSchema.safeParse(email);
        
        if (!email || !result.success) return;
        const imapConfig = MailHelper.getImapConfigFromEmail(email);
        const emailDomain = MailHelper.getDomainFromEmail(email);
        setEmailDomain(emailDomain);
        if (!imapConfig) {
            setShowAdvancedImapFields(true);
            form.resetField("imap_server");
            form.resetField("imap_port");
            form.resetField("use_ssl");
            return;
        }
        setShowAdvancedImapFields(false);
        form.setValue("imap_server", imapConfig.host);
        form.setValue("imap_port", imapConfig.port);
        form.setValue("use_ssl", imapConfig.use_ssl);
    };

    /**
     * Exec the mutation to import emails from an IMAP server.
     */
    const importFromImap = async (data: FormFields) => {
        const payload = {
            imap_server: data.imap_server!,
            imap_port: data.imap_port!,
            use_ssl: data.use_ssl!,
            username: data.username!,
            password: data.password!,
            recipient: router.query.mailboxId as string,
        }
        return imapMutation.mutateAsync(
            { data: payload }
        );
    }

    /**
     * Exec the mutation to import emails from an Archive file.
     */
    const importFromArchive = async (file: File) => {
        const payload = {
            import_file: file,
            recipient: router.query.mailboxId as string,
        }
        return archiveMutation.mutateAsync({ data: payload });
    }

    /**
     * According to the form data,
     * exec the mutation to import emails from an IMAP server or an Archive file.
     * We assume that all mutation returns a celery task id as response.
     */
    const handleSubmit = async (data: FormFields) => {
        if (data.archive_file.length > 0) {
            importFromArchive(data.archive_file[0]);
        } else {
            importFromImap(data);
        }
    };

    useEffect(() => {
        if (!showImapForm) {
            form.setValue('imap_server', undefined, { shouldDirty: true, shouldValidate: true });
            form.setValue('imap_port', undefined, { shouldDirty: true, shouldValidate: true });
            form.setValue('use_ssl', undefined, { shouldDirty: true, shouldValidate: true });
            form.setValue('username', undefined, { shouldDirty: true, shouldValidate: true });
            form.setValue('password', undefined, { shouldDirty: true, shouldValidate: true });
        }
    }, [showImapForm]);

    return (
        <FormProvider {...form}>
            <form
                className="modal-importer-form"
                onSubmit={form.handleSubmit(handleSubmit)}
                noValidate
            >
                <h2>{t('message_importer_modal.form.title')}</h2>
                { showImapForm === true && (
                    <>
                    <div className="form-field-row flex-justify-center">
                        <p>{t('message_importer_modal.form.imap_import_description')}</p>
                    </div>
                    <div className="form-field-row">
                        <RhfInput
                            label={t('message_importer_modal.form.labels.email_address')}
                            name="username"
                            type="email"
                            text={form.formState.errors.username ? t(form.formState.errors.username.message as string) : undefined}
                            onBlur={discoverImapServer}
                            fullWidth
                        />
                    </div>
                    <div className="form-field-row">
                        <RhfInput
                            label={t('message_importer_modal.form.labels.password')}
                            name="password"
                            type="password"
                            text={form.formState.errors.password ? t(form.formState.errors.password.message as string) : undefined}
                            fullWidth
                        />
                    </div>
                    {
                        showAdvancedImapFields ? (
                            <>
                                <div className="form-field-row flex-justify-center">
                                    <p>{t('message_importer_modal.form.imap_import_description')}</p>
                                </div>
                                <div className="form-field-row">
                                    <RhfInput
                                        name="imap_server"
                                        label={t('message_importer_modal.form.labels.imap_server')}
                                        text={form.formState.errors.imap_server ? t(form.formState.errors.imap_server.message as string) : undefined}
                                        fullWidth
                                    />
                                    <RhfInput
                                        name="imap_port"
                                        type="number"
                                        min={1}
                                        max={65535}
                                        label={t('message_importer_modal.form.labels.imap_port')}
                                        text={form.formState.errors.imap_port ? t(form.formState.errors.imap_port.message as string) : undefined}
                                        fullWidth
                                    />
                                </div>
                                <div className="form-field-row">
                                    <RhfCheckbox
                                        label={t("message_importer_modal.form.labels.use_ssl")}
                                        name="use_ssl"
                                        fullWidth
                                    />
                                </div>
                            </>
                        ) : (
                            <>
                                <input type="hidden" {...form.register('imap_server')} />
                                <input type="hidden" {...form.register('imap_port')} />
                                <input type="hidden" {...form.register('use_ssl')} />
                            </>
                        )
                    }
                    {
                        emailDomain && (
                            <Banner type="info">
                                <p>{t('message_importer_modal.form.imap_banner.helper')}</p>
                                <p><LinkToDoc imapDomain={emailDomain} /></p>
                            </Banner>
                        )
                    }
                    <div className="form-field-row flex-justify-center modal-importer-form__or-separator">
                        <p>{t('message_importer_modal.form.or')}</p>
                    </div>
                    </>
                )}
                <div className="form-field-row flex-justify-center">
                    <p>{t('message_importer_modal.form.upload_archive_file')}</p>
                </div>
                <div className="form-field-row">
                    <RhfFileUploader
                        name="archive_file"
                        accept=".eml,.mbox"
                        icon={<span className="material-icons">inventory_2</span>}
                        fileSelectedIcon={<span className="material-icons">inventory_2</span>}
                        bigText={t('message_importer_modal.form.labels.archive_file_description')}
                        text={t('message_importer_modal.form.labels.archive_file_helper')}
                        fullWidth
                    />
                </div>
                <div className="form-field-row">
                    <Button
                        type="submit"
                        aria-busy={isPending}
                        disabled={isPending}
                        icon={isPending ? <Spinner size="sm" /> : undefined}
                        fullWidth
                    >
                        {t('actions.import')}
                    </Button>
                </div>
            </form>
        </FormProvider>
    );
};


const LinkToDoc = ({ imapDomain }: { imapDomain: string }) => {
    const { t } = useTranslation();
    const domainDoc = {
        "gmail.com": {
            displayName: "Gmail",
            href: "https://support.google.com/accounts/answer/185833"
        },
        "orange.fr": {
            displayName: "Orange",
            href: "https://assistancepro.orange.fr/mail_pro/parametrer_le_mail_pro/sur_un_ordinateur/messagerie_pro_orange__activer_ou_desactiver_le_protocole_popimap_pour_les_logiciels_de_messagerie_-448045"
        },
        "wanadoo.fr": {
            displayName: "Wanadoo",
            href: "https://assistancepro.orange.fr/mail_pro/parametrer_le_mail_pro/sur_un_ordinateur/messagerie_pro_orange__activer_ou_desactiver_le_protocole_popimap_pour_les_logiciels_de_messagerie_-448045"
        },
    }
    const doc = domainDoc[imapDomain as keyof typeof domainDoc];

    if (!doc) return null;
    return <a href={doc.href} target="_blank" rel="noreferrer noopener">{t('message_importer_modal.form.imap_banner.link_label', { name: doc.displayName })}</a>
}
