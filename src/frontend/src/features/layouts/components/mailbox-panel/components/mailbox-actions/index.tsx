import { Button } from "@openfun/cunningham-react";
import { useRouter } from "next/router";
import { useTranslation } from "react-i18next";
import { useLayoutContext } from "../../../main";

export const MailboxPanelActions = () => {
    const { t } = useTranslation();
    const router = useRouter();
    const { closeLeftPanel } = useLayoutContext();

    const goToNewMessageForm = (event: React.MouseEvent<HTMLButtonElement | HTMLAnchorElement>) => {
        event.preventDefault();
        closeLeftPanel();
        router.push(`/mailbox/new`);
    }

    return (
        <div className="mailbox-panel-actions">
            <Button
                onClick={goToNewMessageForm}
                href="/mailbox/new"
                icon={<span className="material-icons">edit_note</span>}
            >
                {t("actions.new_message")}
            </Button>
        </div>
    )
}

