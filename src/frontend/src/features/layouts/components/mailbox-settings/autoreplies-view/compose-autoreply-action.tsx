import { Button } from "@gouvfr-lasuite/cunningham-react";
import { Icon } from "@gouvfr-lasuite/ui-kit";
import { useTranslation } from "react-i18next";
import { useModal } from "@gouvfr-lasuite/cunningham-react";
import { ModalComposeMailboxAutoreply } from "../modal-compose-mailbox-autoreply";

export const ComposeAutoreplyAction = () => {
    const { t } = useTranslation();
    const modal = useModal();

    return (
        <>
            <Button
                onClick={() => modal.open()}
                icon={<Icon name="add" />}
            >
                {t("New auto-reply")}
            </Button>
            <ModalComposeMailboxAutoreply
                isOpen={modal.isOpen}
                onClose={() => modal.close()}
            />
        </>
    );
};
