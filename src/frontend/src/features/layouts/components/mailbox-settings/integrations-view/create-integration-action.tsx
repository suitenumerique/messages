import { Button } from "@gouvfr-lasuite/cunningham-react";
import { useResponsive } from "@gouvfr-lasuite/ui-kit";
import { Icon } from "@/features/ui/components/icon";
import { Plus } from "@gouvfr-lasuite/ui-kit/icons";
import { useTranslation } from "react-i18next";
import { useModal } from "@gouvfr-lasuite/cunningham-react";
import { Mailbox } from "@/features/api/gen";
import { ModalComposeIntegration } from "../modal-compose-integration";

type CreateIntegrationActionProps = {
    mailbox: Mailbox;
};

export const CreateIntegrationAction = ({ mailbox }: CreateIntegrationActionProps) => {
    const { t } = useTranslation();
    const modal = useModal();
    const { isMobile } = useResponsive();

    return (
        <>
            <Button
                size={isMobile ? "small" : "nano"}
                onClick={() => modal.open()}
                icon={<Icon icon={Plus} />}
                aria-label={isMobile ? t("New integration") : undefined}
            >
                {!isMobile && t("New integration")}
            </Button>
            <ModalComposeIntegration
                isOpen={modal.isOpen}
                onClose={() => modal.close()}
                mailbox={mailbox}
            />
        </>
    );
};
