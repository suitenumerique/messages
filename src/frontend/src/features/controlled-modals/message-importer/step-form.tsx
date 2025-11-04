import { FocusEventHandler, useEffect, useMemo, useState } from "react";
import z from "zod";
import { zodResolver } from "@hookform/resolvers/zod";
import { FormProvider, useForm, useWatch } from "react-hook-form";
import { Button } from "@openfun/cunningham-react";
import { Spinner } from "@gouvfr-lasuite/ui-kit";
import { useTranslation } from "react-i18next";
import { useRouter } from "next/router";
import { importFileCreateResponse202, importImapCreateResponse202, useImportFileCreate, useImportImapCreate } from "@/features/api/gen";
import MailHelper, { IMAP_DOMAIN_REGEXES } from "@/features/utils/mail-helper";
import { RhfInput } from "../../forms/components/react-hook-form";
import { RhfFileUploader } from "../../forms/components/react-hook-form/rhf-file-uploader";
import { RhfCheckbox } from "../../forms/components/react-hook-form/rhf-checkbox";
import { Banner } from "@/features/ui/components/banner";
import i18n from "@/features/i18n/initI18n";
import { BucketUploadState, useBucketUpload } from "./use-bucket-upload";
import ProgressBar from "@/features/ui/components/progress-bar";
import { IMPORT_STEP } from ".";

const usernameSchema = z.email({ error: i18n.t('The email address is invalid.') });

const importerFormSchema = z.object({
    archive_file: z.array(z.instanceof(File)),
    username: usernameSchema.optional(),
    imap_server: z
        .string()
        .nonempty({ error: i18n.t('IMAP server is required.') })
        .optional(),
    imap_port: z
        .preprocess(
            (value: string | number) => typeof value === 'number' ? value : parseInt(value, 10),
            z.int().min(1).max(65535)
        ).optional(),
    use_ssl: z
        .boolean()
        .optional(),
    password: z
        .string()
        .nonempty({ error: i18n.t('Password is required.') })
        .optional(),
})

type FormFields = z.infer<typeof importerFormSchema>;

type StepFormProps = {
    onUploading: () => void;
    onSuccess: (taskId: string) => void;
    onError: (error: string | null) => void;
    error: string | null;
    step: IMPORT_STEP;
}
export const StepForm = ({ onUploading, onSuccess, onError, error, step }: StepFormProps) => {
    const { t } = useTranslation();
    const router = useRouter();
    const [showAdvancedImapFields, setShowAdvancedImapFields] = useState(false);
    const [emailDomain, setEmailDomain] = useState<string | undefined>(undefined);
    const imapMutation = useImportImapCreate({
        mutation: {
            meta: { noGlobalError: true },
            onError: () => onError(t('An error occurred while importing messages.')),
            onSuccess: (data) => onSuccess((data as importImapCreateResponse202).data.task_id!)
        }
    });
    const archiveMutation = useImportFileCreate({
        mutation: {
            meta: { noGlobalError: true },
            onError: () => onError(t('An error occurred while importing messages.')),
            onSuccess: (data) => onSuccess((data as importFileCreateResponse202).data.task_id!)
        }
    });
    const bucketUploadManager = useBucketUpload({
        onSuccess: (manager) => archiveMutation.mutate({
            data: {
                filename: manager.file!.name,
                recipient: router.query.mailboxId as string,
            }
        }, {
            onSettled: manager.reset,
        }),
        onError: (error) => {
            if (error === 'Aborted') onError(t('You have aborted the upload.'));
            else onError(t('An error occurred while uploading the archive file.'));
        },
    });
    const isBucketUploading = [BucketUploadState.INITIATING, BucketUploadState.IMPORTING, BucketUploadState.COMPLETING].includes(bucketUploadManager.state);
    const isPending = imapMutation.isPending || archiveMutation.isPending || isBucketUploading;

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
    const discoverImapServer: FocusEventHandler<HTMLInputElement> = async () => {
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
        bucketUploadManager.upload(file);
        onUploading();
    }

    /**
     * According to the form data,
     * exec the mutation to import emails from an IMAP server or an Archive file.
     * We assume that all mutation returns a celery task id as response.
     */
    const handleSubmit = async (data: FormFields) => {
        onError(null);
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
                {step === 'uploading'
                    ? <h2>{t('Uploading your archive')}</h2>
                    : <h2>{t('First, we need some information about your old mailbox')}</h2>
                }
                {showImapForm === true && (
                    <>
                        <div className="form-field-row flex-justify-center">
                            <p>{t('Indicate your old email address and your password.')}</p>
                        </div>
                        <div className="form-field-row">
                            <RhfInput
                                label={t('Email address')}
                                name="username"
                                type="email"
                                text={form.formState.errors.username ? t(form.formState.errors.username.message as string) : undefined}
                                onBlur={discoverImapServer}
                                fullWidth
                            />
                        </div>
                        <div className="form-field-row">
                            <RhfInput
                                label={t('Password')}
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
                                        <p>{t('Indicate your old email address and your password.')}</p>
                                    </div>
                                    <div className="form-field-row">
                                        <RhfInput
                                            name="imap_server"
                                            label={t('IMAP server')}
                                            text={form.formState.errors.imap_server ? t(form.formState.errors.imap_server.message as string) : undefined}
                                            fullWidth
                                        />
                                        <RhfInput
                                            name="imap_port"
                                            type="number"
                                            min={1}
                                            max={65535}
                                            label={t('IMAP port')}
                                            text={form.formState.errors.imap_port ? t(form.formState.errors.imap_port.message as string) : undefined}
                                            fullWidth
                                        />
                                    </div>
                                    <div className="form-field-row">
                                        <RhfCheckbox
                                            label={t("Use SSL")}
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
                                    <p>{t('To be able to import emails from an IMAP server, you may need to allow IMAP access on your account.')}</p>
                                    <p><LinkToDoc imapDomain={emailDomain} /></p>
                                </Banner>
                            )
                        }
                        <div className="form-field-row flex-justify-center modal-importer-form__or-separator">
                            <p>{t('Or')}</p>
                        </div>
                    </>
                )}
                {step !== 'uploading' && (
                <div className="form-field-row flex-justify-center">
                    <p>{t('Upload an archive')}</p>
                </div>
                )}
                <div className="form-field-row archive_file_field">
                    <RhfFileUploader
                        name="archive_file"
                        accept=".eml,.mbox"
                        icon={<span className="material-icons">inventory_2</span>}
                        fileSelectedIcon={<span className="material-icons">inventory_2</span>}
                        bigText={t('Drag and drop an archive here')}
                        text={t('EML or MBOX')}
                        fullWidth
                    />
                    {[BucketUploadState.INITIATING, BucketUploadState.IMPORTING, BucketUploadState.COMPLETING, BucketUploadState.COMPLETED].includes(bucketUploadManager.state) && (
                        <div className="progress-bar-container">
                            <ProgressBar progress={bucketUploadManager.progress} />
                            <p>{t('Uploading... {{progress}}%', { progress: bucketUploadManager.progress })}</p>
                        </div>
                    )}
                </div>
                {error && (<Banner type="error"><p>{t(error)}</p></Banner>)}
                <div className="form-field-row">
                    {[BucketUploadState.IMPORTING].includes(bucketUploadManager.state) ? (
                        <Button
                            type="button"
                            onClick={bucketUploadManager.abort}
                            color="tertiary"
                            fullWidth
                        >
                            {t('Abort upload')}
                        </Button>

                    ) : (
                        <Button
                            type="submit"
                            aria-busy={isPending}
                            disabled={isPending}
                            icon={isPending ? <Spinner size="sm" /> : undefined}
                            fullWidth
                        >
                            {t('Import')}
                        </Button>
                    )}
                </div>
            </form>
        </FormProvider>
    );
};


const LinkToDoc = ({ imapDomain }: { imapDomain: string }) => {
    const { t } = useTranslation();
    const domainDoc = {
        [IMAP_DOMAIN_REGEXES.get("gmail")!]: {
            displayName: "Gmail",
            href: "https://support.google.com/accounts/answer/185833"
        },
        [IMAP_DOMAIN_REGEXES.get("orange")!]: {
            displayName: "Orange",
            href: "https://assistance.orange.fr/ordinateurs-peripheriques/installer-et-utiliser/l-utilisation-du-mail-et-du-cloud/mail-orange/le-mail-orange-nouvelle-version/parametrer-la-boite-mail/mail-orange-comment-activer-ou-desactiver-les-acces-pop-imap-pour-les-logiciels-ou-applications-de-messagerie-tiers_427398-957069"
        },
        [IMAP_DOMAIN_REGEXES.get("wanadoo")!]: {
            displayName: "Wanadoo",
            href: "https://assistance.orange.fr/ordinateurs-peripheriques/installer-et-utiliser/l-utilisation-du-mail-et-du-cloud/mail-orange/le-mail-orange-nouvelle-version/parametrer-la-boite-mail/mail-orange-comment-activer-ou-desactiver-les-acces-pop-imap-pour-les-logiciels-ou-applications-de-messagerie-tiers_427398-957069"
        },
        [IMAP_DOMAIN_REGEXES.get("yahoo")!]: {
            displayName: "Yahoo!",
            href: "https://fr.aide.yahoo.com/kb/SLN4075.html?activity=yhelp-signin&guccounter=1&guce_referrer=aHR0cHM6Ly9sb2dpbi55YWhvby5jb20v&guce_referrer_sig=AQAAAL_UYOz8UEdd09wJ1xwaD2Wk7ZEVJTpLoR2yd3KnPbAE4SJGaRT33BA_kqufMpZRtaNzcoOlt7D8hHCog4XCzkqWNwQTfq8pQCqNk3PxxeZ-SwPx_gNC7wl6aPZ_f7JDM-_Co419TiTtNKwZ2f2cxleG_AqbLPzRblPmozI3STS0"
        }
    }
    const doc = Array.from(Object.entries(domainDoc)).find(([regex]) => new RegExp(`^${regex}$`).test(imapDomain))?.[1];

    if (!doc) return null;
    return <a href={doc.href} target="_blank" rel="noreferrer noopener">{t('How to allow IMAP connections from your account {{name}}?', { name: doc.displayName })}</a>
}
