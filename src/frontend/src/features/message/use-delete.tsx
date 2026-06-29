import { useThreadsBulkDeleteCreate } from "@/features/api/gen";
import { Message, ScopeEnum, Thread } from "@/features/api/gen/models";
import { addToast, ToasterItem } from "../ui/components/toaster";
import { useMailboxContext } from "../providers/mailbox";

type DeleteOnSuccess = (deletedCount?: number) => void;

type DeleteOptions = {
    threadIds?: Thread["id"][];
    messageIds?: Message["id"][];
    onSuccess?: DeleteOnSuccess;
};

type ToastMessage = (deletedCount: number, submittedCount: number) => string;

type UseDeleteOptions = {
    toastMessage?: ToastMessage;
    onSuccess?: DeleteOnSuccess;
    showToast?: boolean;
};

const extractDeletedCount = (response: { data: unknown }): number | undefined => {
    const data = response.data as Record<string, unknown>;
    return typeof data.deleted_count === "number" ? data.deleted_count : undefined;
};

/**
 * Generic hook to permanently (hard) delete draft or trashed messages in bulk.
 * Unlike `useFlag`/`useTrash`, the deletion is irreversible, so the toast
 * never offers an "Undo" action.
 * !!! Do not use this hook directly, use the specialized hooks instead !!!
 */
const useDelete = (scope: ScopeEnum, options?: UseDeleteOptions) => {
    const { unpinThreads } = useMailboxContext();
    const { mutate, status } = useThreadsBulkDeleteCreate({
        mutation: {
            onSuccess: (response, { data }) => {
                const deletedCount = extractDeletedCount(response);
                unpinThreads(data.thread_ids ?? []);
                options?.onSuccess?.(deletedCount);
                if (options?.showToast !== false && options?.toastMessage) {
                    const submittedCount =
                        data.thread_ids?.length || data.message_ids?.length || 0;
                    addToast(
                        <ToasterItem type={deletedCount === 0 ? "error" : "info"}>
                            <p>{options.toastMessage(deletedCount ?? submittedCount, submittedCount)}</p>
                        </ToasterItem>
                    );
                }
            },
        },
    });

    const remove = ({ threadIds = [], messageIds = [], onSuccess }: DeleteOptions) =>
        mutate(
            {
                data: {
                    scope,
                    thread_ids: threadIds,
                    message_ids: messageIds,
                },
            },
            {
                onSuccess: (response) => onSuccess?.(extractDeletedCount(response)),
            }
        );

    return { remove, status };
};

export default useDelete;
