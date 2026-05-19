import { useCallback } from "react";
import { useTranslation } from "react-i18next";
import { useMailboxContext } from "../providers/mailbox";
import { addToast, ToasterItem } from "../ui/components/toaster";
import { handle } from "../utils/errors";

type CopyDeepLinkTarget = {
    messageId?: string;
    eventId?: string;
};

/**
 * Builds and copies a shareable deep-link URL for the currently selected
 * thread into the clipboard. When a `messageId` or `eventId` is provided,
 * the URL is anchored to the matching DOM element so the receiver lands
 * directly on that message or comment.
 *
 * Returns `true` when the clipboard write succeeded, `false` otherwise —
 * call-sites can use this to gate UI side-effects (e.g. closing a popover
 * only on success).
 */
const useCopyDeepLink = () => {
    const { t } = useTranslation();
    const { selectedMailbox, selectedThread } = useMailboxContext();

    return useCallback(async (target: CopyDeepLinkTarget = {}): Promise<boolean> => {
        if (!selectedMailbox || !selectedThread) return false;
        const base = `${window.location.origin}/mailbox/${selectedMailbox.id}/thread/${selectedThread.id}`;
        const anchor = target.messageId
            ? `#thread-message-${target.messageId}`
            : target.eventId
                ? `#thread-event-${target.eventId}`
                : '';
        try {
            await navigator.clipboard.writeText(`${base}${anchor}`);
            addToast(<ToasterItem><p>{t('Link copied to clipboard')}</p></ToasterItem>);
            return true;
        } catch (error) {
            const kind = target.messageId ? 'message' : target.eventId ? 'comment' : 'thread';
            handle(new Error(`Failed to copy ${kind} link.`), { extra: { error } });
            return false;
        }
    }, [selectedMailbox, selectedThread, t]);
};

export default useCopyDeepLink;
