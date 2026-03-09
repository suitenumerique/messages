import { Button } from "@gouvfr-lasuite/cunningham-react";
import { Icon, IconType, IconSize } from "@gouvfr-lasuite/ui-kit";
import { useTranslation } from "react-i18next";
import { useForm, FormProvider } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import * as z from "zod";
import { useState, useMemo } from "react";
import { useQueryClient } from "@tanstack/react-query";
import {
    Channel,
    useMailboxesChannelsCreate,
    useMailboxesChannelsPartialUpdate,
    useMailboxesChannelsRotatePasswordCreate,
    getMailboxesChannelsListUrl,
} from "@/features/api/gen";
import { useMailboxContext } from "@/features/providers/mailbox";
import { RhfInput } from "@/features/forms/components/react-hook-form";
import { RhfSelect } from "@/features/forms/components/react-hook-form/rhf-select";
import { addToast, ToasterItem } from "@/features/ui/components/toaster";
import { Banner } from "@/features/ui/components/banner";
import { handle } from "@/features/utils/errors";
import { useConfig } from "@/features/providers/config";

type ChannelCreateResponse = Channel & { password?: string };

type ClientBridgeIntegrationFormProps = {
    channel?: Channel;
    onSuccess: (channel: Channel) => void;
    onClose: () => void;
};

const formSchema = (t: (key: string) => string) => z.object({
    name: z.string().min(1, { message: t("Name is required.") }),
    role: z.enum(["reader", "editor", "sender", "sender_only"]),
});

type FormFields = z.infer<ReturnType<typeof formSchema>>;

export const ClientBridgeIntegrationForm = ({
    channel,
    onSuccess,
    onClose,
}: ClientBridgeIntegrationFormProps) => {
    const { t } = useTranslation();
    const { selectedMailbox } = useMailboxContext();
    const queryClient = useQueryClient();
    const [error, setError] = useState<string | null>(null);
    const [generatedPassword, setGeneratedPassword] = useState<string | null>(null);
    const [isRotating, setIsRotating] = useState(false);
    const isEditing = !!channel;

    const createMutation = useMailboxesChannelsCreate();
    const updateMutation = useMailboxesChannelsPartialUpdate();
    const rotateMutation = useMailboxesChannelsRotatePasswordCreate();

    const schema = useMemo(() => formSchema(t), [t]);

    const form = useForm<FormFields>({
        resolver: zodResolver(schema),
        defaultValues: {
            name: channel?.name || t("My email client"),
            role: ((channel?.settings as Record<string, string>)?.role as FormFields["role"]) || "sender",
        },
    });

    const errors = form.formState.errors;

    const invalidateChannels = async () => {
        await queryClient.invalidateQueries({
            queryKey: [getMailboxesChannelsListUrl(selectedMailbox!.id)],
            exact: false
        });
    };

    const onSubmit = async (data: FormFields) => {
        setError(null);

        try {
            if (isEditing && channel) {
                await updateMutation.mutateAsync({
                    mailboxId: selectedMailbox!.id,
                    id: channel.id,
                    data: { name: data.name, settings: { role: data.role } },
                });
                addToast(
                    <ToasterItem type="info">
                        <span>{t("Integration updated!")}</span>
                    </ToasterItem>
                );
                await invalidateChannels();
            } else {
                const response = await createMutation.mutateAsync({
                    mailboxId: selectedMailbox!.id,
                    data: {
                        name: data.name,
                        type: "client-bridge",
                        settings: { role: data.role },
                    },
                });
                await invalidateChannels();
                const password = (response.data as ChannelCreateResponse).password;
                if (password) {
                    setGeneratedPassword(password);
                }
                onSuccess(response.data);
            }
        } catch (err) {
            handle(err);
            setError(t("An error occurred while saving the integration."));
        }
    };

    const handleRotatePassword = async () => {
        if (!channel) return;
        setIsRotating(true);
        setError(null);
        try {
            const resp = await rotateMutation.mutateAsync({
                mailboxId: selectedMailbox!.id,
                id: channel.id,
            });
            const password = resp.data?.password;
            if (password) {
                setGeneratedPassword(password);
                addToast(
                    <ToasterItem type="info">
                        <span>{t("Password rotated successfully!")}</span>
                    </ToasterItem>
                );
            }
        } catch (err) {
            handle(err);
            setError(t("An error occurred while rotating the password."));
        } finally {
            setIsRotating(false);
        }
    };

    const mailboxEmail = selectedMailbox?.email ?? "";

    // After creation or rotation, show the password
    if (generatedPassword) {
        return (
            <div className="widget-integration-form">
                <div className="widget-integration-form__section">
                    <Banner type="warning">
                        {t("Save this password now. You won't be able to see it again.")}
                    </Banner>
                </div>
                <ConnectionDetails mailboxEmail={mailboxEmail} generatedPassword={generatedPassword} />
                <div className="widget-integration-form__actions">
                    <Button type="button" onClick={onClose}>
                        {t("Done")}
                    </Button>
                </div>
            </div>
        );
    }

    // Editing an existing integration — show connection details with rotate
    if (isEditing) {
        return (
            <FormProvider {...form}>
            <form onSubmit={form.handleSubmit(onSubmit)} className="widget-integration-form">
                <div className="widget-integration-form__section">
                    <h3>{t("General")}</h3>
                    <RhfInput
                        label={t("Name")}
                        name="name"
                        text={errors.name?.message || t("This name is for internal use only and will not be visible to users.")}
                        state={errors.name ? "error" : "default"}
                        fullWidth
                    />
                    <RhfSelect
                        label={t("Role")}
                        name="role"
                        text={t("Controls what this integration can do: read emails, edit flags, or send messages.")}
                        options={[
                            { value: "reader", label: t("Reader — read-only IMAP access") },
                            { value: "editor", label: t("Editor — IMAP read and edit flags") },
                            { value: "sender", label: t("Sender — full IMAP and SMTP access") },
                            { value: "sender_only", label: t("Sender only — SMTP send, no IMAP") },
                        ]}
                        fullWidth
                    />
                    <div className="widget-integration-form__actions">
                        <Button
                            type="submit"
                            size="small"
                            disabled={updateMutation.isPending}
                        >
                            {t("Save changes")}
                        </Button>
                    </div>
                </div>

                {error && (
                    <Banner type="error">{error}</Banner>
                )}

                <ConnectionDetails
                    mailboxEmail={mailboxEmail}
                    onRotatePassword={handleRotatePassword}
                    isRotating={isRotating}
                />
            </form>
            </FormProvider>
        );
    }

    // Creating a new integration
    return (
        <FormProvider {...form}>
        <form onSubmit={form.handleSubmit(onSubmit)} className="widget-integration-form">
            <div className="widget-integration-form__section">
                <h3>{t("General")}</h3>
                <RhfInput
                    label={t("Name")}
                    name="name"
                    text={errors.name?.message || t("This name is for internal use only and will not be visible to users.")}
                    state={errors.name ? "error" : "default"}
                    fullWidth
                />
                <RhfSelect
                    label={t("Role")}
                    name="role"
                    text={t("Controls what this integration can do: read emails, edit flags, or send messages.")}
                    options={[
                        { value: "reader", label: t("Reader — read-only IMAP access") },
                        { value: "editor", label: t("Editor — IMAP read and edit flags") },
                        { value: "sender", label: t("Sender — full IMAP and SMTP access") },
                        { value: "sender_only", label: t("Sender only — SMTP send, no IMAP") },
                    ]}
                    fullWidth
                />
            </div>

            {error && (
                <Banner type="error">{error}</Banner>
            )}

            <div className="widget-integration-form__actions">
                <Button type="button" variant="secondary" onClick={onClose}>
                    {t("Cancel")}
                </Button>
                <Button
                    type="submit"
                    disabled={createMutation.isPending}
                >
                    {t("Create integration")}
                </Button>
            </div>
        </form>
        </FormProvider>
    );
};

const CopyButton = ({ value }: { value: string }) => {
    const { t } = useTranslation();
    return (
        <Button
            type="button"
            variant="tertiary"
            size="small"
            icon={<Icon name="content_copy" type={IconType.OUTLINED} size={IconSize.SMALL} />}
            onClick={() => {
                navigator.clipboard.writeText(value).then(() => {
                    addToast(
                        <ToasterItem type="info">
                            <span>{t("Copied to clipboard")}</span>
                        </ToasterItem>
                    );
                }).catch(() => {
                    addToast(
                        <ToasterItem type="error">
                            <span>{t("Failed to copy to clipboard")}</span>
                        </ToasterItem>
                    );
                });
            }}
            aria-label={t("Copy")}
        />
    );
};

type ConnectionDetailsProps = {
    mailboxEmail: string;
    generatedPassword?: string;
    onRotatePassword?: () => void;
    isRotating?: boolean;
};

const ConnectionDetails = ({ mailboxEmail, generatedPassword, onRotatePassword, isRotating }: ConnectionDetailsProps) => {
    const { t } = useTranslation();
    const config = useConfig();
    const bridgeConfig = config.CLIENTBRIDGE_PUBLIC_CONFIG;

    const imapHost = bridgeConfig?.imap_host ?? window.location.hostname;
    const imapPort = String(bridgeConfig?.imap_port ?? 993);
    const imapSecurity = bridgeConfig?.imap_security ?? "SSL/TLS";
    const smtpHost = bridgeConfig?.smtp_host ?? window.location.hostname;
    const smtpPort = String(bridgeConfig?.smtp_port ?? 587);
    const smtpSecurity = bridgeConfig?.smtp_security ?? "STARTTLS";

    return (
        <div className="widget-integration-form__section">
            <h3>{t("Connection details")}</h3>
            <p className="widget-integration-form__section-description">
                {t("Use these settings to configure your email client (Thunderbird or your mobile phone).")}
            </p>
            <div className="client-bridge-form__details">
                <dl className="client-bridge-form__detail-list">
                    <div className="client-bridge-form__detail-item">
                        <dt>{t("Username")}</dt>
                        <dd>
                            <code>{mailboxEmail}</code>
                            <CopyButton value={mailboxEmail} />
                        </dd>
                    </div>
                    {generatedPassword && (
                        <div className="client-bridge-form__detail-item">
                            <dt>{t("Password")}</dt>
                            <dd>
                                <code>{generatedPassword}</code>
                                <CopyButton value={generatedPassword} />
                            </dd>
                        </div>
                    )}
                    {!generatedPassword && onRotatePassword && (
                        <div className="client-bridge-form__detail-item">
                            <dt>{t("Password")}</dt>
                            <dd>
                                <span className="widget-integration-form__section-description">••••••••</span>
                                <Button
                                    type="button"
                                    variant="secondary"
                                    size="small"
                                    onClick={onRotatePassword}
                                    disabled={isRotating}
                                >
                                    {isRotating ? t("Rotating...") : t("Rotate password")}
                                </Button>
                            </dd>
                        </div>
                    )}
                </dl>
            </div>
            <h4>{t("Incoming mail (IMAP)")}</h4>
            <div className="client-bridge-form__details">
                <dl className="client-bridge-form__detail-list">
                    <div className="client-bridge-form__detail-item">
                        <dt>{t("Server")}</dt>
                        <dd>
                            <code>{imapHost}</code>
                            <CopyButton value={imapHost} />
                        </dd>
                    </div>
                    <div className="client-bridge-form__detail-item">
                        <dt>{t("Port")}</dt>
                        <dd><code>{imapPort}</code></dd>
                    </div>
                    <div className="client-bridge-form__detail-item">
                        <dt>{t("Security")}</dt>
                        <dd>{imapSecurity}</dd>
                    </div>
                </dl>
            </div>
            <h4>{t("Outgoing mail (SMTP)")}</h4>
            <div className="client-bridge-form__details">
                <dl className="client-bridge-form__detail-list">
                    <div className="client-bridge-form__detail-item">
                        <dt>{t("Server")}</dt>
                        <dd>
                            <code>{smtpHost}</code>
                            <CopyButton value={smtpHost} />
                        </dd>
                    </div>
                    <div className="client-bridge-form__detail-item">
                        <dt>{t("Port")}</dt>
                        <dd><code>{smtpPort}</code></dd>
                    </div>
                    <div className="client-bridge-form__detail-item">
                        <dt>{t("Security")}</dt>
                        <dd>{smtpSecurity}</dd>
                    </div>
                </dl>
            </div>
        </div>
    );
};
