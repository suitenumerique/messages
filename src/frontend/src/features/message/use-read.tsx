import { DateHelper } from "@/features/utils/date-helper";
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
    return DateHelper.compareInstants(createdAt, readAt) > 0;
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
    return DateHelper.compareInstants(messagedAt, readAt) > 0;
};

/**
 * The read pointer for "mark as read now": the later of this device's clock
 * and the newest message of the threads. A clock running behind the server's
 * would otherwise leave the latest mail unread; one running ahead is clamped
 * by the server.
 */
export const readAtNow = (messagedAts: readonly (string | null | undefined)[]): string => {
    const now = Date.now();
    let latest: string | undefined;
    for (const value of messagedAts) {
        if (!value || Number.isNaN(new Date(value).getTime())) continue;
        if (!latest || DateHelper.compareInstants(value, latest) > 0) latest = value;
    }
    // The server's string, not a Date: it has microseconds, which a Date
    // would truncate to a pointer just before the message. A message in the
    // device's current millisecond is at or after it.
    return latest && new Date(latest).getTime() >= now ? latest : new Date(now).toISOString();
};

/**
 * The read pointer that leaves a message created at ``createdAt``, and all
 * later ones, unread: 1 ms before it. Null (all unread) for a message of the
 * first millisecond of year 1, which nothing can precede on the server.
 */
export const readAtBefore = (createdAt: string): string | null => {
    const before = new Date(new Date(createdAt).getTime() - 1);
    return before.getUTCFullYear() < 1 ? null : before.toISOString();
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
 * callback so they run for every caller, before its own `onSuccess`.
 */
const useRead = () => {
    const { selectedMailbox, pinThreads, patchMessages, invalidateThreadsStats, invalidateLabelsStats } = useMailboxContext();
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
            invalidateLabelsStats();
        },
    });

    const markAsReadAt = ({ threadIds, readAt, onSuccess }: MarkAsReadAtOptions) => {
        const isUnread = readAt === null;
        const flagFn = isUnread ? mark : unmark;
        flagFn({ threadIds, mailboxId, readAt, onSuccess: () => onSuccess?.() });
    };

    return {
        markAsReadAt,
        status,
    };
}

export default useRead;
