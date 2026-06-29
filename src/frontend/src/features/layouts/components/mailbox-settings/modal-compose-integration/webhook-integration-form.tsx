import { Button } from "@gouvfr-lasuite/cunningham-react";
import { Icon, IconSize, IconType } from "@gouvfr-lasuite/ui-kit";
import { useTranslation } from "react-i18next";
import { useForm, FormProvider } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import * as z from "zod";
import { useState, useMemo } from "react";
import { useQueryClient } from "@tanstack/react-query";
import {
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
import { useConfig } from "@/features/providers/config";
import { handle } from "@/features/utils/errors";

// Environments where the backend (DEBUG=True) accepts http:// webhook
// URLs; everywhere else https is required, so mirror that client-side.
const DEV_ENVIRONMENTS = ["development", "developmentminimal", "e2e"];

// A single flat trigger lifecycle event replaces the old (events, phase,
// blocking) triple — the event name itself says when it fires and whether
// it blocks, so only valid combinations are representable. Mirrors
// enums.WebhookTrigger.
type WebhookTrigger =
    | "message.inbound"
    | "message.delivering"
    | "message.delivered";

type WebhookChannelSettings = {
    url?: string;
    trigger?: WebhookTrigger;
    format?: "eml" | "jmap" | "jmap_metadata";
    auth_method?: "jwt" | "api_key";
};

type CreatedWebhookCredential = {
    label: string;
    value: string;
};

type WebhookIntegrationFormProps = {
    mailbox: Mailbox;
    channel?: Channel;
    onSuccess: (channel: Channel) => void;
    onClose: () => void;
};

const createFormSchema = (
    t: (key: string) => string,
    allowInsecureUrl: boolean,
) =>
    z.object({
        name: z.string().min(1, { error: t("Name is required.") }),
        url: z
            .string()
            .min(1, { error: t("URL is required.") })
            .regex(allowInsecureUrl ? /^https?:\/\//i : /^https:\/\//i, {
                error: allowInsecureUrl
                    ? t("URL must start with http:// or https://")
                    : t("URL must start with https://"),
            }),
        trigger: z.enum([
            "message.inbound",
            "message.delivering",
            "message.delivered",
        ]),
        format: z.enum(["eml", "jmap", "jmap_metadata"]),
        auth_method: z.enum(["jwt", "api_key"]),
    });

type FormFields = z.infer<ReturnType<typeof createFormSchema>>;

export const WebhookIntegrationForm = ({
    mailbox,
    channel,
    onSuccess,
    onClose,
}: WebhookIntegrationFormProps) => {
    const { t } = useTranslation();
    const config = useConfig();
    const queryClient = useQueryClient();
    const [error, setError] = useState<string | null>(null);
    const settings = channel?.settings as WebhookChannelSettings | undefined;
    const isEditing = !!channel;
    const allowInsecureUrl = DEV_ENVIRONMENTS.includes(config.ENVIRONMENT);

    const createMutation = useMailboxesChannelsCreate();
    const updateMutation = useMailboxesChannelsPartialUpdate();
    const regenerateMutation = useMailboxesChannelsRegenerateSecretCreate();

    const formSchema = useMemo(
        () => createFormSchema(t, allowInsecureUrl),
        [t, allowInsecureUrl],
    );

    const form = useForm<FormFields>({
        resolver: zodResolver(formSchema),
        defaultValues: {
            name: channel?.name || "",
            url: settings?.url || "",
            trigger: settings?.trigger || "message.delivered",
            format: settings?.format || "eml",
            auth_method: settings?.auth_method || "jwt",
        },
    });

    const [createdCredential, setCreatedCredential] =
        useState<CreatedWebhookCredential | null>(null);

    const {
        handleSubmit,
        formState: { errors },
    } = form;

    const invalidateChannels = async () => {
        await queryClient.invalidateQueries({
            queryKey: [getMailboxesChannelsListUrl(mailbox.id)],
            exact: false,
        });
    };

    // Surface a freshly minted credential exactly once — the receiver
    // needs it to verify every webhook we send. The backend returns
    // ``secret`` for auth_method=jwt and ``api_key`` for api_key. Returns
    // true when a credential was shown (so callers know not to close yet).
    const showCredential = (
        source: { secret?: string; api_key?: string },
    ): boolean => {
        if (source.secret) {
            setCreatedCredential({
                label: t("Webhook signing secret"),
                value: source.secret,
            });
            return true;
        }
        if (source.api_key) {
            setCreatedCredential({
                label: t("Webhook API key"),
                value: source.api_key,
            });
            return true;
        }
        return false;
    };

    const onRegenerate = async () => {
        if (!channel) return;
        setError(null);
        try {
            const response = await regenerateMutation.mutateAsync({
                mailboxId: mailbox.id,
                id: channel.id,
            });
            const data = response.data as RegeneratedSecretResponse;
            showCredential(data);
        } catch (err) {
            handle(err);
            setError(t("An error occurred while saving the integration."));
        }
    };

    const onSubmit = async (data: FormFields) => {
        setError(null);

        const newSettings: WebhookChannelSettings = {
            url: data.url,
            trigger: data.trigger,
            format: data.format,
            auth_method: data.auth_method,
        };

        try {
            if (isEditing && channel) {
                await updateMutation.mutateAsync({
                    mailboxId: mailbox.id,
                    id: channel.id,
                    data: {
                        name: data.name,
                        settings: newSettings,
                    },
                });
                addToast(
                    <ToasterItem type="info">
                        <span>{t("Integration updated!")}</span>
                    </ToasterItem>,
                );
                await invalidateChannels();
            } else {
                const newChannel = await createMutation.mutateAsync({
                    mailboxId: mailbox.id,
                    data: {
                        name: data.name,
                        type: "webhook",
                        settings: newSettings,
                    },
                });
                addToast(
                    <ToasterItem type="info">
                        <span>{t("Integration created!")}</span>
                    </ToasterItem>,
                );
                await invalidateChannels();
                if (newChannel.status === 201) {
                    // The one-time credentials are create-only response
                    // fields surfaced via the generated
                    // ``ChannelCreateResponse`` view (the create hook types
                    // ``data`` as the plain ``Channel``, which omits them).
                    const payload =
                        newChannel.data as unknown as ChannelCreateResponse;
                    if (!showCredential(payload)) {
                        onSuccess(newChannel.data);
                    }
                }
            }
        } catch (err) {
            handle(err);
            setError(t("An error occurred while saving the integration."));
        }
    };

    // Shown after create OR after regenerating in edit mode — the new
    // credential is only ever returned once.
    if (createdCredential) {
        return (
            <div className="webhook-integration-form">
                <div className="webhook-integration-form__section">
                    <h3>{t("Save this credential now")}</h3>
                    <Banner type="warning">
                        {t(
                            "This value is shown only once. Configure your receiver with it before closing — you can rotate it later if you need a new one.",
                        )}
                    </Banner>
                    <label
                        className="webhook-integration-form__credential-label"
                        htmlFor="webhook-credential-value"
                    >
                        {createdCredential.label}
                    </label>
                    <CopyableInput
                        id="webhook-credential-value"
                        value={createdCredential.value}
                        aria-label={createdCredential.label}
                    />
                </div>
                <div className="webhook-integration-form__actions">
                    <Button type="button" onClick={onClose}>
                        {t("Done")}
                    </Button>
                </div>
            </div>
        );
    }

    return (
        <FormProvider {...form}>
            <form
                onSubmit={handleSubmit(onSubmit)}
                className="webhook-integration-form"
            >
                <div className="webhook-integration-form__section">
                    <h3>{t("General")}</h3>
                    <RhfInput
                        label={t("Name")}
                        name="name"
                        text={
                            errors.name?.message ||
                            t(
                                "This name is for internal use only and will not be visible to users.",
                            )
                        }
                        state={errors.name ? "error" : "default"}
                        fullWidth
                    />
                </div>

                <div className="webhook-integration-form__section">
                    <h3>{t("Endpoint")}</h3>
                    <RhfInput
                        label={t("URL")}
                        name="url"
                        text={
                            errors.url?.message ||
                            t(
                                "Data will be POSTed to this URL in the format selected below.",
                            )
                        }
                        state={errors.url ? "error" : "default"}
                        fullWidth
                    />
                    {/* One flat trigger: a lifecycle event whose name says
                        both when it fires and whether it blocks delivery. */}
                    <RhfSelect
                        label={t("Trigger")}
                        name="trigger"
                        options={[
                            {
                                label: t(
                                    "Message inbound — blocking, before the spam check; can shape the message before it is scanned",
                                ),
                                value: "message.inbound",
                            },
                            {
                                label: t(
                                    "Message delivering — blocking, after the spam check; can shape the message and sees the verdict",
                                ),
                                value: "message.delivering",
                            },
                            {
                                label: t(
                                    "Message delivered (recommended) — fire after delivery, response ignored",
                                ),
                                value: "message.delivered",
                            },
                        ]}
                        text={t(
                            "Which point in the message's lifecycle fires this webhook, and whether it can influence delivery.",
                        )}
                        fullWidth
                    />
                    <RhfSelect
                        label={t("Payload format")}
                        name="format"
                        options={[
                            {
                                label: t("Raw .eml (message/rfc822)"),
                                value: "eml",
                            },
                            {
                                label: t("JMAP Email (full message, RFC 8621)"),
                                value: "jmap",
                            },
                            {
                                label: t("JMAP Email (metadata only, no body)"),
                                value: "jmap_metadata",
                            },
                        ]}
                        text={t("What we post in the request body.")}
                        fullWidth
                    />
                </div>

                <div className="webhook-integration-form__section">
                    <h3>{t("Authentication")}</h3>
                    <RhfSelect
                        label={t("Method")}
                        name="auth_method"
                        options={[
                            {
                                label: t(
                                    "Signed (HMAC + JWT) — recommended for receivers that can verify a signature",
                                ),
                                value: "jwt",
                            },
                            {
                                label: t(
                                    "API key in header — for receivers that can only check a static header value",
                                ),
                                value: "api_key",
                            },
                        ]}
                        text={t(
                            "How the receiver authenticates our requests. The credential is shown once at creation.",
                        )}
                        fullWidth
                    />
                    {isEditing && (
                        <>
                            <Banner type="info">
                                {t(
                                    "Regenerating the credential invalidates the old one immediately. The receiver must be updated with the new value before it can verify webhooks again.",
                                )}
                            </Banner>
                            <Button
                                type="button"
                                variant="secondary"
                                onClick={onRegenerate}
                                disabled={regenerateMutation.isPending}
                                className="webhook-integration-form__regenerate"
                            >
                                {t("Regenerate credential")}
                            </Button>
                        </>
                    )}
                </div>

                <p className="webhook-integration-form__doc-link">
                    <a
                        href="https://github.com/suitenumerique/messages/blob/main/docs/webhooks.md"
                        target="_blank"
                        rel="noopener noreferrer"
                    >
                        {t(
                            "Read the webhook documentation for all technical details",
                        )}
                        <Icon
                            name="open_in_new"
                            type={IconType.OUTLINED}
                            size={IconSize.SMALL}
                        />
                    </a>
                </p>

                {error && <Banner type="error">{error}</Banner>}

                <div className="webhook-integration-form__actions">
                    <Button type="button" variant="secondary" onClick={onClose}>
                        {t("Cancel")}
                    </Button>
                    <Button
                        type="submit"
                        disabled={
                            createMutation.isPending || updateMutation.isPending
                        }
                    >
                        {isEditing
                            ? t("Save changes")
                            : t("Create integration")}
                    </Button>
                </div>
            </form>
        </FormProvider>
    );
};
