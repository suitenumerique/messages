import {
  MODAL_NOTIFICATIONS_ID,
  ModalNotifications,
} from "@/features/layouts/components/notifications-settings/modal-notifications";
import { useModalStore } from "@/features/providers/modal-store";

/**
 * Binds the account-level notifications modal to the global modal store. Kept
 * separate from the modal component on purpose: the component must never import
 * the store, as that back-edge would close an import cycle (modal-store →
 * controlled-modals → modal → store) and trip a temporal-dead-zone error on the
 * modal id at registration time.
 */
export const ControlledNotifications = () => {
  const { isModalOpen, closeModal } = useModalStore();

  return (
    <ModalNotifications
      isOpen={isModalOpen(MODAL_NOTIFICATIONS_ID)}
      onClose={() => closeModal(MODAL_NOTIFICATIONS_ID)}
    />
  );
};
