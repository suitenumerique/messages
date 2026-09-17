/**
 * Cross-tab synchronization for compose surfaces. Every surface editing a
 * draft (pop-out page, docked window) broadcasts its lifecycle events so the
 * other tabs can refresh their caches (queries do not refetch on window focus
 * in this app) and drop the windows of a draft that no longer exists.
 */

import { randomUUID } from "@/features/utils/uuid";

export const COMPOSE_BROADCAST_CHANNEL = "messages:compose";

export type ComposeBroadcastMessage = {
    type: "draft-updated" | "draft-sent" | "draft-deleted";
    draftId: string;
    threadId?: string;
    mailboxId: string;
};

type ComposeBroadcastEnvelope = ComposeBroadcastMessage & {
    /** Tab that posted the message, see `TAB_ID`. */
    source: string;
};

// A BroadcastChannel delivers to every other channel object of the origin,
// the ones of the posting tab included: the subscriber uses this id to skip
// its own tab's events, which the local state already reflects.
const TAB_ID = randomUUID();

export const postComposeBroadcast = (message: ComposeBroadcastMessage) => {
    if (typeof BroadcastChannel === "undefined") return;
    const channel = new BroadcastChannel(COMPOSE_BROADCAST_CHANNEL);
    const envelope: ComposeBroadcastEnvelope = { ...message, source: TAB_ID };
    channel.postMessage(envelope);
    channel.close();
};

export const subscribeToComposeBroadcast = (
    onMessage: (message: ComposeBroadcastMessage) => void,
): (() => void) => {
    if (typeof BroadcastChannel === "undefined") return () => {};
    const channel = new BroadcastChannel(COMPOSE_BROADCAST_CHANNEL);
    channel.onmessage = (event: MessageEvent<ComposeBroadcastEnvelope>) => {
        if (event.data?.type && event.data.draftId && event.data.source !== TAB_ID) {
            onMessage(event.data);
        }
    };
    return () => channel.close();
};
