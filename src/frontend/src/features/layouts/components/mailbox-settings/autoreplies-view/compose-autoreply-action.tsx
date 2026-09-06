import { Button } from "@gouvfr-lasuite/cunningham-react";
import { useResponsive } from "@gouvfr-lasuite/ui-kit";
import { useTranslation } from "react-i18next";
import { useModal } from "@gouvfr-lasuite/cunningham-react";
import { Mailbox } from "@/features/api/gen";
import { ModalComposeMailboxAutoreply } from "../modal-compose-mailbox-autoreply";
import { Icon } from "@/features/ui/components/icon";
import { Plus } from "@gouvfr-lasuite/ui-kit/icons";

type ComposeAutoreplyActionProps = { mailbox: Mailbox; };

export const ComposeAutoreplyAction = ({ mailbox }: ComposeAutoreplyActionProps) => {
    const { t } = useTranslation();
    const modal = useModal();
    const { isMobile } = useResponsive();

    return (
        <>
            <Button
                size={isMobile ? "small" : "nano"}
                onClick={() => modal.open()}
                icon={<Icon icon={Plus} />}
                aria-label={isMobile ? t("New auto-reply") : undefined}
            >
                {!isMobile && t("New auto-reply")}
            </Button>
            <ModalComposeMailboxAutoreply
                isOpen={modal.isOpen}
                onClose={() => modal.close()}
                mailbox={mailbox}
            />
        </>
    );
};
