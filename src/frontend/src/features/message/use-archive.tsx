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

const ARCHIVED_TOAST_ID = "ARCHIVED_TOAST_ID";

/**
 * Hook to mark messages or threads as archived
 */
const useArchive = () => {
    const { invalidateThreadMessages, invalidateThreadsStats } = useMailboxContext();

    const { mutate, status } = useFlagCreate({
        mutation: {
            onSuccess: (_, { data }) => {
                invalidateThreadMessages();
                invalidateThreadsStats();
                if (data.value === true) {
                    addToast(<ArchiveSuccessToast threadIds={data.thread_ids} messageIds={data.message_ids} />, {
                        toastId: ARCHIVED_TOAST_ID,
                    })
                }
            },
        }
    });

    const markAsArchived =
        (status: boolean) =>
        ({ threadIds = [], messageIds = [], onSuccess }: MarkAsOptions) =>
            mutate({
                data: {
                    flag: 'archived',
                    value: status,
                    thread_ids: threadIds,
                    message_ids: messageIds,
                },
            }, {
                onSuccess
            });

    return {
        markAsArchived: markAsArchived(true),
        markAsUnarchived: markAsArchived(false),
        status
    };
};

const ArchiveSuccessToast = ({ threadIds = [], messageIds = [] }: { threadIds?: Thread['id'][], messageIds?: Message['id'][] }) => {
    const { t } = useTranslation();
    const { markAsUnarchived } = useArchive();

    const undo = () => {
        markAsUnarchived({
            threadIds: threadIds,
            messageIds: messageIds,
            onSuccess: () => {
                toast.dismiss(ARCHIVED_TOAST_ID);
            }
        });
    }
    return (
        <ToasterItem
            type="info"
            actions={[{ label: t('Undo'), onClick: undo }]}
        >
            <span>{threadIds.length > 0 ? t('The conversation has been archived') : t('The message has been archived')}</span>
        </ToasterItem>
    )
};

export default useArchive;
