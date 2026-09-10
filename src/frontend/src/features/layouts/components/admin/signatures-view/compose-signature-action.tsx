import { Button, useModal } from "@gouvfr-lasuite/ui-components";
import { ModalComposeSignature } from "../modal-compose-signature";
import { useTranslation } from "react-i18next";

export const ComposeSignatureAction = () => {
    const modal = useModal();
    const { t } = useTranslation();


    return (
        <>
            <Button variant="primary" onClick={modal.open}>
                {t("New signature")}
            </Button>
            <ModalComposeSignature
                isOpen={modal.isOpen}
                onClose={modal.close}
            />
        </>
    )
};
