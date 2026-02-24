import { useCallback } from "react";
import { useRouter } from "next/router";
import { useTranslation } from "react-i18next";
import { threadsSplitCreateResponse201, useThreadsSplitCreate } from "@/features/api/gen/threads/threads";
import { useMailboxContext } from "../providers/mailbox";
import { addToast, ToasterItem } from "../ui/components/toaster";
import { handle } from "../utils/errors";

/**
 * Hook to split a thread at a given message.
 * Moves the selected message and all later messages to a new thread.
 */
const useSplitThread = () => {
    const { t } = useTranslation();
    const router = useRouter();
    const { selectedMailbox, invalidateThreadMessages, invalidateThreadsStats } = useMailboxContext();
    const { mutateAsync, status } = useThreadsSplitCreate();

    const splitThread = useCallback(async ({ threadId, messageId }: { threadId: string; messageId: string }) => {
        try {
            const response = await mutateAsync({
                id: threadId,
                data: { message_id: messageId },
            }) as threadsSplitCreateResponse201;

            await invalidateThreadMessages();
            await invalidateThreadsStats();

            // Navigate to the new thread
            if (selectedMailbox) {
                router.replace(`/mailbox/${selectedMailbox.id}/thread/${response.data.id}${window.location.search}`);
            }

            addToast(
                <ToasterItem>
                    {t("Thread has been split successfully.")}
                </ToasterItem>,
                { toastId: "split-thread-success" }
            );
        } catch (error) {
            handle(error);
        }
    }, [mutateAsync, invalidateThreadMessages, invalidateThreadsStats, selectedMailbox, router, t]);

    return { splitThread, status };
};

export default useSplitThread;
