import { Modal, ModalProps } from "@openfun/cunningham-react";
import { useModalStore } from ".";

type ControlledModalProps = Omit<ModalProps, "isOpen" | "onClose"> & { modalId: string; onClose?: () => void }

/**
 * A controlled modal aims to work with the ModalStoreProvider to be controlled
 * anywhere in the app. It requires a modalId to sync its state from ModalStoreProvider.
 * 
 * Then the modal must be registered in the global store (take a look at global-store.ts)
 * 
 */
export const ControlledModal = ({ children, modalId, onClose, ...props }: ControlledModalProps) => {
    const { isModalOpen, closeModal } = useModalStore();
    const isOpen = isModalOpen(modalId);
    const handleClose = () => {
        closeModal(modalId);
        onClose?.();
    }

    return (
        <Modal {...props} isOpen={isOpen} onClose={handleClose}>
            {children}
        </Modal>
    )
}
