import { useRouter } from "next/router";
import { useTranslation } from "react-i18next";
import { Button } from "@gouvfr-lasuite/cunningham-react";
import { useMailboxContext } from "@/features/providers/mailbox";
import { useLayoutContext } from "../../../main";
import useAbility, { Abilities } from "@/hooks/use-ability";
import { Icon, IconType, Spinner } from "@gouvfr-lasuite/ui-kit";
import { useState } from "react";

export const MailboxPanelActions = () => {
    const { t } = useTranslation();
    const router = useRouter();
    const { selectedMailbox, refetchMailboxes } = useMailboxContext();
    const { closeLeftPanel } = useLayoutContext();
    const canWriteMessages = useAbility(Abilities.CAN_WRITE_MESSAGES, selectedMailbox);
    const [showSpinner, setShowSpinner] = useState(false);

    const handleRefresh = async () => {
        setShowSpinner(true);
        try {
            await refetchMailboxes();
        } finally {
            setShowSpinner(false);
        }
    };

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
                    icon={<Icon name="edit_note" type={IconType.OUTLINED} aria-hidden="true" />}
                    disabled={!canWriteMessages}
                >
                    {t("New message")}
                </Button>
            }
            </div>
            <div className="mailbox-panel-actions__extra">
                <Button
                    icon={
                        <span className="mailbox-panel-actions__refresh-wrapper">
                            <span className={`material-icons mailbox-panel-actions__refresh-icon${showSpinner ? ' mailbox-panel-actions__refresh-icon--hidden' : ''}`}>autorenew</span>
                            <span className={`mailbox-panel-actions__refresh-spinner${showSpinner ? ' mailbox-panel-actions__refresh-spinner--visible' : ''}`}>
                                <Spinner size="sm" />
                            </span>
                        </span>
                    }
                    variant="tertiary"
                    aria-label={showSpinner ? t('Loading…') : t('Refresh')}
                    onClick={handleRefresh}
                    disabled={showSpinner}
                />
            </div>
        </div>
    )
}

