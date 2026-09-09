import { useState } from "react";
import { Button, Modal, ModalSize } from "@gouvfr-lasuite/cunningham-react";
import { useTranslation } from "react-i18next";
import { Spinner } from "@gouvfr-lasuite/ui-kit";

type CloseConfirmModalProps = {
    isOpen: boolean;
    onSave: () => Promise<void>;
    onDelete: () => Promise<void>;
    onCancel: () => void;
};

/**
 * Asked when closing a compose window holding a brand new draft: the user
 * chooses between keeping the draft for later or deleting it.
 */
export const CloseConfirmModal = ({ isOpen, onSave, onDelete, onCancel }: CloseConfirmModalProps) => {
    const { t } = useTranslation();
    const [pendingAction, setPendingAction] = useState<"save" | "delete" | null>(null);

    const runAction = (action: "save" | "delete", callback: () => Promise<void>) => async () => {
        setPendingAction(action);
        try {
            await callback();
        } finally {
            setPendingAction(null);
        }
    };

    return (
        <Modal
            isOpen={isOpen}
            onClose={onCancel}
            size={ModalSize.SMALL}
            title={t("Do you want to keep this draft?")}
        >
            <div className="compose-window-close-confirm">
                <p>{t("You can save it to finish it later, or delete it permanently.")}</p>
                <footer className="compose-window-close-confirm__actions">
                    <Button
                        type="button"
                        color="error"
                        variant="secondary"
                        onClick={runAction("delete", onDelete)}
                        disabled={!!pendingAction}
                        icon={pendingAction === "delete" ? <Spinner size="sm" /> : undefined}
                        fullWidth
                    >
                        {t("Delete draft")}
                    </Button>
                    <Button
                        type="button"
                        variant="secondary"
                        onClick={runAction("save", onSave)}
                        disabled={!!pendingAction}
                        icon={pendingAction === "save" ? <Spinner size="sm" /> : undefined}
                        fullWidth
                    >
                        {t("Save and close")}
                    </Button>
                </footer>
            </div>
        </Modal>
    );
};
