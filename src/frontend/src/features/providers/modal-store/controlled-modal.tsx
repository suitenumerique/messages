import { Modal, ModalDefaultVariantProps } from "@gouvfr-lasuite/ui-components";
import { useModalStore } from ".";

type ControlledModalProps = Omit<ModalDefaultVariantProps, "isOpen" | "onClose"> & { modalId: string; onClose?: () => void, confirmFn?: () => Promise<boolean> }

/**
 * A controlled modal aims to work with the ModalStoreProvider to be controlled
 * anywhere in the app. It requires a modalId to sync its state from ModalStoreProvider.
 *
 * Then the modal must be registered in the global store (take a look at global-store.ts)
 *
 */
export const ControlledModal = ({ children, modalId, onClose, confirmFn, ...props }: ControlledModalProps) => {
    const { isModalOpen, closeModal } = useModalStore();
    const isOpen = isModalOpen(modalId);
    const handleClose = async() => {
        if (confirmFn) {
            const decision = await confirmFn();
            if (!decision) return;
        }
        onClose?.();
        closeModal(modalId);
    }

    return (
        <Modal {...props} isOpen={isOpen} onClose={handleClose}>
            {children}
        </Modal>
    )
}
