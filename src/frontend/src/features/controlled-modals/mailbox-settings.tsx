import {
  MODAL_MAILBOX_SETTINGS_ID,
  ModalMailboxSettings,
  SettingsTabId,
} from "@/features/layouts/components/mailbox-settings/modal-mailbox-settings";
import { useModalStore } from "@/features/providers/modal-store";

/**
 * Binds the mailbox settings modal to the global modal store. Kept separate from
 * the modal component on purpose: the component must never import the store, as
 * that back-edge would close an import cycle (modal-store → controlled-modals →
 * modal → store) and trip a temporal-dead-zone error on the modal id at
 * registration time.
 */
export const ControlledMailboxSettings = () => {
  const { isModalOpen, closeModal, getModalPayload } = useModalStore();
  const payload = getModalPayload(MODAL_MAILBOX_SETTINGS_ID) as
    | { initialTab?: SettingsTabId }
    | undefined;
  const initialTab = payload?.initialTab;

  return (
    <ModalMailboxSettings
      isOpen={isModalOpen(MODAL_MAILBOX_SETTINGS_ID)}
      onClose={() => closeModal(MODAL_MAILBOX_SETTINGS_ID)}
      initialTab={initialTab}
    />
  );
};
