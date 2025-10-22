import { useFlagCreate } from "@/features/api/gen"
import { Thread, Message } from "@/features/api/gen/models"
import { useMailboxContext } from "../providers/mailbox";

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
    const { invalidateThreadMessages, invalidateThreadsStats } = useMailboxContext();

    const { mutate, status } = useFlagCreate({
        mutation: {
            onSuccess: (_, variables) => {
                invalidateThreadMessages({
                    type: 'update',
                    metadata: { ids: variables.data.message_ids ?? [], threadIds: variables.data.thread_ids ?? [] },
                    payload: { is_unread: variables.data.value, read_at: new Date().toISOString() }
                });
                invalidateThreadsStats();
            },
        }
    });

    const markAs =
        (status: MarkAsStatus) =>
            ({ threadIds = [], messageIds = [], onSuccess }: MarkAsOptions) => {
                mutate({
                    data: {
                        flag: 'unread',
                        value: status === 'unread',
                        thread_ids: threadIds,
                        message_ids: messageIds,
                    },
                }, {
                    onSuccess,
                });
            }
    return {
        markAsRead: markAs('read'),
        markAsUnread: markAs('unread'),
        status
    };
};

export default useRead;
