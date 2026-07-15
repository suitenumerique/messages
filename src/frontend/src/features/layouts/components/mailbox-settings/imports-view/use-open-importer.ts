import { useModalStore } from "@/features/providers/modal-store";
import { MODAL_MAILBOX_SETTINGS_ID } from "../modal-mailbox-settings";

/**
 * Open the mailbox settings modal straight onto the Imports tab's new-import
 * sub-view. Every "start an import" entry point (empty inbox, mailbox page,
 * header menu) routes here, so the importer flow always lives inside settings.
 *
 * Safe to call from anywhere *except* inside the settings-modal subtree itself
 * (that back-edge would close the modal-store ↔ controlled-modals import cycle);
 * the Imports tab's own "New import" button flips local state instead.
 */
export const useOpenImporter = () => {
  const { openModal } = useModalStore();
  return () =>
    openModal(MODAL_MAILBOX_SETTINGS_ID, {
      initialTab: "imports",
      initialImportsView: "new",
    });
};
