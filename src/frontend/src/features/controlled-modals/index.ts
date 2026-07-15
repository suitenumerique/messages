import { ControlledMailboxSettings } from "@/features/controlled-modals/mailbox-settings";
import { MODAL_MAILBOX_SETTINGS_ID } from "@/features/layouts/components/mailbox-settings/modal-mailbox-settings";
import { registerModal } from "../providers/modal-store";

// Imperatively register all controlled modals. (The message importer isn't one:
// it lives inside the mailbox settings modal's Imports tab — see useOpenImporter.)
registerModal(MODAL_MAILBOX_SETTINGS_ID, ControlledMailboxSettings);
