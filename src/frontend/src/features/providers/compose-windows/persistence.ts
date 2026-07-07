import { MessageFormMode } from "@/features/forms/components/message-form";
import { ComposeWindowDescriptor, ComposeWindowDisplayState } from "./types";

export const COMPOSE_WINDOWS_STORAGE_KEY = "messages:compose-windows:v1";

const FORM_MODES: MessageFormMode[] = ["new", "reply", "reply_all", "forward"];
const RESTORABLE_STATES: ComposeWindowDisplayState[] = ["open", "minimized"];

type PersistedComposeWindow = {
    draftId: string;
    mailboxId: string;
    mode: MessageFormMode;
    state: ComposeWindowDisplayState;
    threadId?: string;
    parentMessageId?: string;
};

/**
 * Keeps only what a page reload can restore: windows whose draft exists
 * server-side. Unmaterialized windows are empty by definition (autosave
 * materializes on first real content) so dropping them loses nothing.
 * "expanded" is downgraded to "open": restoring a full overlay after a
 * reload would be intrusive.
 */
export const serializeWindows = (windows: readonly ComposeWindowDescriptor[]): string => {
    const persisted: PersistedComposeWindow[] = windows
        .filter((window): window is ComposeWindowDescriptor & { draftId: string } => !!window.draftId)
        .map((window) => ({
            draftId: window.draftId,
            mailboxId: window.mailboxId,
            mode: window.mode,
            state: window.state === "expanded" ? "open" : window.state,
            threadId: window.threadId,
            parentMessageId: window.parentMessageId,
        }));
    return JSON.stringify(persisted);
};

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
    return parsed
        .filter((entry): entry is PersistedComposeWindow =>
            !!entry
            && typeof entry === "object"
            && typeof (entry as PersistedComposeWindow).draftId === "string"
            && typeof (entry as PersistedComposeWindow).mailboxId === "string"
            && FORM_MODES.includes((entry as PersistedComposeWindow).mode)
            && RESTORABLE_STATES.includes((entry as PersistedComposeWindow).state)
        )
        .map((entry) => ({
            windowId: crypto.randomUUID(),
            mailboxId: entry.mailboxId,
            mode: entry.mode,
            state: entry.state,
            draftId: entry.draftId,
            threadId: typeof entry.threadId === "string" ? entry.threadId : undefined,
            parentMessageId: typeof entry.parentMessageId === "string" ? entry.parentMessageId : undefined,
            openedOnExistingDraft: true,
            focusTick: 0,
        }));
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
