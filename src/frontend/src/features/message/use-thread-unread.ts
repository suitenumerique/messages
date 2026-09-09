import { useMemo } from "react";
import { useParams } from "@tanstack/react-router";

import { Thread } from "@/features/api/gen/models";

/**
 * Whether a thread is unread *for the mailbox being viewed*.
 *
 * Read state is per-access, so `thread.has_unread` — computed backend-side for
 * the requesting mailbox — is not enough on its own once the list cache has
 * been patched optimistically: `useRead` writes the new pointer on the matching
 * access, and this recomputation is what makes the row update immediately.
 */
export const useThreadUnread = (thread: Thread): boolean => {
    const params = useParams({ strict: false }) as { mailboxId?: string };
    const mailboxId = params?.mailboxId;

    return useMemo(() => {
        const access = thread.accesses.find((a) => a.mailbox.id === mailboxId);
        const compareDate = thread.messaged_at;
        if (!access || !compareDate) return false;
        if (!access.read_at) return true;
        return new Date(compareDate) > new Date(access.read_at);
    }, [thread, mailboxId]);
};

export default useThreadUnread;
