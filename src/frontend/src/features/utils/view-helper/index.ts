import { SearchHelper } from "../search-helper";
import { ALL_MESSAGES_FOLDER, MAILBOX_FOLDERS } from "@/features/layouts/components/mailbox-panel/components/mailbox-list";

// Type for the union of all folder id values, generated from MAILBOX_FOLDERS
// plus the virtual "all_messages" folder which lives outside the sidebar.
export type ViewName =
    | ReturnType<typeof MAILBOX_FOLDERS>[number]['id']
    | ReturnType<typeof ALL_MESSAGES_FOLDER>['id'];

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
}

export default ViewHelper;
