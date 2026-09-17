import { MessageFormMode } from "@/features/forms/components/message-form";
import { randomUUID } from "@/features/utils/uuid";
import { enforceSingleExpanded } from "./core";
import { ComposeWindowDescriptor, ComposeWindowPresentation } from "./types";
import { COMPOSE_WINDOWS_STORAGE_KEY } from "@/features/config";



const FORM_MODES: MessageFormMode[] = ["new", "reply", "reply_all", "forward"];
const PRESENTATIONS: ComposeWindowPresentation[] = ["docked", "floating"];

type PersistedComposeWindow = {
    draftId: string;
    mailboxId: string;
    mode: MessageFormMode;
    presentation: ComposeWindowPresentation;
    isMinimized: boolean;
    threadId?: string;
    parentMessageId?: string;
};

/**
 * Keeps only what a page reload can restore: windows whose draft exists
 * server-side. Unmaterialized windows are empty by definition (autosave
 * materializes on first real content) so dropping them loses nothing.
 */
export const serializeWindows = (windows: readonly ComposeWindowDescriptor[]): string => {
    const persisted: PersistedComposeWindow[] = windows
        .filter((window): window is ComposeWindowDescriptor & { draftId: string } => !!window.draftId)
        .map((window) => ({
            draftId: window.draftId,
            mailboxId: window.mailboxId,
            mode: window.mode,
            presentation: window.presentation,
            isMinimized: window.isMinimized,
            threadId: window.threadId,
            parentMessageId: window.parentMessageId,
        }));
    return JSON.stringify(persisted);
};

const toDescriptor = (entry: PersistedComposeWindow): ComposeWindowDescriptor => ({
    windowId: randomUUID(),
    mailboxId: entry.mailboxId,
    mode: entry.mode,
    presentation: entry.presentation,
    isMinimized: entry.isMinimized,
    draftId: entry.draftId,
    threadId: typeof entry.threadId === "string" ? entry.threadId : undefined,
    parentMessageId: typeof entry.parentMessageId === "string" ? entry.parentMessageId : undefined,
    openedOnExistingDraft: true,
    focusTick: 0,
});

/**
 * Defensive parsing: the storage may hold corrupted or outdated payloads.
 * Anything unexpected yields an empty list rather than a crash at boot.
 */
export const deserializeWindows = (raw: string | null): ComposeWindowDescriptor[] => {
    if (!raw) return [];
    let parsed: unknown;
    try {
        parsed = JSON.parse(raw);
    } catch {
        return [];
    }
    if (!Array.isArray(parsed)) return [];
    const windows = parsed
        .filter((entry): entry is PersistedComposeWindow =>
            !!entry
            && typeof entry === "object"
            && typeof (entry as PersistedComposeWindow).draftId === "string"
            && typeof (entry as PersistedComposeWindow).mailboxId === "string"
            && FORM_MODES.includes((entry as PersistedComposeWindow).mode)
            && PRESENTATIONS.includes((entry as PersistedComposeWindow).presentation)
            && typeof (entry as PersistedComposeWindow).isMinimized === "boolean"
        )
        .map(toDescriptor);
    return enforceSingleExpanded(windows);
};

export const loadPersistedWindows = (): ComposeWindowDescriptor[] => {
    if (typeof localStorage === "undefined") return [];
    try {
        return deserializeWindows(localStorage.getItem(COMPOSE_WINDOWS_STORAGE_KEY));
    } catch {
        return [];
    }
};

export const persistWindows = (windows: readonly ComposeWindowDescriptor[]) => {
    if (typeof localStorage === "undefined") return;
    try {
        localStorage.setItem(COMPOSE_WINDOWS_STORAGE_KEY, serializeWindows(windows));
    } catch {
        // Quota errors and private-mode restrictions must never break compose.
    }
};

/**
 * Forget the persisted windows. The list only holds draft ids, but on a shared
 * device the next account would still try to reopen the previous one's drafts
 * at boot, so it goes with the session.
 */
export const clearPersistedWindows = () => {
    if (typeof localStorage === "undefined") return;
    try {
        localStorage.removeItem(COMPOSE_WINDOWS_STORAGE_KEY);
    } catch {
        // Same tolerance as persistWindows: storage failures never block logout.
    }
};
