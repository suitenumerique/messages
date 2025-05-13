import { useFlagCreate } from "@/features/api/gen"
import { Thread, Message } from "@/features/api/gen/models"
import { useQueryClient } from "@tanstack/react-query";
import { useMailboxContext } from "../providers/mailbox";

type MarkAsOptions = {
    threadIds?: Thread["id"][],
    messageIds?: Message['id'][],
    onSuccess?: () => void,
}

/**
 * Hook to mark messages or threads as trashed
 */
const useTrash = () => {
    const queryClient = useQueryClient();
    const { invalidateThreadMessages } = useMailboxContext();

    const { mutate, status } = useFlagCreate({
        mutation: {
            onSuccess: () => {
                invalidateThreadMessages();
                queryClient.invalidateQueries({ queryKey: ["/api/v1.0/mailboxes/"] });
            },
        }
    });

    const markAsTrash = 
        (status: boolean) =>
        ({ threadIds = [], messageIds = [], onSuccess }: MarkAsOptions) =>
            mutate({
                data: {
                    flag: 'trashed',
                    value: status,
                    thread_ids: threadIds,
                    message_ids: messageIds,
                },
            }, {
                onSuccess,
            });

    return { 
        markAsTrashed: markAsTrash(true),
        markAsUntrashed: markAsTrash(false),
        status
    };
};

export default useTrash;