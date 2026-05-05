import { useMailboxContext } from "@/features/providers/mailbox";

/**
 * True when the current thread/mailbox pair is collaborative: shared
 * mailbox (Mailbox.is_shared — non-identity or identity shared via
 * delegation) or thread accessible from more than one mailbox. Gates
 * thread-scoped collaboration features (assignment CTAs, internal
 * messages) that have no purpose in a mono-user conversation.
 */
export const useIsSharedContext = (): boolean => {
    const { selectedMailbox, selectedThread } = useMailboxContext();
    return (
        selectedMailbox?.is_shared === true
        || (selectedThread?.accesses?.length ?? 0) > 1
    );
};
