import { Button, ButtonProps } from "@gouvfr-lasuite/cunningham-react";
import { Icon } from "@gouvfr-lasuite/ui-kit";
import { useTranslation } from "react-i18next";
import { useModal } from "@gouvfr-lasuite/cunningham-react";
import { Mailbox } from "@/features/api/gen";
import { ModalComposeMailboxAutoreply } from "../modal-compose-mailbox-autoreply";

type ComposeAutoreplyActionProps = {
    mailbox: Mailbox;
    size?: ButtonProps["size"];
};

export const ComposeAutoreplyAction = ({ mailbox, size }: ComposeAutoreplyActionProps) => {
    const { t } = useTranslation();
    const modal = useModal();

    return (
        <>
            <Button
                size={size}
                onClick={() => modal.open()}
                icon={<Icon name="add" />}
            >
                {t("New auto-reply")}
            </Button>
            <ModalComposeMailboxAutoreply
                isOpen={modal.isOpen}
                onClose={() => modal.close()}
                mailbox={mailbox}
            />
        </>
    );
};
