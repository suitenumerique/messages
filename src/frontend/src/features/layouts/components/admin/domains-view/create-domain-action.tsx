import { MailDomainAdminWrite } from "@/features/api/gen";
import { ModalCreateDomain } from "@/features/layouts/components/admin/modal-create-domain";
import useAbility, { Abilities } from "@/hooks/use-ability";
import { FEATURE_KEYS, useFeatureFlag } from "@/hooks/use-feature";
import { Button, useModal } from "@gouvfr-lasuite/cunningham-react";
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
    const isFeatureEnabled = useFeatureFlag(FEATURE_KEYS.MAILDOMAIN_CREATE);

    if (!canCreateDomains || !isFeatureEnabled) {
        return null;
    }

    return (
        <>
            <Button onClick={modal.open}>
                {t("New domain")}
            </Button>
            <ModalCreateDomain
                isOpen={modal.isOpen}
                onClose={modal.close}
                onCreate={onCreate}
            />
        </>
    )
}
