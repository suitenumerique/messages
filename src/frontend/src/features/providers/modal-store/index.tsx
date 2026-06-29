import { createContext, PropsWithChildren, useContext, useEffect, useState } from "react";
import { modalStore } from "./global-store";
import { useNavigate } from "@tanstack/react-router";

type ModalStoreContextType = {
    openModal: (modalId: string, payload?: unknown) => void;
    closeModal: (modalId: string) => void;
    isModalOpen: (modalId: string) => boolean;
    getModalPayload: (modalId: string) => unknown;
};

const ModalStoreContext = createContext<ModalStoreContextType>({
    openModal: () => {},
    closeModal: () => {},
    isModalOpen: () => false,
    getModalPayload: () => undefined,
});

/**
 * This provider aims to manage state of all modals that should be opened
 * everywhere in the app.
 */
export const ModalStoreProvider = ({ children }: PropsWithChildren) => {
    const [openModals, setOpenModals] = useState<Set<string>>(new Set());
    const navigate = useNavigate();
    // Optional per-modal payload (e.g. the tab to preselect) handed over at open
    // time and read back by the controlled modal wrapper.
    const [modalPayloads, setModalPayloads] = useState<Record<string, unknown>>({});

    const openModal = (modalId: string, payload?: unknown) => {
        setModalPayloads((prev) => ({ ...prev, [modalId]: payload }));
        setOpenModals((prev) => new Set([...prev, modalId]));
    };

    const getModalPayload = (modalId: string) => modalPayloads[modalId];

    const closeModal = async (modalId: string) => {
        // Remove the modal hash from the url if needed, keeping the current
        // route and its search params untouched.
        if (window.location.hash.includes(`#${modalId}`)) {
            await navigate({ to: ".", search: (prev) => prev });
        }
        // Remove the modal hash from the localStorage if needed
        if (localStorage.getItem('openControlledModal') === modalId) {
            localStorage.removeItem('openControlledModal');
        }
        setOpenModals((prev) => {
            const next = new Set([...prev]);
            next.delete(modalId);
            return next;
        });
        setModalPayloads((prev) => {
            const next = { ...prev };
            delete next[modalId];
            return next;
        });
    };

    const isModalOpen = (modalId: string) => {
        return openModals.has(modalId);
    };

    const contextValue = {
        openModal,
        closeModal,
        isModalOpen,
        getModalPayload
    };

    /**
     * Listen for hash change to open the modal
     * if the location.hash contains a registered modal id
     */
    useEffect(() => {
        const handleHashChange = () => {
            const modalId = window.location.hash.replace('#', '') || localStorage.getItem('openControlledModal');
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
                openModals.has(modalId) && <Modal key={modalId} />
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

// Imperatively register all controlled modals
import "@/features/controlled-modals";
