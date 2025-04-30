import { Thread } from "../api/gen/models";

export type ThreadUnreadState = 'full' | 'partial' | null;

class ThreadHelper {
    /**
     * IsUnread is an helper function that returns thread unread state.
     * If bool (default to false) is true, it returns a boolean instead of a ThreadUnreadState.
     * If bool is false, it returns a ThreadUnreadState : 
     * - 'full' if the thread has no unread messages.
     * - 'partial' if the thread has at least one unread message.
     * - null if the thread has no unread messages.
     * 
     */

    static isUnread(thread: Thread, bool?: true): boolean
    static isUnread(thread: Thread, bool?: false): ThreadUnreadState
    static isUnread(thread: Thread, bool: boolean = false): boolean | ThreadUnreadState {
        const total_messages = thread.count_messages ?? 0;
        const unread_messages = thread.count_unread ?? 0;
        let state: ThreadUnreadState = null;

        if (total_messages > 0 && unread_messages === total_messages) state = 'full';
        else if (unread_messages > 0) state = 'partial';

        return bool ? Boolean(state) : state;
    }
}

export default ThreadHelper;