import { ModalMessageImporter, MODAL_MESSAGE_IMPORTER_ID } from "@/features/controlled-modals/message-importer";
import { registerModal } from "../providers/modal-store";

// Imperatively register all controlled modals
registerModal(MODAL_MESSAGE_IMPORTER_ID, ModalMessageImporter);
