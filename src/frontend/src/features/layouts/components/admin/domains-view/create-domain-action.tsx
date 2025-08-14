import { MailDomainAdminWrite } from "@/features/api/gen";
import { ModalCreateDomain } from "@/features/layouts/components/admin/modal-create-domain";
import useAbility, { Abilities } from "@/hooks/use-ability";
import { Button, useModal } from "@openfun/cunningham-react";
import { useTranslation } from "react-i18next";

type CreateDomainActionProps = {
    onCreate: (createdDomain: MailDomainAdminWrite) => void;
}

/**
 * Action button to create a new domain.
 * Only visible if the user has the ability to manage domains.
 */
export const CreateDomainAction = ({ onCreate }: CreateDomainActionProps) => {
    const modal = useModal();
    const { t } = useTranslation();
    const canCreateDomains = useAbility(Abilities.CAN_CREATE_MAILDOMAINS);

    if (!canCreateDomains) {
        return null;
    }

    return (
        <>
            <Button color="primary" onClick={modal.open}>
                {t("admin_maildomains_list.actions.new_domain")}
            </Button>
            <ModalCreateDomain
                isOpen={modal.isOpen}
                onClose={modal.close}
                onCreate={onCreate}
            />
        </>
    )
}
