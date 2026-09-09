import { useTranslation } from "react-i18next";
import { IconType } from "@gouvfr-lasuite/ui-kit";
import { Edit } from "@gouvfr-lasuite/ui-kit/icons";
import { Icon } from "@/features/ui/components/icon";
import { Message } from "@/features/api/gen";
import { Banner } from "@/features/ui/components/banner";
import { useComposeWindows } from "@/features/providers/compose-windows";
import { ComposeWindowDescriptor } from "@/features/providers/compose-windows/types";
import { useOpenDraftInWindow } from "@/features/message/use-open-draft-in-window";

type ComposeDraftPlaceholderProps = {
    draft: Message;
    /** Set when the draft is currently open in a compose window. */
    detachedWindow?: ComposeWindowDescriptor;
};

/**
 * Marks the position of a draft inside the thread feed. Drafts are never
 * edited inline: clicking the card resumes the draft in a compose window,
 * and while that window is open the card points at it instead.
 */
export const ComposeDraftPlaceholder = ({ draft, detachedWindow }: ComposeDraftPlaceholderProps) => {
    const { t } = useTranslation();
    const { focusWindow } = useComposeWindows();
    const { openDraftInWindow } = useOpenDraftInWindow();

    if (detachedWindow) {
        return (
            <div className="compose-draft-placeholder compose-draft-placeholder--detached">
                <Banner
                    type="neutral"
                    icon={<Icon name="open_in_new" type={IconType.OUTLINED} />}
                    fullWidth
                    actions={[
                        {
                            label: t("Show window"),
                            onClick: () => focusWindow(detachedWindow.windowId),
                            variant: "secondary",
                        },
                    ]}
                >
                    <p>{t("You are editing this draft in a separate window.")}</p>
                </Banner>
            </div>
        );
    }

    return (
        <div className="compose-draft-placeholder compose-draft-placeholder--detached">
            <Banner
                type="neutral"
                icon={<Icon icon={Edit} />}
                fullWidth
                actions={[
                    {
                        label: t("Continue editing"),
                        onClick: () => openDraftInWindow(draft),
                        variant: "secondary",
                    },
                ]}
            >
                <p>
                    <strong>{t("Draft")}</strong>
                    {' - '}{draft.subject?.trim() || t("New message")}
                </p>
            </Banner>
        </div>
    )
};
