import type { ChangeFlagRequestRequest, Message, Thread } from "@/features/api/gen/models";

/** Items flagged while their toast is visible, merged into that toast. */
export type ToastBatch = {
    toastId: string;
    threadIds: Thread['id'][];
    messageIds: Message['id'][];
    /** Items the backend left untouched; unknown if any response lacked a count. */
    skippedCount?: number;
};

export type ToastBatchSummary = {
    isThreadScope: boolean;
    submittedCount: number;
    displayCount: number;
    isPartial: boolean;
    isNone: boolean;
};

class ToastBatchHelper {
    /**
     * Key under which successive flags share a toast: same flag, scope and
     * mailbox and, for messages, same thread. Access is granted per thread,
     * so a batch of messages from one thread either all succeeds or all fails:
     * merging the messages of another thread could hide a failure behind a
     * success, as a message flag only tells whether any thread was updated.
     * @param data - the flag request.
     * @param messageThreadId - the thread the flagged messages belong to.
     * @returns the batch key.
     */
    static getKey(data: ChangeFlagRequestRequest, messageThreadId?: Thread['id'] | null): string {
        const scope = (data.thread_ids?.length ?? 0) > 0 ? 'thread' : `message--${messageThreadId ?? 'none'}`;
        return `${data.flag.toLowerCase()}_TOAST_ID--${scope}--${data.mailbox_id ?? 'none'}`;
    }

    /**
     * Merge a flag response into the batch of the toast it joins.
     * @param previous - the batch of the visible toast, if the flag joins it.
     * @param toastId - the toast the batch belongs to.
     * @param data - the flag request.
     * @param updatedCount - the `updated_threads` of the response, if any.
     * @returns the new batch.
     */
    static merge(
        previous: ToastBatch | undefined,
        toastId: string,
        data: ChangeFlagRequestRequest,
        updatedCount: number | undefined,
    ): ToastBatch {
        const skippedCount = ToastBatchHelper.countSkipped(data, updatedCount);
        return {
            toastId,
            threadIds: [...new Set([...(previous?.threadIds ?? []), ...(data.thread_ids ?? [])])],
            messageIds: [...new Set([...(previous?.messageIds ?? []), ...(data.message_ids ?? [])])],
            skippedCount: !previous
                ? skippedCount
                : previous.skippedCount === undefined || skippedCount === undefined
                    ? undefined
                    : previous.skippedCount + skippedCount,
        };
    }

    /**
     * What the toast of a batch reports: how many items, and whether some or
     * all of them failed.
     * @param batch - the batch shown by the toast.
     * @returns the counts and failure state of the batch.
     */
    static summarize({ threadIds, messageIds, skippedCount }: Omit<ToastBatch, 'toastId'>): ToastBatchSummary {
        const isThreadScope = threadIds.length > 0;
        const submittedCount = isThreadScope ? threadIds.length : messageIds.length;
        const updatedCount = skippedCount === undefined ? undefined : Math.max(0, submittedCount - skippedCount);
        const isPartial = isThreadScope && updatedCount !== undefined && updatedCount > 0 && updatedCount < submittedCount;
        const isNone = updatedCount === 0 && submittedCount > 0;
        return {
            isThreadScope,
            submittedCount,
            displayCount: isNone || isPartial ? updatedCount ?? 0 : submittedCount,
            isPartial,
            isNone,
        };
    }

    // Counting the items left untouched rather than the updated ones keeps a
    // thread flagged twice from being counted twice and hiding a failure.
    private static countSkipped(data: ChangeFlagRequestRequest, updatedCount: number | undefined): number | undefined {
        if (updatedCount === undefined) return undefined;
        const threadCount = data.thread_ids?.length ?? 0;
        if (threadCount > 0) return Math.max(0, threadCount - updatedCount);
        // For messages, `updated_threads` counts their threads: it only tells
        // whether any of them was updated.
        return updatedCount === 0 ? (data.message_ids?.length ?? 0) : 0;
    }
}

export default ToastBatchHelper;
