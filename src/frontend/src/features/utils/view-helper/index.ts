import { LargestThread } from "@/features/api/gen/models";
import { SearchHelper } from "../search-helper";
import { ALL_MESSAGES_FOLDER, MAILBOX_FOLDERS } from "@/features/layouts/components/mailbox-panel/components/mailbox-list";

// Type for the union of all folder id values, generated from MAILBOX_FOLDERS
// plus the virtual "all_messages" folder which lives outside the sidebar.
export type ViewName =
    | ReturnType<typeof MAILBOX_FOLDERS>[number]['id']
    | ReturnType<typeof ALL_MESSAGES_FOLDER>['id'];

// Folders a thread is opened in, most relevant first (see getThreadFolderSearch).
const THREAD_FOLDER_PREFERENCE: ViewName[] = ['inbox', 'archives', 'outbox', 'sent', 'drafts'];

class ViewHelper {
    #isView(viewName: ViewName): boolean {
        if (typeof window === 'undefined') return false;

        const searchParams = new URLSearchParams(window.location.search);
        const folder = viewName === 'all_messages'
            ? ALL_MESSAGES_FOLDER()
            : MAILBOX_FOLDERS().find((folder) => folder.id === viewName);
        if (!folder) throw new Error(`${viewName} folder not found. Invalid folder id "${viewName}".`);

        const matchViewFilters = Object.entries(folder.filter || {}).every(([key, value]) => searchParams.get(key) === value);
        if (matchViewFilters) return true;

        const isSearchable = viewName === 'all_messages' || ('searchable' in folder && folder.searchable);
        const matchSearchParams = isSearchable && SearchHelper.parseSearchQuery(searchParams.get('search') || '')?.in === viewName;
        return matchSearchParams;
    }

    static isInboxView() {
        return new ViewHelper().#isView('inbox');
    }

    static isAllMessagesView() {
        return new ViewHelper().#isView('all_messages');
    }

    static isTrashedView() {
        return new ViewHelper().#isView('trash');
    }

    static isSpamView() {
        return new ViewHelper().#isView('spam');
    }

    static isArchivedView() {
        return new ViewHelper().#isView('archives');
    }

    static isSentView() {
        return new ViewHelper().#isView('sent');
    }

    static isDraftsView() {
        return new ViewHelper().#isView('drafts');
    }

    static isOutboxView() {
        return new ViewHelper().#isView('outbox');
    }

    /**
     * Pick the folder to open a thread in, from its folder flags.
     *
     * Spam and fully trashed threads go to their own folder: every other folder
     * filters them out. Otherwise the current folder is kept when it lists the
     * thread, else the first folder of THREAD_FOLDER_PREFERENCE that does —
     * sent and drafts last, as a thread holding one usually has received mail
     * too. Only top-level folders are matched: their filters are all thread
     * flags, while subfolders also filter on per-user state (unread, starred…).
     *
     * @param thread The thread's folder flags.
     * @returns The search params to open the thread with, or `true` to keep the
     * current ones.
     */
    static getThreadFolderSearch(thread: ThreadFolderFlags): Record<string, string> | true {
        const folders = MAILBOX_FOLDERS();
        const filterOf = (id: ViewName) => folders.find((folder) => folder.id === id)?.filter ?? {};
        if (thread.is_spam) return filterOf('spam');
        if (thread.is_trashed) return filterOf('trash');

        const lists = (filter: Record<string, string>) => Object.entries(filter).every(
            ([key, value]) => key in thread && (thread[key as keyof ThreadFolderFlags] ? "1" : "0") === value,
        );
        const currentSearch = typeof window === 'undefined'
            ? {}
            : Object.fromEntries(new URLSearchParams(window.location.search));
        if (Object.keys(currentSearch).length > 0 && lists(currentSearch)) return true;

        for (const id of THREAD_FOLDER_PREFERENCE) {
            const filter = filterOf(id);
            if (lists(filter)) return filter;
        }
        return true;
    }
}

/** The thread flags the sidebar folders filter on. */
export type ThreadFolderFlags = Pick<
    LargestThread,
    'has_active' | 'has_draft' | 'has_sender' | 'has_delivery_pending' | 'has_archived' | 'has_trashed' | 'is_trashed' | 'is_spam'
>;

export default ViewHelper;
