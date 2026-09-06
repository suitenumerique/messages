import { Button } from "@gouvfr-lasuite/cunningham-react";
import { Icon } from "@/features/ui/components/icon";
import { Plus } from "@gouvfr-lasuite/ui-kit/icons";
import { useTranslation } from "react-i18next";
import { useModal } from "@gouvfr-lasuite/cunningham-react";
import { Mailbox } from "@/features/api/gen";
import { ModalComposeTemplate } from "../modal-compose-template";
import { useResponsive } from "@gouvfr-lasuite/ui-kit";

type ComposeTemplateActionProps = {
    mailbox: Mailbox;
};

export const ComposeTemplateAction = ({ mailbox }: ComposeTemplateActionProps) => {
    const { t } = useTranslation();
    const modal = useModal();
    const { isMobile } = useResponsive();

    return (
        <>
            <Button
                size={isMobile ? "small" : "nano"}
                onClick={() => modal.open()}
                icon={<Icon icon={Plus} />}
                aria-label={isMobile ? t("New template") : undefined}
            >
                {!isMobile && t("New template")}
            </Button>
            <ModalComposeTemplate
                isOpen={modal.isOpen}
                onClose={() => modal.close()}
                mailbox={mailbox}
            />
        </>
    );
};
