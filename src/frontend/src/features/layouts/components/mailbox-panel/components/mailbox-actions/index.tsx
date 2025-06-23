import { Button } from "@openfun/cunningham-react";
import { useRouter } from "next/router";
import { useTranslation } from "react-i18next";
import { useLayoutContext } from "../../../main";
import { useMailboxContext } from "@/features/providers/mailbox";

export const MailboxPanelActions = () => {
    const { t } = useTranslation();
    const router = useRouter();
    const { selectedMailbox } = useMailboxContext();
    const { closeLeftPanel } = useLayoutContext();

    const goToNewMessageForm = (event: React.MouseEvent<HTMLButtonElement | HTMLAnchorElement>) => {
        event.preventDefault();
        closeLeftPanel();
        router.push(`/mailbox/${selectedMailbox!.id}/new`);
    }

    if (!selectedMailbox) return null;

    return (
        <div className="mailbox-panel-actions">
            <Button
                onClick={goToNewMessageForm}
                href={`/mailbox/${selectedMailbox.id}/new`}
                icon={<span className="material-icons">edit_note</span>}
            >
                {t("actions.new_message")}
            </Button>
        </div>
    )
}

