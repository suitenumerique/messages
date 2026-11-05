import { useCallback, useState } from "react";
import { RECENT_MESSAGE_TEMPLATES_KEY } from "@/features/config/constants";

export const MAX_RECENT_MESSAGE_TEMPLATES = 3;

type RecentTemplatesStore = Record<string, string[]>;

// Storage may be unavailable (private mode, blocked site data) or hold a
// corrupted value: recents are a convenience, so any failure means "none".
const readStore = (): RecentTemplatesStore => {
    try {
        const parsed: unknown = JSON.parse(sessionStorage.getItem(RECENT_MESSAGE_TEMPLATES_KEY) ?? "{}");
        if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) return {};
        return Object.fromEntries(
            Object.entries(parsed).filter(
                (entry): entry is [string, string[]] =>
                    Array.isArray(entry[1]) && entry[1].every((id) => typeof id === "string"),
            ),
        );
    } catch {
        return {};
    }
};

const writeStore = (store: RecentTemplatesStore) => {
    try {
        sessionStorage.setItem(RECENT_MESSAGE_TEMPLATES_KEY, JSON.stringify(store));
    } catch {
        // Recents are best effort.
    }
};

/**
 * Message templates recently inserted from a mailbox, most recent first.
 * Kept in sessionStorage: they only live as long as the browser tab.
 */
export const useRecentMessageTemplates = (mailboxId: string) => {
    const [recentIds, setRecentIds] = useState<string[]>(() => readStore()[mailboxId] ?? []);

    const addRecent = useCallback((templateId: string) => {
        const store = readStore();
        const ids = [
            templateId,
            ...(store[mailboxId] ?? []).filter((id) => id !== templateId),
        ].slice(0, MAX_RECENT_MESSAGE_TEMPLATES);
        writeStore({ ...store, [mailboxId]: ids });
        setRecentIds(ids);
    }, [mailboxId]);

    return { recentIds, addRecent };
};
