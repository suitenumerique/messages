import { useFlagCreate } from "@/features/api/gen"
import { Thread, Message } from "@/features/api/gen/models"
import { useMailboxContext } from "../providers/mailbox";
import { addToast, ToasterItem } from "../ui/components/toaster";
import { toast } from "react-toastify";
import { useTranslation } from "react-i18next";

type MarkAsOptions = {
    threadIds?: Thread["id"][],
    messageIds?: Message['id'][],
    onSuccess?: () => void,
}

const TRASHED_TOAST_ID = "TRASHED_TOAST_ID";

/**
 * Hook to mark messages or threads as trashed
 */
const useTrash = () => {
    const { invalidateThreadMessages, invalidateThreadsStats } = useMailboxContext();

    const { mutate, status } = useFlagCreate({
        mutation: {
            onSuccess: (_, { data }) => {
                invalidateThreadMessages();
                invalidateThreadsStats();
                if (data.value === true) {
                    addToast(<TrashSuccessToast threadIds={data.thread_ids} messageIds={data.message_ids} />, {
                        toastId: TRASHED_TOAST_ID,
                    })
                }
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
                onSuccess
            });

    return { 
        markAsTrashed: markAsTrash(true),
        markAsUntrashed: markAsTrash(false),
        status
    };
};

const TrashSuccessToast = ({ threadIds = [], messageIds = [] }: { threadIds?: Thread['id'][], messageIds?: Message['id'][] }) => {
    const { t } = useTranslation();
    const { markAsUntrashed } = useTrash();

    const undo = () => {
        markAsUntrashed({
            threadIds: threadIds,
            messageIds: messageIds,
            onSuccess: () => {
                toast.dismiss(TRASHED_TOAST_ID);
            }
        });
    }
    return (
        <ToasterItem
            type="info"
            actions={[{ label: t('actions.undo'), onClick: undo }]}
        >
            <span>{threadIds.length > 0 ? t('trash.thread_deleted') : t('trash.message_deleted')}</span>
        </ToasterItem>
    )
};

export default useTrash;
