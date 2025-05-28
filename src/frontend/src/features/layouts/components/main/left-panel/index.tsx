import { useResponsive } from "@gouvfr-lasuite/ui-kit";
import { MailboxPanel } from "../../mailbox-panel";
import { useAuth } from "@/features/auth";
import { HeaderRight } from "../header/authenticated";

export const LeftPanel = () => {
    const { user } = useAuth();
    const { isTablet } = useResponsive();

    return (
        <div className="left-panel">
            <div className="left-panel__content">
                {user && <MailboxPanel />}
            </div>
            {isTablet &&
                <div className="left-panel__footer">
                    <HeaderRight />
                </div>
            }
        </div>
    )
}
