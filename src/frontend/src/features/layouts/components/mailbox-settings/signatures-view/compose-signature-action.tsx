import { Button } from "@gouvfr-lasuite/cunningham-react";
import { Icon } from "@gouvfr-lasuite/ui-kit";
import { useTranslation } from "react-i18next";
import { useModal } from "@gouvfr-lasuite/cunningham-react";
import { ModalComposeMailboxSignature } from "../modal-compose-mailbox-signature";

export const ComposeSignatureAction = () => {
    const { t } = useTranslation();
    const modal = useModal();

    return (
        <>
            <Button
                onClick={() => modal.open()}
                icon={<Icon name="add" />}
            >
                {t("New signature")}
            </Button>
            <ModalComposeMailboxSignature
                isOpen={modal.isOpen}
                onClose={() => modal.close()}
            />
        </>
    );
};
