import { createContext, PropsWithChildren, useContext, useEffect, useMemo, useState } from "react";
import { modalStore } from "./global-store";
import { useRouter } from "next/router";

type ModalStoreContextType = {
    openModal: (modalId: string) => void;
    closeModal: (modalId: string) => void;
    isModalOpen: (modalId: string) => boolean;
};

const ModalStoreContext = createContext<ModalStoreContextType>({
    openModal: () => {},
    closeModal: () => {},
    isModalOpen: () => false,
});

/**
 * This provider aims to manage state of all modals that should be opened
 * everywhere in the app.
 */
export const ModalStoreProvider = ({ children }: PropsWithChildren) => {
    const [openModals, setOpenModals] = useState<Set<string>>(new Set());
    const router = useRouter();

    const openModal = (modalId: string) => {
        setOpenModals((prev) => new Set([...prev, modalId]));
    };

    const closeModal = async (modalId: string) => {
        // Remove the modal hash from the url if needed
        if (window.location.hash.includes(`#${modalId}`)) {
            await router.push(router.asPath.split('#')[0])
        }
        setOpenModals((prev) => {
            const next = new Set([...prev]);
            next.delete(modalId);
            return next;
        });
    };

    const isModalOpen = (modalId: string) => {
        return openModals.has(modalId);
    };

    const contextValue = useMemo(() => ({
        openModal,
        closeModal,
        isModalOpen
    }), [modalStore, openModal, closeModal, isModalOpen]);

    /**
     * Listen for hash change to open the modal
     * if the location.hash contains a registered modal id
     */
    useEffect(() => {
        const handleHashChange = () => {
            const modalId = window.location.hash.replace('#', '');
            if (modalId && modalStore.has(modalId) && !isModalOpen(modalId)) {
                openModal(modalId);
            }
        }
        window.addEventListener('hashchange', handleHashChange);
        handleHashChange();

        return () => {
            window.removeEventListener('hashchange', handleHashChange);
        }
    }, []);

    return (
        <ModalStoreContext.Provider value={contextValue}>
            {children}
            {Array.from(modalStore.entries()).map(([modalId, Modal]) => (
                <Modal key={modalId} />
            ))}
        </ModalStoreContext.Provider>
    )
}

/**
 * The hook to consume the context of ModalStoreProvider.
 */
export const useModalStore = () => {
    const context = useContext(ModalStoreContext);

    if (!context) {
        throw new Error("useModalStore must be used within a ModalStoreProvider");
    }

    return context;
}

// Forward other useful stuff
export { ControlledModal } from "./controlled-modal";
export { registerModal } from "./global-store";
