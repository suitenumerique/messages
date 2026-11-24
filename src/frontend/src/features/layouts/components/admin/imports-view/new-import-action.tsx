import { Button, useModal } from "@gouvfr-lasuite/ui-components";
import { useTranslation } from "react-i18next";
import { ModalMailboxImport } from "@/features/layouts/components/admin/modal-mailbox-import";
import { useAdminMailDomain } from "@/features/providers/admin-maildomain";
import useAbility, { Abilities } from "@/hooks/use-ability";

/**
 * Starts an import into a mailbox of the domain, picked in the modal.
 * Only visible if the user can import into the domain mailboxes.
 */
export const NewImportAction = () => {
    const modal = useModal();
    const { t } = useTranslation();
    const { selectedMailDomain } = useAdminMailDomain();
    const canImport = useAbility(Abilities.CAN_IMPORT_MAILDOMAIN_MESSAGES, selectedMailDomain);

    if (!selectedMailDomain || !canImport) {
        return null;
    }

    return (
        <>
            <Button variant="primary" onClick={modal.open}>
                {t("New import")}
            </Button>
            <ModalMailboxImport
                isOpen={modal.isOpen}
                onClose={modal.close}
                domainId={selectedMailDomain.id}
            />
        </>
    );
};
