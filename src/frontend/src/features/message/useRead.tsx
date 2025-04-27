import { useReadCreate } from "@/features/api/gen"
import { Thread, Message } from "@/features/api/gen/models"
import { useQueryClient } from "@tanstack/react-query";
import { useMailboxContext } from "../mailbox/provider";

type MarkAsStatus = 'read' | 'unread';

type MarkAsOptions = {
    threadIds?: Thread["id"][],
    messageIds?: Message['id'][],
    onSuccess?: () => void,
}

/**
 * Hook to mark messages or threads as read or unread
 */
const useRead = () => {
    const queryClient = useQueryClient();
    const { invalidateThreadMessages } = useMailboxContext();

    const { mutate, status } = useReadCreate({
        mutation: {
            onSuccess: () => {
                invalidateThreadMessages();
                queryClient.invalidateQueries({ queryKey: ["/api/v1.0/mailboxes/"] });
            },
        }
    });

    const markAs = 
        (status: MarkAsStatus) =>
        ({ threadIds = [], messageIds = [], onSuccess }: MarkAsOptions) =>
            mutate({
                data: {
                    status: status === 'read' ? 1 : 0,
                    thread_ids: threadIds,
                    message_ids: messageIds,
                },
            }, {
                onSuccess,
            });

    return { 
        markAsRead: markAs('read'),
        markAsUnread: markAs('unread'),
        status
    };
};

export default useRead;