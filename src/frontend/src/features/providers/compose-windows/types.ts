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
    /**
     * Mailbox currently picked in the form's "From" field, reported by the
     * form once mounted. Only drives the sender shown on the window surfaces:
     * `mailboxId` stays the context the window was opened from.
     */
    senderId?: string;
    /** True while the draft is being transferred to `senderId`. Not persisted. */
    isSenderPending?: boolean;
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

/** A window whose draft exists server-side. */
export type MaterializedComposeWindow = ComposeWindowDescriptor & { draftId: string };

/** Drafts deleted from outside their compose window (drafts list, thread toolbar). */
export type DeletedDrafts = {
    /** Only the drafts the backend actually deleted, not every submitted target. */
    draftIds: readonly string[];
    /** Threads targeted by the deletion, whose messages must be refreshed. */
    threadIds: readonly string[];
    /** Mailbox whose threads list and stats must be refreshed. */
    mailboxId?: string;
};

export type OpenComposeWindowInput = {
    mailboxId: string;
    mode?: MessageFormMode;
    draftId?: string;
    threadId?: string;
    parentMessageId?: string;
};
