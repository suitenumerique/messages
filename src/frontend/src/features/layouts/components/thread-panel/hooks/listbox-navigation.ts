/**
 * Pure keyboard-navigation logic for the thread listbox.
 * Kept framework-free so edge clamping can be unit-tested without React.
 */

type ThreadRef = { id: string };

export type ListboxNavKey = 'ArrowUp' | 'ArrowDown' | 'Home' | 'End';

export const LISTBOX_NAV_KEYS: ReadonlyArray<ListboxNavKey> = ['ArrowUp', 'ArrowDown', 'Home', 'End'];

export const isListboxNavKey = (key: string): key is ListboxNavKey =>
    (LISTBOX_NAV_KEYS as ReadonlyArray<string>).includes(key);

/**
 * Resolve the thread that should receive focus after a navigation key.
 * Arrow keys clamp at the list edges (no wrap-around).
 * @returns the id of the thread to focus, or null when the list is empty
 */
export const getNextFocusId = (
    threads: ThreadRef[],
    currentId: string | null,
    key: ListboxNavKey,
): string | null => {
    if (threads.length === 0) return null;
    if (key === 'Home') return threads[0].id;
    if (key === 'End') return threads[threads.length - 1].id;

    const currentIndex = currentId ? threads.findIndex((thread) => thread.id === currentId) : -1;
    if (currentIndex === -1) return threads[0].id;

    const nextIndex = key === 'ArrowUp'
        ? Math.max(0, currentIndex - 1)
        : Math.min(threads.length - 1, currentIndex + 1);
    return threads[nextIndex].id;
};
