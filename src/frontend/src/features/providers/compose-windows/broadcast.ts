/**
 * Cross-tab synchronization for compose surfaces. The standalone pop-out page
 * broadcasts draft lifecycle events so the main tab can refresh its caches
 * (queries do not refetch on window focus in this app).
 */

export const COMPOSE_BROADCAST_CHANNEL = "messages:compose";

export type ComposeBroadcastMessage = {
    type: "draft-updated" | "draft-sent" | "draft-deleted";
    draftId: string;
    threadId?: string;
    mailboxId: string;
};

export const postComposeBroadcast = (message: ComposeBroadcastMessage) => {
    if (typeof BroadcastChannel === "undefined") return;
    const channel = new BroadcastChannel(COMPOSE_BROADCAST_CHANNEL);
    channel.postMessage(message);
    channel.close();
};

export const subscribeToComposeBroadcast = (
    onMessage: (message: ComposeBroadcastMessage) => void,
): (() => void) => {
    if (typeof BroadcastChannel === "undefined") return () => {};
    const channel = new BroadcastChannel(COMPOSE_BROADCAST_CHANNEL);
    channel.onmessage = (event: MessageEvent<ComposeBroadcastMessage>) => {
        if (event.data?.type && event.data.draftId) {
            onMessage(event.data);
        }
    };
    return () => channel.close();
};
