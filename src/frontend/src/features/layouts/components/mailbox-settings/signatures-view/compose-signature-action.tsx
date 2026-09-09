import { Button } from "@gouvfr-lasuite/cunningham-react";
import { useResponsive } from "@gouvfr-lasuite/ui-kit";
import { useTranslation } from "react-i18next";
import { useModal } from "@gouvfr-lasuite/cunningham-react";
import { Mailbox } from "@/features/api/gen";
import { ModalComposeMailboxSignature } from "../modal-compose-mailbox-signature";
import { Icon } from "@/features/ui/components/icon";
import { Plus } from "@gouvfr-lasuite/ui-kit/icons";

type ComposeSignatureActionProps = {
    mailbox: Mailbox;
};

export const ComposeSignatureAction = ({ mailbox }: ComposeSignatureActionProps) => {
    const { t } = useTranslation();
    const modal = useModal();
    const { isMobile } = useResponsive();

    return (
        <>
            <Button
                size={isMobile ? "small" : "nano"}
                onClick={() => modal.open()}
                icon={<Icon icon={Plus} />}
                aria-label={isMobile ? t("New signature") : undefined}
            >
                {!isMobile && t("New signature")}
            </Button>
            <ModalComposeMailboxSignature
                isOpen={modal.isOpen}
                onClose={() => modal.close()}
                mailbox={mailbox}
            />
        </>
    );
};
