import { useResponsive } from "@gouvfr-lasuite/ui-kit";
import { useAuth } from "@/features/auth";
import { HeaderRight } from "../header/authenticated";
import { MailboxPanel } from "../../mailbox-panel";
import { LanguagePicker } from "../language-picker";
import { LagaufreButton } from "@/features/ui/components/lagaufre";

export const LeftPanel = ({ hasNoMailbox = true }: { hasNoMailbox?: boolean }) => {
    const { user } = useAuth();
    const { isTablet } = useResponsive();

    if (!isTablet && hasNoMailbox) return null;

    return (
        <div className="left-panel">
            <div className="left-panel__content">
                {user && !hasNoMailbox && <MailboxPanel />}
            </div>
            {isTablet &&
                <div className="left-panel__footer">
                    {user ? <HeaderRight /> : <>
                        <LanguagePicker />
                        <LagaufreButton />
                    </>}
                </div>
            }
        </div>
    )
}
