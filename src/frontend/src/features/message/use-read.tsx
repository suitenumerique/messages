import { useMailboxContext } from "../providers/mailbox";
import useFlag from "./use-flag";

type MarkAsReadAtOptions = {
    threadIds: string[];
    readAt: string | null;
    onSuccess?: () => void;
}

/**
 * Compute `is_unread` for a single message given a read pointer. Mirrors the
 * backend invariant `is_unread = created_at > read_at`, with `read_at === null`
 * meaning "everything unread".
 */
const deriveMessageIsUnread = (createdAt: string, readAt: string | null): boolean => {
    if (readAt === null) return true;
    return new Date(createdAt) > new Date(readAt);
};

/**
 * Compute `has_unread` for a thread given the new read pointer of its access.
 * A thread is unread if any of its messages were sent strictly after the
 * read pointer; with `readAt === null` (mark all unread), it is always unread
 * unless the thread carries no message activity at all.
 */
const deriveThreadHasUnread = (messagedAt: string | null | undefined, readAt: string | null): boolean => {
    if (!messagedAt) return false;
    if (readAt === null) return true;
    return new Date(messagedAt) > new Date(readAt);
};

/**
 * Hook to mark threads as read up to a given timestamp.
 *
 * - readAt = ISO timestamp → messages created before that are read
 * - readAt = null → all messages are unread
 *
 * The flag API value is derived: readAt === null means unread (value=true).
 *
 * The cache patch + stats invalidation live on the `useFlag` mutation-level
 * callback — NOT on a per-call `onSuccess` — so they still fire when the
 * caller component (e.g. `ThreadActionBar`) unmounts before the mutation
 * settles. React Query drops per-call callbacks of unmounted hooks but keeps
 * mutation-level ones; routing both through `useFlag` options makes the
 * "mark as unread" flow survive the synchronous `unselectThread()` that
 * precedes it.
 */
const useRead = () => {
    const { selectedMailbox, pinThreads, patchMessages, invalidateThreadsStats } = useMailboxContext();
    const mailboxId = selectedMailbox?.id;

    const { mark, unmark, status } = useFlag('unread', {
        showToast: false,
        onSuccess: (data) => {
            const newReadAt = data.read_at ?? null;
            const affectedThreadIds = data.thread_ids ?? [];
            const targetMailboxId = data.mailbox_id;

            // Patch + pin every affected thread in the threads list cache.
            // Pinning keeps it visible even when the active filter (e.g.
            // "unread") would otherwise drop it on the next refetch.
            if (targetMailboxId) {
                pinThreads(affectedThreadIds, (thread) => ({
                    ...thread,
                    has_unread: deriveThreadHasUnread(thread.messaged_at, newReadAt),
                    accesses: thread.accesses.map((access) =>
                        access.mailbox.id === targetMailboxId
                            ? { ...access, read_at: newReadAt }
                            : access
                    ),
                }));
            }

            // Patch the per-thread messages cache so already-loaded thread
            // views pick up the new read state without waiting for a refetch.
            affectedThreadIds.forEach((threadId) => {
                patchMessages(threadId, (message) => ({
                    ...message,
                    is_unread: deriveMessageIsUnread(message.created_at!, newReadAt),
                }));
            });

            invalidateThreadsStats();
        },
    });

    const markAsReadAt = ({ threadIds, readAt, onSuccess }: MarkAsReadAtOptions) => {
        const isUnread = readAt === null;
        const flagFn = isUnread ? mark : unmark;
        // Caller-supplied `onSuccess` stays per-call: it is UX-only (e.g.
        // closing a modal) and acceptable to drop on unmount.
        flagFn({ threadIds, mailboxId, readAt, onSuccess: () => onSuccess?.() });
    };

    return {
        markAsReadAt,
        status,
    };
}

export default useRead;
