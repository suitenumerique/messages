import { Button, ButtonProps } from "@gouvfr-lasuite/cunningham-react";
import { Icon } from "@gouvfr-lasuite/ui-kit";
import { useTranslation } from "react-i18next";
import { useModal } from "@gouvfr-lasuite/cunningham-react";
import { Mailbox } from "@/features/api/gen";
import { ModalComposeMailboxSignature } from "../modal-compose-mailbox-signature";

type ComposeSignatureActionProps = {
    mailbox: Mailbox;
    size?: ButtonProps["size"];
};

export const ComposeSignatureAction = ({ mailbox, size }: ComposeSignatureActionProps) => {
    const { t } = useTranslation();
    const modal = useModal();

    return (
        <>
            <Button
                size={size}
                onClick={() => modal.open()}
                icon={<Icon name="add" />}
            >
                {t("New signature")}
            </Button>
            <ModalComposeMailboxSignature
                isOpen={modal.isOpen}
                onClose={() => modal.close()}
                mailbox={mailbox}
            />
        </>
    );
};
