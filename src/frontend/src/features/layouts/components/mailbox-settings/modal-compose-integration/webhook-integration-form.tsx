import { Button } from "@gouvfr-lasuite/cunningham-react";
import { Icon, IconType } from "@gouvfr-lasuite/ui-kit";
import { useTranslation } from "react-i18next";
import { useForm, FormProvider } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import * as z from "zod";
import { useState, useMemo } from "react";
import { useQueryClient } from "@tanstack/react-query";
import {
    Channel,
    Mailbox,
    useMailboxesChannelsCreate,
    useMailboxesChannelsPartialUpdate,
    getMailboxesChannelsListUrl,
} from "@/features/api/gen";
import {
    RhfInput,
    RhfSelect,
    RhfCheckbox,
} from "@/features/forms/components/react-hook-form";
import { addToast, ToasterItem } from "@/features/ui/components/toaster";
import { Banner } from "@/features/ui/components/banner";
import { CopyableInput } from "@/features/ui/components/copyable-input";
import { handle } from "@/features/utils/errors";

type WebhookChannelSettings = {
    url?: string;
    events?: string[];
    phase?: "before_spam" | "after_spam";
    format?: "eml" | "jmap" | "jmap_metadata";
    blocking?: boolean;
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

const createFormSchema = (t: (key: string) => string) =>
    z.object({
        name: z.string().min(1, { error: t("Name is required.") }),
        url: z
            .string()
            .min(1, { error: t("URL is required.") })
            .regex(/^https?:\/\//i, {
                error: t("URL must start with http:// or https://"),
            }),
        phase: z.enum(["before_spam", "after_spam"]),
        format: z.enum(["eml", "jmap", "jmap_metadata"]),
        blocking: z.boolean(),
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
    const queryClient = useQueryClient();
    const [error, setError] = useState<string | null>(null);
    const settings = channel?.settings as WebhookChannelSettings | undefined;
    const isEditing = !!channel;

    const createMutation = useMailboxesChannelsCreate();
    const updateMutation = useMailboxesChannelsPartialUpdate();

    const formSchema = useMemo(() => createFormSchema(t), [t]);

    const form = useForm<FormFields>({
        resolver: zodResolver(formSchema),
        defaultValues: {
            name: channel?.name || "",
            url: settings?.url || "",
            phase: settings?.phase || "after_spam",
            format: settings?.format || "eml",
            blocking: settings?.blocking ?? false,
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

    const onSubmit = async (data: FormFields) => {
        setError(null);

        const newSettings: WebhookChannelSettings = {
            url: data.url,
            events: ["message.inbound"],
            phase: data.phase,
            format: data.format,
            blocking: data.blocking,
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
                    // Surface the freshly minted credential exactly
                    // once — the receiver needs this value to verify
                    // every webhook we send. The backend returns
                    // ``secret`` for auth_method=jwt and ``api_key`` for
                    // auth_method=api_key. These one-time credentials are
                    // create-only response fields, not part of the
                    // generated ``Channel`` type — read them off an
                    // index-signature view.
                    const payload = newChannel.data as unknown as Record<
                        string,
                        unknown
                    >;
                    const secret = payload.secret as string | undefined;
                    const apiKey = payload.api_key as string | undefined;
                    if (secret) {
                        setCreatedCredential({
                            label: t("Webhook signing secret"),
                            value: secret,
                        });
                    } else if (apiKey) {
                        setCreatedCredential({
                            label: t("Webhook API key"),
                            value: apiKey,
                        });
                    } else {
                        onSuccess(newChannel.data);
                    }
                }
            }
        } catch (err) {
            handle(err);
            setError(t("An error occurred while saving the integration."));
        }
    };

    if (createdCredential && !isEditing) {
        return (
            <div className="widget-integration-form">
                <div className="widget-integration-form__section">
                    <h3>{t("Save this credential now")}</h3>
                    <Banner type="warning">
                        {t(
                            "This value is shown only once. Configure your receiver with it before closing — you can rotate it later if you need a new one.",
                        )}
                    </Banner>
                    <label className="widget-integration-form__credential-label">
                        {createdCredential.label}
                    </label>
                    <CopyableInput
                        value={createdCredential.value}
                        aria-label={createdCredential.label}
                    />
                </div>
                <div className="widget-integration-form__actions">
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
                className="widget-integration-form"
            >
                <div className="widget-integration-form__section">
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

                <div className="widget-integration-form__section">
                    <h3>{t("Endpoint")}</h3>
                    <RhfInput
                        label={t("URL")}
                        name="url"
                        text={
                            errors.url?.message ||
                            t(
                                "Each incoming message will be POSTed to this URL in the format selected below.",
                            )
                        }
                        state={errors.url ? "error" : "default"}
                        fullWidth
                    />
                    <RhfSelect
                        label={t("Authentication")}
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
                            "How the receiver authenticates our requests. The secret is shown once at creation.",
                        )}
                        fullWidth
                    />
                </div>

                <div className="widget-integration-form__section">
                    <h3>{t("Behavior")}</h3>
                    <RhfSelect
                        label={t("When to fire")}
                        name="phase"
                        options={[
                            {
                                label: t("After spam check (recommended)"),
                                value: "after_spam",
                            },
                            {
                                label: t("Before spam check"),
                                value: "before_spam",
                            },
                        ]}
                        text={t(
                            "Whether the webhook fires before or after the message is checked for spam.",
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
                                label: t("JMAP Email JSON (RFC 8621)"),
                                value: "jmap",
                            },
                            {
                                label: t("JMAP Email metadata (notification only)"),
                                value: "jmap_metadata",
                            },
                        ]}
                        text={t(
                            "Body posted to the endpoint. Envelope metadata is always sent as X-StMsg-* headers.",
                        )}
                        fullWidth
                    />
                    <RhfCheckbox
                        label={t(
                            "Blocking — let this endpoint shape what happens to the message",
                        )}
                        name="blocking"
                    />
                    <Banner type="info">
                        <p>
                            <strong>
                                {t(
                                    "Non-blocking (default) is the safe choice:",
                                )}
                            </strong>{" "}
                            {t(
                                "we POST and ignore the response. Receivers cannot affect the message.",
                            )}
                        </p>
                        <p>
                            <strong>
                                {t(
                                    "Blocking lets the receiver act on this single message",
                                )}
                            </strong>{" "}
                            {t(
                                "by returning a JSON body. Available actions (all scoped to the message being received): drop / retry the delivery, override the spam verdict, attach labels, assign users by email, mark starred / read / trashed / archived, suppress the autoreply, add an internal comment to the thread, and create a draft reply from a template. Use blocking only with receivers you trust.",
                            )}
                        </p>
                    </Banner>
                </div>

                {error && <Banner type="error">{error}</Banner>}

                <div className="widget-integration-form__actions">
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

                {!isEditing && (
                    <div className="widget-integration-form__section widget-integration-form__section--info">
                        <Icon name="info" type={IconType.OUTLINED} />
                        <p>
                            {t(
                                "Your endpoint will receive a JSON payload containing the parsed message (from, to, subject, body, headers, …).",
                            )}
                        </p>
                    </div>
                )}
            </form>
        </FormProvider>
    );
};
