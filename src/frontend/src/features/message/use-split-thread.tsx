import { useCallback } from "react";
import { useNavigate } from "@tanstack/react-router";
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
    const navigate = useNavigate();
    const { selectedMailbox, invalidateMailbox, invalidateThreadsStats } = useMailboxContext();
    const { mutateAsync, status } = useThreadsSplitCreate();

    const splitThread = useCallback(async ({ threadId, messageId }: { threadId: string; messageId: string }) => {
        try {
            const response = await mutateAsync({
                id: threadId,
                data: { message_id: messageId },
            }) as threadsSplitCreateResponse201;

            await invalidateMailbox();
            await invalidateThreadsStats();

            // Navigate to the new thread
            if (selectedMailbox) {
                navigate({ to: '/mailbox/$mailboxId/thread/$threadId', params: { mailboxId: selectedMailbox.id, threadId: response.data.id }, search: Object.fromEntries(new URLSearchParams(window.location.search)), replace: true });
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
    }, [mutateAsync, invalidateMailbox, invalidateThreadsStats, selectedMailbox, navigate, t]);

    return { splitThread, status };
};

export default useSplitThread;
