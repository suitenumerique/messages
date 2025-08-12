import { ModalCreateAddress } from "@/features/layouts/components/admin/modal-create-address";
import { useAdminMailDomain } from "@/features/providers/admin-maildomain";
import useAbility, { Abilities } from "@/hooks/use-ability";
import { Button, useModal } from "@openfun/cunningham-react";
import { useTranslation } from "react-i18next";

type CreateMailboxActionProps = {
    onCreate: () => void;
}

/**
 * Action button to create a new mailbox.
 * Only visible if the user has the ability to manage mailboxes.
 */
export const CreateMailboxAction = ({ onCreate }: CreateMailboxActionProps) => {
    const modal = useModal();
    const { t } = useTranslation();
    const { selectedMailDomain } = useAdminMailDomain();
    const canManageMailboxes = useAbility(Abilities.CAN_MANAGE_MAILDOMAIN_MAILBOXES, selectedMailDomain);

    if (!canManageMailboxes) {
        return null;
    }

    return (
        <>
            <Button color="primary" onClick={modal.open}>
                {t("admin_maildomains_details.actions.new_address")}
            </Button>
            <ModalCreateAddress
                isOpen={modal.isOpen}
                onClose={modal.close}
                onCreate={onCreate}
            />
        </>
    )
}
