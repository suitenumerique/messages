import { Button } from "@gouvfr-lasuite/cunningham-react";
import { Icon } from "@gouvfr-lasuite/ui-kit";
import { useTranslation } from "react-i18next";
import { useModal } from "@gouvfr-lasuite/cunningham-react";
import { ModalComposeIntegration } from "../modal-compose-integration";

export const CreateIntegrationAction = () => {
    const { t } = useTranslation();
    const modal = useModal();

    return (
        <>
            <Button
                onClick={() => modal.open()}
                icon={<Icon name="add" />}
            >
                {t("New integration")}
            </Button>
            <ModalComposeIntegration
                isOpen={modal.isOpen}
                onClose={() => modal.close()}
            />
        </>
    );
};
