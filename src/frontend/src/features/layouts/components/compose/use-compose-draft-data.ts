import { useState } from "react";
import { Mailbox, Message, Thread, useMessagesRetrieve, useThreadsRetrieve } from "@/features/api/gen";
import { APIError } from "@/features/api/api-error";
import { useMailboxContext } from "@/features/providers/mailbox";

type UseComposeDraftDataInput = {
    mailboxId: string;
    draftId?: string;
    parentMessageId?: string;
    threadId?: string;
    /** Transient snapshots provided at open time to skip the initial fetches. */
    initialDraft?: Message;
    initialParent?: Message;
};

type UseComposeDraftDataResult = {
    mailbox: Mailbox | undefined;
    draft: Message | undefined;
    parentMessage: Message | undefined;
    thread: Thread | null;
    isLoading: boolean;
    /** The draft was deleted or made inaccessible elsewhere (404). */
    isDraftNotFound: boolean;
};

/**
 * Resolves the data a compose surface detached from the thread view needs:
 * the draft, its parent message, the thread (required to compute edit
 * permissions on replies) and the sending mailbox. Shared by the floating
 * compose window and the standalone pop-out page.
 */
export const useComposeDraftData = ({
    mailboxId,
    draftId,
    parentMessageId,
    threadId,
    initialDraft,
    initialParent,
}: UseComposeDraftDataInput): UseComposeDraftDataResult => {
    const { mailboxes } = useMailboxContext();
    const mailbox = mailboxes?.find((entry) => entry.id === mailboxId);
    // Captured at mount on purpose: a draft materialized later by the form
    // itself must neither trigger a fetch nor flip the loading gate below —
    // that would unmount the form and lose its unsaved state.
    const [initialDraftId] = useState(draftId);

    const draftQuery = useMessagesRetrieve(initialDraftId ?? "", {
        query: {
            enabled: !!initialDraftId && !initialDraft,
            // A refetch must never reset the form: the draft prop is only the
            // initial state of the form, so keep the cache entry untouched.
            staleTime: Infinity,
            meta: { noGlobalError: true },
        },
    });
    const draft = initialDraft ?? draftQuery.data?.data;

    const effectiveParentId = parentMessageId ?? draft?.parent_id ?? undefined;
    const parentQuery = useMessagesRetrieve(effectiveParentId ?? "", {
        query: {
            enabled: !!effectiveParentId && !initialParent,
            staleTime: Infinity,
            meta: { noGlobalError: true },
        },
    });
    const parentMessage = initialParent ?? parentQuery.data?.data;

    // Replies need the thread to resolve edit permissions; a thread-less
    // "new" draft (no parent) does not.
    const effectiveThreadId = effectiveParentId ? (threadId ?? draft?.thread_id ?? undefined) : undefined;
    const threadQuery = useThreadsRetrieve(effectiveThreadId ?? "", {
        query: {
            enabled: !!effectiveThreadId,
            meta: { noGlobalError: true },
        },
        request: {
            params: {
                mailbox_id: mailboxId,
            },
        },
    });
    const thread = threadQuery.data?.data ?? null;

    const isDraftNotFound = draftQuery.error instanceof APIError && draftQuery.error.code === 404;

    const isLoading =
        (!!initialDraftId && !draft && !isDraftNotFound)
        || (!!effectiveParentId && !parentMessage && parentQuery.isLoading)
        || (!!effectiveThreadId && threadQuery.isLoading);

    return { mailbox, draft, parentMessage, thread, isLoading, isDraftNotFound };
};
