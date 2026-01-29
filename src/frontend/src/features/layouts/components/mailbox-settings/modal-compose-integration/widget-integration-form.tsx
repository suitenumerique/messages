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
    getMailboxesChannelsListUrl,
} from "@/features/api/gen";
import { useMailboxContext } from "@/features/providers/mailbox";
import { RhfInput } from "@/features/forms/components/react-hook-form";
import { addToast, ToasterItem } from "@/features/ui/components/toaster";
import { Banner } from "@/features/ui/components/banner";
import { handle } from "@/features/utils/errors";
import { TagsSelector } from "./tags-selector";

type WidgetChannelSettings = {
    tags?: string[];
    subject_template?: string;
    config?: { enabled: boolean };
};

type WidgetIntegrationFormProps = {
    channel?: Channel;
    onSuccess: (channel: Channel) => void;
    onClose: () => void;
};

const createFormSchema = (t: (key: string) => string) => z.object({
    name: z.string().min(1, { message: t("Name is required.") }),
    subject_template: z.string().min(1, { message: t("Subject template is required.") }),
});

type FormFields = z.infer<ReturnType<typeof createFormSchema>>;

export const WidgetIntegrationForm = ({
    channel,
    onSuccess,
    onClose,
}: WidgetIntegrationFormProps) => {
    const { t } = useTranslation();
    const { selectedMailbox } = useMailboxContext();
    const queryClient = useQueryClient();
    const [error, setError] = useState<string | null>(null);
    const widgetSettings = (channel?.settings as WidgetChannelSettings | undefined);
    const [selectedTags, setSelectedTags] = useState<string[]>(
        widgetSettings?.tags || []
    );
    const isEditing = !!channel;

    const createMutation = useMailboxesChannelsCreate();
    const updateMutation = useMailboxesChannelsPartialUpdate();

    const formSchema = useMemo(() => createFormSchema(t), [t]);

    const form = useForm<FormFields>({
        resolver: zodResolver(formSchema),
        defaultValues: {
            name: channel?.name || "",
            subject_template: widgetSettings?.subject_template || t("Message from {referer_domain}"),
        },
    });

    const { handleSubmit, formState: { errors } } = form;

    const invalidateChannels = async () => {
        await queryClient.invalidateQueries({
            queryKey: [getMailboxesChannelsListUrl(selectedMailbox!.id)],
            exact: false
        });
    };

    const onSubmit = async (data: FormFields) => {
        setError(null);

        const settings = {
            subject_template: data.subject_template,
            tags: selectedTags,
            config: { enabled: true },
        };

        try {
            if (isEditing && channel) {
                // For updates, only send name and settings (not type)
                await updateMutation.mutateAsync({
                    mailboxId: selectedMailbox!.id,
                    id: channel.id,
                    data: {
                        name: data.name,
                        settings,
                    },
                });
                addToast(
                    <ToasterItem type="info">
                        <span>{t("Integration updated!")}</span>
                    </ToasterItem>
                );
                await invalidateChannels();
            } else {
                // For creation, include type
                const newChannel = await createMutation.mutateAsync({
                    mailboxId: selectedMailbox!.id,
                    data: {
                        name: data.name,
                        type: "widget",
                        settings,
                    },
                });
                addToast(
                    <ToasterItem type="info">
                        <span>{t("Integration created!")}</span>
                    </ToasterItem>
                );
                await invalidateChannels();
                if (newChannel.status === 201) {
                    onSuccess(newChannel.data);
                }
            }
        } catch (err) {
            handle(err);
            setError(t("An error occurred while saving the integration."));
        }
    };

    const widgetSnippet = channel ? `<script src="${process.env.NEXT_PUBLIC_FEEDBACK_WIDGET_PATH}loader.js" async></script>
<script>
window._lasuite_widget = window._lasuite_widget || [];
_lasuite_widget.push(["loader", "init", {
  "params": {
    "api": "${process.env.NEXT_PUBLIC_FEEDBACK_WIDGET_API_URL}",
    "channel": "${channel.id}"
  },
  "script": "${process.env.NEXT_PUBLIC_FEEDBACK_WIDGET_PATH}feedback.js",
  "widget": "feedback",
}]);
</script>` : "";

    return (
        <FormProvider {...form}>
        <form onSubmit={handleSubmit(onSubmit)} className="widget-integration-form">
            <div className="widget-integration-form__section">
                <h3>{t("General")}</h3>
                <RhfInput
                    label={t("Name")}
                    name="name"
                    text={errors.name?.message || t("This name is for internal use only and will not be visible to users.")}
                    state={errors.name ? "error" : "default"}
                    fullWidth
                />
            </div>

            <div className="widget-integration-form__section">
                <h3>{t("Settings")}</h3>
                <RhfInput
                    label={t("Subject template")}
                    name="subject_template"
                    text={errors.subject_template?.message || t("Use {referer_domain} to include the website domain in the subject.")}
                    state={errors.subject_template ? "error" : "default"}
                    fullWidth
                />
                <TagsSelector
                    selectedTags={selectedTags}
                    onTagsChange={setSelectedTags}
                />
            </div>

            {!isEditing && (
                <div className="widget-integration-form__section widget-integration-form__section--info">
                    <Icon name="info" type={IconType.OUTLINED} />
                    <p>
                        {t("After creating the widget, you will receive the installation code to add to your website.")}
                    </p>
                </div>
            )}

            {error && (
                <Banner type="error">{error}</Banner>
            )}

            <div className="widget-integration-form__actions">
                <Button type="button" variant="secondary" onClick={onClose}>
                    {t("Cancel")}
                </Button>
                <Button
                    type="submit"
                    disabled={createMutation.isPending || updateMutation.isPending}
                >
                    {isEditing ? t("Save changes") : t("Create integration")}
                </Button>
            </div>

            {isEditing && channel && (
                <div className="widget-integration-form__section">
                    <h3>{t("Installation")}</h3>
                    <p className="widget-integration-form__section-description">
                        {t("Add this code snippet to your website to display the feedback widget.")}
                    </p>
                    <div className="widget-integration-form__snippet">
                        <pre><code>{widgetSnippet}</code></pre>
                        <Button
                            type="button"
                            variant="tertiary"
                            size="small"
                            icon={<Icon name="content_copy" type={IconType.OUTLINED} />}
                            onClick={async () => {
                                try {
                                    await navigator.clipboard.writeText(widgetSnippet);
                                    addToast(
                                        <ToasterItem type="info">
                                            <span>{t("Copied to clipboard")}</span>
                                        </ToasterItem>
                                    );
                                } catch {
                                    addToast(
                                        <ToasterItem type="error">
                                            <span>{t("Unable to copy to clipboard.")}</span>
                                        </ToasterItem>
                                    );
                                }
                            }}
                        >
                            {t("Copy")}
                        </Button>
                    </div>
                    <p className="widget-integration-form__doc-link">
                        <a
                            href="https://integration.lasuite.numerique.gouv.fr/guides/feedback/"
                            target="_blank"
                            rel="noopener noreferrer"
                        >
                            {t("View full documentation")}
                            <Icon name="open_in_new" type={IconType.OUTLINED} size={IconSize.SMALL} />
                        </a>
                    </p>
                </div>
            )}
        </form>
        </FormProvider>
    );
};
