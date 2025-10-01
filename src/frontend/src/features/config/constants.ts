/**
 * List of portal container ids of the app.
 * Take a look at `_document.tsx`
 */
export enum PORTALS {
    DRAG_PREVIEW = 'portal-drag-preview',
}

// Default page size for the API
export const DEFAULT_PAGE_SIZE = 20;

// Session storage keys
export const APP_STORAGE_PREFIX = "messages_";
export const SESSION_EXPIRED_KEY = APP_STORAGE_PREFIX + "session_expired";
