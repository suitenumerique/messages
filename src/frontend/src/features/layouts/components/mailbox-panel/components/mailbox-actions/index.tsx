import { useRouter } from "next/router";
import { useTranslation } from "react-i18next";
import { Button } from "@openfun/cunningham-react";
import { useMailboxContext } from "@/features/providers/mailbox";
import { useLayoutContext } from "../../../main";
import useAbility, { Abilities } from "@/hooks/use-ability";

export const MailboxPanelActions = () => {
    const { t } = useTranslation();
    const router = useRouter();
    const { selectedMailbox } = useMailboxContext();
    const { closeLeftPanel } = useLayoutContext();
    const canWriteMessages = useAbility(Abilities.CAN_WRITE_MESSAGES, selectedMailbox);

    const goToNewMessageForm = (event: React.MouseEvent<HTMLButtonElement | HTMLAnchorElement>) => {
        event.preventDefault();
        if (!canWriteMessages) return;
        closeLeftPanel();
        router.push(`/mailbox/${selectedMailbox!.id}/new`);
    }

    if (!selectedMailbox) return null;

    return (
        <div className="mailbox-panel-actions">
            <div>
            {
                <Button
                    onClick={goToNewMessageForm}
                    href={`/mailbox/${selectedMailbox.id}/new`}
                    icon={<span className="material-icons">edit_note</span>}
                    disabled={!canWriteMessages}
                >
                    {t("New message")}
                </Button>
            }
            </div>
            <div className="mailbox-panel-actions__extra">
            </div>
        </div>
    )
}

