import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Button, Modal, ModalSize, TextArea } from "@gouvfr-lasuite/cunningham-react";

// Keep in sync with ADDITIONAL_INSTRUCTIONS_MAX_CHARS in the backend ai_draft view.
export const AI_INSTRUCTIONS_MAX_LENGTH = 2000;

type AiInstructionsModalProps = {
    isOpen: boolean;
    onClose: () => void;
    onSubmit: (additionalInstructions: string) => void;
};

/**
 * Asks the agent for extra instructions before regenerating an AI draft.
 * The instructions typed here are applied to the next generated reply.
 */
export const AiInstructionsModal = ({ isOpen, onClose, onSubmit }: AiInstructionsModalProps) => {
    const { t } = useTranslation();
    const [instructions, setInstructions] = useState("");
    const trimmedInstructions = instructions.trim();

    const handleClose = () => {
        setInstructions("");
        onClose();
    };

    const handleSubmit = () => {
        if (!trimmedInstructions) return;
        onSubmit(trimmedInstructions);
        setInstructions("");
    };

    return (
        <Modal
            isOpen={isOpen}
            onClose={handleClose}
            size={ModalSize.MEDIUM}
            title={t("Regenerate AI draft")}
            closeOnClickOutside
            rightActions={
                <>
                    <Button variant="secondary" size="small" onClick={handleClose}>
                        {t("Cancel")}
                    </Button>
                    <Button size="small" onClick={handleSubmit} disabled={!trimmedInstructions}>
                        {t("Regenerate")}
                    </Button>
                </>
            }
        >
            <TextArea
                label={t("Additional instructions")}
                text={t("Describe what the AI should change in the next draft, e.g. shorter, more formal, mention the deadline.")}
                value={instructions}
                onChange={(event) => setInstructions(event.target.value)}
                onKeyDown={(event) => {
                    if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) {
                        event.preventDefault();
                        handleSubmit();
                    }
                }}
                maxLength={AI_INSTRUCTIONS_MAX_LENGTH}
                rows={5}
                autoFocus
                fullWidth
            />
        </Modal>
    );
};
