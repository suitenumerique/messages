import { MessageFormMode } from "@/features/forms/components/message-form";

/**
 * How the expanded window renders on desktop/tablet: docked to the bottom
 * right, or floating as a centered overlay. Mobile ignores it (the expanded
 * window is always a full-screen sheet).
 */
export type ComposeWindowPresentation = "docked" | "floating";

export type ComposeWindowDescriptor = {
    windowId: string;
    mailboxId: string;
    mode: MessageFormMode;
    presentation: ComposeWindowPresentation;
    /**
     * A minimized window collapses into a dock tab; restoring it reapplies
     * its presentation. At most one window is un-minimized at a time.
     */
    isMinimized: boolean;
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
};

export type OpenComposeWindowInput = {
    mailboxId: string;
    mode?: MessageFormMode;
    draftId?: string;
    threadId?: string;
    parentMessageId?: string;
};
