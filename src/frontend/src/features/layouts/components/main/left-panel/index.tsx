import { useResponsive } from "@gouvfr-lasuite/ui-kit";
import { MailboxPanel } from "../../mailbox-panel";
import { useAuth } from "@/features/auth";
import { HeaderRight } from "../header/authenticated";

export const LeftPanel = ({ hasNoMailbox }: { hasNoMailbox: boolean }) => {
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
                    <HeaderRight />
                </div>
            }
        </div>
    )
}
