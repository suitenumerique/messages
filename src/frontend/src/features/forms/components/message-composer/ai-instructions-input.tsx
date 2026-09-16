import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useComponentsContext } from "@blocknote/react";
import { Icon, IconSize } from "@gouvfr-lasuite/ui-kit";

// Keep in sync with ADDITIONAL_INSTRUCTIONS_MAX_CHARS in the backend ai_draft view.
export const AI_INSTRUCTIONS_MAX_LENGTH = 2000;

type AiInstructionsInputProps = {
    onClose: () => void;
    onSubmit: (additionalInstructions: string) => void;
};

/**
 * Small inline field shown next to the AI button in the composer toolbar.
 * The instructions typed here are applied to the next generated reply.
 */
export const AiInstructionsInput = ({ onClose, onSubmit }: AiInstructionsInputProps) => {
    const { t } = useTranslation();
    const Components = useComponentsContext()!;
    const [instructions, setInstructions] = useState("");
    const trimmedInstructions = instructions.trim();

    const handleSubmit = () => {
        if (!trimmedInstructions) return;
        onSubmit(trimmedInstructions);
    };

    const handleKeyDown = (event: React.KeyboardEvent<HTMLInputElement>) => {
        // Keep keystrokes (arrows, Enter…) away from the toolbar and the editor.
        event.stopPropagation();
        if (event.key === "Enter") {
            event.preventDefault();
            handleSubmit();
        } else if (event.key === "Escape") {
            event.preventDefault();
            onClose();
        }
    };

    return (
        <div className="ai-instructions-input">
            <input
                type="text"
                className="ai-instructions-input__field"
                aria-label={t("Additional instructions for the Orgamind")}
                placeholder={t("Instructions for the Orgamind...")}
                value={instructions}
                onChange={(event) => setInstructions(event.target.value)}
                onKeyDown={handleKeyDown}
                maxLength={AI_INSTRUCTIONS_MAX_LENGTH}
                autoFocus
                // The BlockNote toolbar runs a Mantine focus trap: when focus enters
                // the toolbar, it moves focus to its first tabbable element unless one
                // is marked with data-autofocus. Without it, the field loses focus
                // right after being clicked and nothing can be typed.
                data-autofocus
            />
            <Components.FormattingToolbar.Button
                icon={<Icon name="send" size={IconSize.SMALL} />}
                label={t("Regenerate")}
                mainTooltip={t("Regenerate Orgamind draft")}
                isDisabled={!trimmedInstructions}
                onClick={handleSubmit}
            />
        </div>
    );
};
