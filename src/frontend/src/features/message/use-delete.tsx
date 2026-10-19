import { threadsBulkDeleteCreateResponse, useThreadsBulkDeleteCreate } from "@/features/api/gen";
import { Message, ScopeEnum, Thread } from "@/features/api/gen/models";
import { addToast, ToasterItem } from "../ui/components/toaster";
import { useMailboxContext } from "../providers/mailbox";

type DeleteOnSuccess = (deletedCount?: number) => void;

export type DeleteOptions = {
    threadIds?: Thread["id"][];
    messageIds?: Message["id"][];
    onSuccess?: DeleteOnSuccess;
};

type ToastMessage = (deletedCount: number, submittedCount: number) => string;

type DeletedTargets = {
    /** Messages actually deleted: the backend silently skips the ones the
     * user cannot edit, so this may be a subset of the submitted targets. */
    messageIds: Message["id"][];
    /** Threads submitted for deletion. */
    threadIds: Thread["id"][];
};

type UseDeleteOptions = {
    toastMessage?: ToastMessage;
    onSuccess?: (deletedCount: number | undefined, deleted: DeletedTargets) => void;
    showToast?: boolean;
};

// fetchAPI throws on non-2xx responses: the narrowing is only for typing.
const extractResult = (response: threadsBulkDeleteCreateResponse) =>
    response.status === 200 ? response.data : undefined;

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
                const result = extractResult(response);
                const deletedCount = result?.deleted_count;
                unpinThreads(data.thread_ids ?? []);
                options?.onSuccess?.(deletedCount, {
                    messageIds: result?.deleted_message_ids ?? [],
                    threadIds: data.thread_ids ?? [],
                });
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
                onSuccess: (response) => onSuccess?.(extractResult(response)?.deleted_count),
            }
        );

    return { remove, status };
};

export default useDelete;
