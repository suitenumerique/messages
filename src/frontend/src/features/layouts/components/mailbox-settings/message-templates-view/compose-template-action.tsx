import { Button, ButtonProps } from "@gouvfr-lasuite/cunningham-react";
import { Icon } from "@gouvfr-lasuite/ui-kit";
import { useTranslation } from "react-i18next";
import { useModal } from "@gouvfr-lasuite/cunningham-react";
import { Mailbox } from "@/features/api/gen";
import { ModalComposeTemplate } from "../modal-compose-template";

type ComposeTemplateActionProps = {
    mailbox: Mailbox;
    size?: ButtonProps["size"];
};

export const ComposeTemplateAction = ({ mailbox, size }: ComposeTemplateActionProps) => {
    const { t } = useTranslation();
    const modal = useModal();

    return (
        <>
            <Button
                size={size}
                onClick={() => modal.open()}
                icon={<Icon name="add" />}
            >
                {t("New template")}
            </Button>
            <ModalComposeTemplate
                isOpen={modal.isOpen}
                onClose={() => modal.close()}
                mailbox={mailbox}
            />
        </>
    );
};
