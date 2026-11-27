import { Button } from "@gouvfr-lasuite/ui-components";
import { useTranslation } from "react-i18next";
import { useForm, FormProvider } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import * as z from "zod";
import { useState, useMemo } from "react";
import { useQueryClient } from "@tanstack/react-query";
import {
    AppPasswordScopeEnum,
    Channel,
    ChannelCreateResponse,
    Mailbox,
    RegeneratedSecretResponse,
    useMailboxesChannelsCreate,
    useMailboxesChannelsPartialUpdate,
    useMailboxesChannelsRegenerateSecretCreate,
    getMailboxesChannelsListUrl,
} from "@/features/api/gen";
import {
    RhfInput,
    RhfSelect,
} from "@/features/forms/components/react-hook-form";
import { addToast, ToasterItem } from "@/features/ui/components/toaster";
import { Banner } from "@/features/ui/components/banner";
import { CopyableInput } from "@/features/ui/components/copyable-input";
import { handle } from "@/features/utils/errors";
import { useConfig } from "@/features/providers/config";

type ClientBridgeIntegrationFormProps = {
    mailbox: Mailbox;
    channel?: Channel;
    onSuccess: (channel: Channel) => void;
    onClose: () => void;
};

const formSchema = (t: (key: string) => string) => z.object({
    name: z.string().min(1, { error: t("Name is required.") }),
    role: z.enum(["reader", "editor", "sender", "sender_only"]),
});

type FormFields = z.infer<ReturnType<typeof formSchema>>;
type Role = FormFields["role"];

// Roles are presets of the scopes an app password may hold.
const READER_SCOPES: AppPasswordScopeEnum[] = [
    AppPasswordScopeEnum["threads:read"],
    AppPasswordScopeEnum["messages:read"],
    AppPasswordScopeEnum["messages:readbody"],
];
const ROLE_SCOPES: Record<Role, AppPasswordScopeEnum[]> = {
    reader: READER_SCOPES,
    editor: [...READER_SCOPES, AppPasswordScopeEnum["threads:write"]],
    sender: [
        ...READER_SCOPES,
        AppPasswordScopeEnum["threads:write"],
        AppPasswordScopeEnum["messages:send"],
    ],
    sender_only: [AppPasswordScopeEnum["messages:send"]],
};

// Scopes set outside this form match no preset: show the narrowest one, so
// saving can't widen the access.
const roleFromScopes = (scopes: unknown): Role => {
    if (!Array.isArray(scopes)) return "reader";
    const current = new Set(scopes);
    const match = (Object.keys(ROLE_SCOPES) as Role[]).find((role) =>
        ROLE_SCOPES[role].length === current.size
        && ROLE_SCOPES[role].every((scope) => current.has(scope)),
    );
    return match ?? "reader";
};

export const ClientBridgeIntegrationForm = ({
    mailbox,
    channel,
    onSuccess,
    onClose,
}: ClientBridgeIntegrationFormProps) => {
    const { t } = useTranslation();
    const queryClient = useQueryClient();
    const [error, setError] = useState<string | null>(null);
    const [generatedPassword, setGeneratedPassword] = useState<string | null>(null);
    const [isRotating, setIsRotating] = useState(false);
    const isEditing = !!channel;

    const createMutation = useMailboxesChannelsCreate();
    const updateMutation = useMailboxesChannelsPartialUpdate();
    const rotateMutation = useMailboxesChannelsRegenerateSecretCreate();

    const schema = useMemo(() => formSchema(t), [t]);

    const form = useForm<FormFields>({
        resolver: zodResolver(schema),
        defaultValues: {
            name: channel?.name || t("My email client"),
            role: channel
                ? roleFromScopes((channel.settings as Record<string, unknown> | undefined)?.scopes)
                : "sender",
        },
    });

    const errors = form.formState.errors;

    const invalidateChannels = async () => {
        await queryClient.invalidateQueries({
            queryKey: [getMailboxesChannelsListUrl(mailbox.id)],
            exact: false
        });
    };

    const onSubmit = async (data: FormFields) => {
        setError(null);

        try {
            if (isEditing && channel) {
                await updateMutation.mutateAsync({
                    mailboxId: mailbox.id,
                    id: channel.id,
                    data: { name: data.name, settings: { scopes: ROLE_SCOPES[data.role] } },
                });
                addToast(
                    <ToasterItem type="info">
                        <span>{t("Integration updated!")}</span>
                    </ToasterItem>
                );
                await invalidateChannels();
            } else {
                const response = await createMutation.mutateAsync({
                    mailboxId: mailbox.id,
                    data: {
                        name: data.name,
                        type: "client-bridge",
                        settings: { scopes: ROLE_SCOPES[data.role] },
                    },
                });
                await invalidateChannels();
                const channelData = response.data as ChannelCreateResponse;
                if (channelData.password) {
                    setGeneratedPassword(channelData.password);
                }
                onSuccess(channelData);
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
                mailboxId: mailbox.id,
                id: channel.id,
            });
            const password = (resp.data as RegeneratedSecretResponse).password;
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

    const mailboxEmail = mailbox.email;

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

type ConnectionDetailsProps = {
    mailboxEmail: string;
    generatedPassword?: string;
    onRotatePassword?: () => void;
    isRotating?: boolean;
};

type ServerProps = {
    title: string;
    host: string;
    port: number;
    security: string;
};

const ServerDetails = ({ title, host, port, security }: ServerProps) => {
    const { t } = useTranslation();
    return (
        <>
            <h4>{title}</h4>
            <div className="client-bridge-form__details">
                <dl className="client-bridge-form__detail-list">
                    <div className="client-bridge-form__detail-item">
                        <dt>{t("Server")}</dt>
                        <dd><CopyableInput value={host} aria-label={`${title} ${t("Server")}`} /></dd>
                    </div>
                    <div className="client-bridge-form__detail-item">
                        <dt>{t("Port")}</dt>
                        <dd>{port}</dd>
                    </div>
                    <div className="client-bridge-form__detail-item">
                        <dt>{t("Security")}</dt>
                        <dd>{security}</dd>
                    </div>
                </dl>
            </div>
        </>
    );
};

const ConnectionDetails = ({ mailboxEmail, generatedPassword, onRotatePassword, isRotating }: ConnectionDetailsProps) => {
    const { t } = useTranslation();
    const config = useConfig();
    // Only the deployment knows where the bridge listens: no guess from the web app's host.
    const bridgeConfig = config.CLIENTBRIDGE_PUBLIC_CONFIG;

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
                        <dd><CopyableInput value={mailboxEmail} aria-label={t("Username")} /></dd>
                    </div>
                    {generatedPassword && (
                        <div className="client-bridge-form__detail-item">
                            <dt>{t("Password")}</dt>
                            <dd><CopyableInput value={generatedPassword} aria-label={t("Password")} /></dd>
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
            {bridgeConfig ? (
                <>
                    <ServerDetails
                        title={t("Incoming mail (IMAP)")}
                        host={bridgeConfig.imap_host ?? ""}
                        port={bridgeConfig.imap_port ?? 0}
                        security={bridgeConfig.imap_security ?? ""}
                    />
                    <ServerDetails
                        title={t("Outgoing mail (SMTP)")}
                        host={bridgeConfig.smtp_host ?? ""}
                        port={bridgeConfig.smtp_port ?? 0}
                        security={bridgeConfig.smtp_security ?? ""}
                    />
                </>
            ) : (
                <Banner type="info">
                    {t("Ask your administrator for the IMAP and SMTP server settings.")}
                </Banner>
            )}
        </div>
    );
};
