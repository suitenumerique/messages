import { useNavigate } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";
import { Button } from "@gouvfr-lasuite/cunningham-react";
import { useMailboxContext } from "@/features/providers/mailbox";
import { useLayoutContext } from "@/features/layouts/components/layout-context";
import useAbility, { Abilities } from "@/hooks/use-ability";
import { Icon, IconType } from "@gouvfr-lasuite/ui-kit";
import { useEffect, useRef, useState } from "react";
import { TransientTooltip } from "@/features/ui/components/transient-tooltip";
import clsx from "clsx";

const MIN_ANIMATION_MS = 700;

export const MailboxPanelActions = () => {
    const { t } = useTranslation();
    const navigate = useNavigate();
    const { selectedMailbox, refetchMailboxes } = useMailboxContext();
    const { closeLeftPanel } = useLayoutContext();
    const canWriteMessages = useAbility(Abilities.CAN_WRITE_MESSAGES, selectedMailbox);
    const [isRefreshing, setIsRefreshing] = useState(false);
    const [feedback, setFeedback] = useState<string | null>(null);
    // Snapshot of count_unread_threads at click-time. Read by the effect below
    // once isRefreshing flips back to false AND React has committed the new
    // mailbox data — at that point we know the delta.
    const baselineUnreadCountRef = useRef<number | null>(null);

    useEffect(() => {
        if (isRefreshing || baselineUnreadCountRef.current === null) return;
        const currentCount = selectedMailbox?.count_unread_threads ?? 0;
        const delta = currentCount - baselineUnreadCountRef.current;
        baselineUnreadCountRef.current = null;
        setFeedback(
            delta > 0
                ? t("{{count}} new message", { count: delta })
                : t("Up to date"),
        );
    }, [isRefreshing, selectedMailbox?.count_unread_threads, t]);

    const handleRefresh = async () => {
        if (isRefreshing) return;
        baselineUnreadCountRef.current = selectedMailbox?.count_unread_threads ?? 0;
        setFeedback(null);
        setIsRefreshing(true);
        try {
            await Promise.all([
                refetchMailboxes(),
                new Promise<void>((resolve) => window.setTimeout(resolve, MIN_ANIMATION_MS)),
            ]);
        } finally {
            setIsRefreshing(false);
        }
    };

    const goToNewMessageForm = (event: React.MouseEvent<HTMLButtonElement | HTMLAnchorElement>) => {
        event.preventDefault();
        if (!canWriteMessages) return;
        closeLeftPanel();
        navigate({ to: '/mailbox/$mailboxId/new', params: { mailboxId: selectedMailbox!.id } });
    };

    if (!selectedMailbox) return null;

    return (
        <div className="mailbox-panel-actions">
            <div>
                <Button
                    onClick={goToNewMessageForm}
                    href={`/mailbox/${selectedMailbox.id}/new`}
                    icon={<Icon name="edit_note" type={IconType.OUTLINED} aria-hidden="true" />}
                    disabled={!canWriteMessages}
                >
                    {t("New message")}
                </Button>
            </div>
            <div className="mailbox-panel-actions__extra">
                <TransientTooltip
                    message={feedback}
                    onHide={() => setFeedback(null)}
                    placement="bottom"
                >
                    <Button
                        icon={
                            <Icon
                                name="autorenew"
                                className={clsx(
                                    "mailbox-panel-actions__refresh-icon",
                                    { "mailbox-panel-actions__refresh-icon--spinning": isRefreshing }
                                )}
                                aria-hidden="true"
                            />
                        }
                        variant="tertiary"
                        aria-label={isRefreshing ? t("Loading…") : t("Refresh")}
                        onClick={handleRefresh}
                        disabled={isRefreshing}
                    />
                </TransientTooltip>
            </div>
        </div>
    );
};
