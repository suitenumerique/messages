import { useQuery } from "@tanstack/react-query";
import { fetchAPI } from "@/features/api/fetch-api";
import { MessageSummary } from "./types";

type MessagesSummaryResponse = {
    data: MessageSummary[];
    status: 200;
};

/**
 * Fetches the lightweight per-message summaries for a thread (sender, date,
 * snippet, unread state) — used by the thread-list expand dropdown.
 * Hand-written rather than via the generated Orval client: ?summary=true
 * returns a different shape than the full Message type the generated
 * useMessagesList hook is typed for.
 *
 * mailboxId is required, not optional: MessageSummarySerializer.is_unread
 * reads an annotation the backend only applies when mailbox_id is present
 * on the request — omitting it would silently make every summary read as
 * "read" (is_unread always false) with no error.
 */
export const useMessageSummaries = (
    threadId: string,
    mailboxId: string,
    { enabled }: { enabled: boolean }
) => {
    return useQuery({
        queryKey: ["messages", "summary", threadId, mailboxId],
        queryFn: () =>
            fetchAPI<MessagesSummaryResponse>("/api/v1.0/messages/", {
                params: { thread_id: threadId, mailbox_id: mailboxId, summary: "true" },
            }),
        select: (response) => response.data,
        enabled,
    });
};
