import { useResponsive } from "@gouvfr-lasuite/ui-kit";
import { usePostHog } from "posthog-js/react";
import { useAuth } from "@/features/auth";
import { HeaderRight } from "../header/authenticated";
import { PostHogSurveyButton } from "@/features/ui/components/feedback-button";
import { useConfig } from "@/features/providers/config";
import { MailboxPanel } from "../../mailbox-panel";
import { LanguagePicker } from "../language-picker";

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
                    {user ? <HeaderRight /> : <LanguagePicker />}
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
