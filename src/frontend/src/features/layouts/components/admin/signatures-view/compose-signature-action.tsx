import { Button, useModal } from "@openfun/cunningham-react";
import { ModalComposeSignature } from "../modal-compose-signature";
import { useTranslation } from "react-i18next";

export const ComposeSignatureAction = () => {
    const modal = useModal();
    const { t } = useTranslation();


    return (
        <>
            <Button color="primary" onClick={modal.open}>
                {t("New signature")}
            </Button>
            <ModalComposeSignature
                isOpen={modal.isOpen}
                onClose={modal.close}
            />
        </>
    )
};
