import { Message } from "@/features/api/gen";
import { MessageFormMode } from "@/features/forms/components/message-form";

export type ComposeWindowDisplayState = "open" | "minimized" | "expanded";

export type ComposeWindowDescriptor = {
    windowId: string;
    mailboxId: string;
    mode: MessageFormMode;
    state: ComposeWindowDisplayState;
    /** Set once the draft is materialized server-side. */
    draftId?: string;
    threadId?: string;
    parentMessageId?: string;
    /** Last known subject, used as the window title. */
    title?: string;
    /**
     * True when the window was opened on a draft that already existed (detached
     * reply, restored window): closing it saves silently instead of asking the
     * "keep or delete" question reserved for brand new drafts.
     */
    openedOnExistingDraft: boolean;
    /** Incremented by focusWindow so the window can grab focus imperatively. */
    focusTick: number;
    /** Transient snapshots to skip the initial fetches. Never persisted. */
    initialDraft?: Message;
    initialParent?: Message;
};

export type OpenComposeWindowInput = {
    mailboxId: string;
    mode?: MessageFormMode;
    draftId?: string;
    threadId?: string;
    parentMessageId?: string;
    initialDraft?: Message;
    initialParent?: Message;
};
