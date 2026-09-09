import { useQueryClient } from "@tanstack/react-query";
import { getMessagesListQueryOptions, Message, Thread } from "@/features/api/gen";
import { errorToString } from "@/features/api/api-error";
import { addToast, ToasterItem } from "@/features/ui/components/toaster";
import { useComposeWindows } from "@/features/providers/compose-windows";
import { useMailboxContext } from "@/features/providers/mailbox";

/**
 * Single entry point to resume a draft: drafts are never edited inline, they
 * always reopen in a compose window (bottom-right dock, sheet on mobile).
 * Used by the draft placeholder in the thread view and by the thread list
 * for draft-only threads.
 */
export const useOpenDraftInWindow = () => {
    const queryClient = useQueryClient();
    const { selectedMailbox } = useMailboxContext();
    const { openComposeWindow } = useComposeWindows();

    const openDraftInWindow = (draft: Message) => {
        if (!selectedMailbox) return null;
        return openComposeWindow({
            mode: draft.parent_id ? "reply" : "new",
            draftId: draft.id,
            mailboxId: selectedMailbox.id,
            threadId: draft.thread_id ?? undefined,
            parentMessageId: draft.parent_id ?? undefined,
        });
    };

    /**
     * The thread list only knows `has_draft`, not the draft id: resolve the
     * thread messages first (same cache key as the thread view, so this
     * pre-warms it) then open the draft, without navigating.
     */
    const openThreadDraftInWindow = async (thread: Thread) => {
        if (!selectedMailbox) return null;
        try {
            const response = await queryClient.fetchQuery(
                getMessagesListQueryOptions({
                    query: { queryKey: ["messages", thread.id] },
                    request: { params: { thread_id: thread.id, mailbox_id: selectedMailbox.id } },
                }),
            );
            const draft = response.data.find((message) => message.is_draft);
            if (!draft) return null;
            return openDraftInWindow(draft);
        } catch (error) {
            addToast(
                <ToasterItem type="error">
                    <span>{errorToString(error)}</span>
                </ToasterItem>
            );
            return null;
        }
    };

    return { openDraftInWindow, openThreadDraftInWindow };
};

export default useOpenDraftInWindow;
