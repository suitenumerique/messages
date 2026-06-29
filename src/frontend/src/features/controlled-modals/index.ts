import { ModalMessageImporter, MODAL_MESSAGE_IMPORTER_ID } from "@/features/controlled-modals/message-importer";
import { ControlledMailboxSettings } from "@/features/controlled-modals/mailbox-settings";
import { MODAL_MAILBOX_SETTINGS_ID } from "@/features/layouts/components/mailbox-settings/modal-mailbox-settings";
import { registerModal } from "../providers/modal-store";

// Imperatively register all controlled modals
registerModal(MODAL_MESSAGE_IMPORTER_ID, ModalMessageImporter);
registerModal(MODAL_MAILBOX_SETTINGS_ID, ControlledMailboxSettings);
