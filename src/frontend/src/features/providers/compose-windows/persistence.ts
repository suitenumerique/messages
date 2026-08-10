import { MessageFormMode } from "@/features/forms/components/message-form";
import { randomUUID } from "@/features/utils/uuid";
import { enforceSingleExpanded } from "./core";
import { ComposeWindowDescriptor, ComposeWindowPresentation } from "./types";

export const COMPOSE_WINDOWS_STORAGE_KEY = "messages:compose-windows:v2";
/** Pre-presentation payload ({state} instead of {presentation, isMinimized}). */
const LEGACY_STORAGE_KEY_V1 = "messages:compose-windows:v1";

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

type LegacyPersistedWindow = Omit<PersistedComposeWindow, "presentation" | "isMinimized"> & {
    state: "open" | "minimized" | "expanded";
};

/** Maps a v1 payload onto the v2 shape so the migration reuses the v2 parser. */
export const migrateLegacyPayload = (raw: string | null): string | null => {
    if (!raw) return null;
    let parsed: unknown;
    try {
        parsed = JSON.parse(raw);
    } catch {
        return null;
    }
    if (!Array.isArray(parsed)) return null;
    const migrated = parsed
        .filter((entry): entry is LegacyPersistedWindow =>
            !!entry
            && typeof entry === "object"
            && ["open", "minimized", "expanded"].includes((entry as LegacyPersistedWindow).state)
        )
        .map(({ state, ...rest }) => ({
            ...rest,
            presentation: state === "expanded" ? "floating" : "docked",
            isMinimized: state === "minimized",
        }));
    return JSON.stringify(migrated);
};

export const loadPersistedWindows = (): ComposeWindowDescriptor[] => {
    if (typeof localStorage === "undefined") return [];
    try {
        const raw = localStorage.getItem(COMPOSE_WINDOWS_STORAGE_KEY);
        if (raw !== null) return deserializeWindows(raw);
        const migrated = migrateLegacyPayload(localStorage.getItem(LEGACY_STORAGE_KEY_V1));
        localStorage.removeItem(LEGACY_STORAGE_KEY_V1);
        return deserializeWindows(migrated);
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
