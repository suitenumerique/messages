import { useFlagCreate } from "@/features/api/gen"
import { Thread, Message, FlagEnum, ChangeFlagRequestRequest, Mailbox } from "@/features/api/gen/models"
import { addToast, ToasterItem } from "../ui/components/toaster";
import { toast, ToastContentProps } from "react-toastify";
import { useTranslation } from "react-i18next";
import ToastBatchHelper, { ToastBatch } from "@/features/utils/toast-batch-helper";

type FlagOnSuccess = (data: ChangeFlagRequestRequest, updatedCount?: number) => void;

type MarkAsOptions = {
    threadIds?: Thread["id"][],
    messageIds?: Message['id'][],
    /** Thread of the `messageIds`: their toast does not merge with other threads'. */
    messageThreadId?: Thread['id'] | null,
    mailboxId?: string,
    readAt?: string | null,
    starredAt?: string | null,
    onSuccess?: FlagOnSuccess,
}

type FlagOptions = {
    toastMessages?: FlagToastMessages;
    onSuccess?: FlagOnSuccess;
    showToast?: boolean;
}

type FlagToastMessages = {
    thread: (updatedCount: number, submittedCount: number) => string;
    message: (updatedCount: number, submittedCount: number) => string;
}

// Items flagged while the toast is still visible are merged into it, so its
// count and its undo action cover all of them and not only the first batch.
// Keyed by `ToastBatchHelper.getKey`; a batch leaves the map once its toast
// closes or its undo starts, so later items open a new toast.
const toastBatches = new Map<string, ToastBatch>();
// react-toastify keeps a closed toast until its exit animation ends and drops
// any new toast reusing its id meanwhile, hence an id per batch.
let toastSequence = 0;

const releaseToastBatch = (batchKey: string, toastId: string): ToastBatch | undefined => {
    const batch = toastBatches.get(batchKey);
    if (batch?.toastId !== toastId) return undefined;
    toastBatches.delete(batchKey);
    return batch;
};

const extractUpdatedCount = (response: { data: unknown }): number | undefined => {
    const data = response.data as Record<string, unknown>;
    return typeof data.updated_threads === 'number' ? data.updated_threads : undefined;
};

/**
 * Generic hook to update thread/message flags
 * !!! Do not use this hook directly, use the specialized hooks instead !!!
 */
const useFlag = (flag: FlagEnum, options?: FlagOptions) => {
    const { mutateAsync, status } = useFlagCreate({
        mutation: {
            onSuccess: (response, { data }) => options?.onSuccess?.(data, extractUpdatedCount(response)),
        }
    });

    const showSuccessToast = (
        data: ChangeFlagRequestRequest,
        updatedCount: number | undefined,
        messageThreadId: MarkAsOptions['messageThreadId'],
    ) => {
        if (options?.showToast === false || data.value !== true) return;
        const batchKey = ToastBatchHelper.getKey(data, messageThreadId);
        const previous = toastBatches.get(batchKey);
        const isMerged = previous !== undefined && toast.isActive(previous.toastId);
        const toastId = isMerged ? previous.toastId : `${batchKey}--${++toastSequence}`;
        const batch = ToastBatchHelper.merge(isMerged ? previous : undefined, toastId, data, updatedCount);
        toastBatches.set(batchKey, batch);

        const content = <FlagUpdateSuccessToast
            flag={flag}
            batchKey={batchKey}
            threadIds={batch.threadIds}
            messageIds={batch.messageIds}
            mailboxId={data.mailbox_id}
            toastId={toastId}
            messages={options?.toastMessages}
            onUndo={options?.onSuccess}
            skippedCount={batch.skippedCount}
        />;
        if (isMerged) {
            // Updating the toast also restarts its auto-close timer.
            // Applied after 100ms by default: long enough for the
            // toast to close meanwhile and be brought back.
            toast.update(toastId, { render: content, delay: 0 });
        } else {
            addToast(content, { toastId, onClose: () => releaseToastBatch(batchKey, toastId) });
        }
    };

    const markAs =
        (status: boolean) =>
        ({ threadIds = [], messageIds = [], messageThreadId, mailboxId, readAt, starredAt, onSuccess }: MarkAsOptions) => {
            const data: ChangeFlagRequestRequest = {
                flag,
                value: status,
                thread_ids: threadIds,
                message_ids: messageIds,
                mailbox_id: mailboxId,
                ...(readAt !== undefined && { read_at: readAt }),
                ...(starredAt !== undefined && { starred_at: starredAt }),
            };
            // `mutate` keeps only the callbacks of its latest call, so flagging
            // twice in a row would drop the `onSuccess` of the first call. Each
            // `mutateAsync` call settles its own promise. The global
            // MutationCache handler already reports the errors.
            // The promise settles even if the caller has unmounted meanwhile,
            // so the toast still shows; callbacks must read live state rather
            // than the render that fired them (as `unselectThread` does with
            // the route).
            mutateAsync({ data }).then(
                (response) => {
                    const updatedCount = extractUpdatedCount(response);
                    showSuccessToast(data, updatedCount, messageThreadId);
                    onSuccess?.(data, updatedCount);
                },
                () => {},
            );
        };

    return {
        mark: markAs(true),
        unmark: markAs(false),
        status
    };
};

type FlagUpdateSuccessToastProps = {
    flag: FlagEnum;
    batchKey: string;
    threadIds?: Thread['id'][];
    messageIds?: Message['id'][];
    mailboxId?: Mailbox['id'];
    toastId: string;
    messages?: FlagToastMessages;
    onUndo?: FlagOnSuccess;
    skippedCount?: number;
}
const FlagUpdateSuccessToast = ({ flag, batchKey, threadIds = [], messageIds = [], mailboxId, toastId, messages, onUndo, skippedCount, closeToast }: FlagUpdateSuccessToastProps & Partial<ToastContentProps>) => {
    const { t } = useTranslation();
    // A mutation-level callback, so the side effects of the undo still run
    // once the toast is gone.
    const { unmark } = useFlag(flag, { showToast: false, onSuccess: onUndo });

    const { isThreadScope, submittedCount, displayCount, isPartial, isNone } = ToastBatchHelper.summarize({ threadIds, messageIds, skippedCount });

    const undo = () => {
        // Taking the batch out seals it: items flagged from now on open a new
        // toast rather than joining this one. The batch also holds the items
        // merged since the last render of this toast.
        const batch = releaseToastBatch(batchKey, toastId) ?? { threadIds, messageIds };
        toast.dismiss(toastId);
        unmark({ threadIds: batch.threadIds, messageIds: batch.messageIds, mailboxId });
    }

    const toastType = isNone ? 'error' : isPartial ? 'warning' : 'info';
    const mainMessage = isThreadScope
        ? (messages?.thread?.(displayCount, submittedCount) ?? t('{{count}} threads have been updated.', { count: displayCount, defaultValue_one: 'The thread has been updated.' }))
        : (messages?.message?.(displayCount, submittedCount) ?? t('{{count}} messages have been updated.', { count: displayCount, defaultValue_one: 'The message has been updated.' }));

    return (
        <ToasterItem
            type={toastType}
            actions={isNone ? [] : [{ label: t('Undo'), onClick: undo }]}
            closeToast={closeToast}
        >
            <div>
                <p>{mainMessage}</p>
                {(isPartial || isNone) && (
                    <p>{t('You may not have sufficient permissions for all selected threads.')}</p>
                )}
            </div>
        </ToasterItem>
    )
};

export default useFlag;
