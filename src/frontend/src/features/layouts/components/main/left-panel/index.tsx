import { useResponsive } from "@gouvfr-lasuite/ui-kit";
import { MailboxPanel } from "../../mailbox-panel";
import { useAuth } from "@/features/auth";
import { HeaderRight } from "../header/authenticated";
import { PostHogSurveyButton } from "@/features/ui/components/feedback-button";
import { useConfig } from "@/features/providers/config";
import { usePostHog } from "posthog-js/react";

export const LeftPanel = ({ hasNoMailbox = true }: { hasNoMailbox?: boolean }) => {
    const { user } = useAuth();
    const posthog = usePostHog();
    const config = useConfig();
    const { isTablet } = useResponsive();

    if (!isTablet && hasNoMailbox) return null;

    return (
        <div className="left-panel">
            <div className="left-panel__content">
                {user && !hasNoMailbox && <MailboxPanel />}
            </div>
            {isTablet &&
                <div className="left-panel__footer">
                    <HeaderRight />
                </div>
            }
            {posthog.__loaded && config.POSTHOG_SURVEY_ID && (
                <div className="left-panel__footer">
                    <PostHogSurveyButton fullWidth />
                </div>
            )}
        </div>
    )
}
