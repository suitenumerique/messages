import { useResponsive } from "@gouvfr-lasuite/ui-components";
import { useAuth } from "@/features/auth";
import { ApplicationMenu, AutoreplyIndicator, HeaderUserMenu, ImportIndicator } from "../header/authenticated";
import { MailboxPanel } from "../../mailbox-panel";
import { LanguagePicker } from "../language-picker";
import { LagaufreButton } from "@/features/ui/components/lagaufre";
import { useMailboxContext } from "@/features/providers/mailbox";
import { QuotaWidget } from "@/features/quota/components/quota-widget";
import { SurveyButton } from "@/features/ui/components/feedback-button";
import { isNativePlatform } from "@/features/native/platform";

export const LeftPanel = ({ hasNoMailbox = true }: { hasNoMailbox?: boolean }) => {
    const { user } = useAuth();
    const { isTablet } = useResponsive();
    const { selectedMailbox } = useMailboxContext();

    if (!isTablet && hasNoMailbox) return null;

    return (
        <div className="left-panel">
            <div className="left-panel__content">
                {user && !hasNoMailbox && <MailboxPanel />}
            </div>
            {isTablet &&
                <div className="left-panel__footer">
                    {user ? <>
                        <HeaderUserMenu />
                        {!isNativePlatform() && <LagaufreButton />}
                        <ApplicationMenu />
                        <AutoreplyIndicator />
                        <ImportIndicator />
                        {/* Always rendered: it pushes the survey button to the end even when there is no gauge. */}
                        <div className="left-panel__footer-gauge">
                            <QuotaWidget mailboxId={selectedMailbox?.id} />
                        </div>
                        <SurveyButton iconOnly color="neutral" variant="tertiary" />
                    </> : <>
                        <LagaufreButton />
                        <LanguagePicker />
                    </>}
                </div>
            }
        </div>
    )
}
