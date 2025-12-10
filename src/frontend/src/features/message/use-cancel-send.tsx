import { useMutation, useQueryClient } from "@tanstack/react-query";
import { fetchAPI } from "@/features/api/fetch-api";

type CancelSendParams = {
    taskId: string;
    messageId: string;
};

const cancelSend = async (params: CancelSendParams): Promise<void> => {
    await fetchAPI("/api/v1.0/send/cancel/", {
        method: "POST",
        body: JSON.stringify(params),
    });
};

export const useCancelSend = () => {
    const queryClient = useQueryClient();

    return useMutation({
        mutationFn: cancelSend,
        onSuccess: async () => {
            // Refetch queries and wait for them to complete
            // This ensures the UI has fresh data before navigation
            await Promise.all([
                queryClient.refetchQueries({ queryKey: ["messages"] }),
                queryClient.refetchQueries({ queryKey: ["threads"] }),
            ]);
        },
    });
};
