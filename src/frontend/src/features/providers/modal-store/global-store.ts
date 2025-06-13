import { ReactElement } from "react";

/**
 * A global store to register all the modals controlled by the ModalStoreProvider.
 * To register a modal, use the `registerModal` function.
 */
export const modalStore = new Map<string, () => ReactElement>();

/**
 * Register a modal controlled by the ModalStoreProvider.
 * Provide a modal id and the associated Modal component to be rendered.
 */
export const registerModal = (modalId: string, Modal: () => ReactElement) => {
    if (modalStore.has(modalId)) {
        throw new Error(`Modal with id ${modalId} already registered.`);
    }

    modalStore.set(modalId, Modal);
}
