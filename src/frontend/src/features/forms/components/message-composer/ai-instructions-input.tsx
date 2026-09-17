import { useTranslation } from "react-i18next";

// Keep in sync with ADDITIONAL_INSTRUCTIONS_MAX_CHARS in the backend ai_draft view.
export const AI_INSTRUCTIONS_MAX_LENGTH = 2000;

type AiInstructionsInputProps = {
    value: string;
    disabled?: boolean;
    onChange: (value: string) => void;
    onSubmit: () => void;
};

/**
 * Small inline field always shown next to the AI button in the composer toolbar.
 * The instructions typed here are applied to the next generated reply, which is
 * triggered by the AI button or by pressing Enter in the field.
 */
export const AiInstructionsInput = ({ value, disabled = false, onChange, onSubmit }: AiInstructionsInputProps) => {
    const { t } = useTranslation();

    const handleKeyDown = (event: React.KeyboardEvent<HTMLInputElement>) => {
        // Keep keystrokes (arrows, Enter…) away from the toolbar and the editor.
        event.stopPropagation();
        if (event.key === "Enter") {
            event.preventDefault();
            if (!disabled) onSubmit();
        } else if (event.key === "Escape") {
            event.preventDefault();
            onChange("");
        }
    };

    return (
        <div className="ai-instructions-input">
            <input
                type="text"
                className="ai-instructions-input__field"
                aria-label={t("Additional instructions for the Orgamind")}
                placeholder={t("Instructions for the Orgamind...")}
                value={value}
                onChange={(event) => onChange(event.target.value)}
                onKeyDown={handleKeyDown}
                maxLength={AI_INSTRUCTIONS_MAX_LENGTH}
                disabled={disabled}
                // The BlockNote toolbar runs a Mantine focus trap: when focus enters
                // the toolbar, it moves focus to its first tabbable element unless one
                // is marked with data-autofocus. Without it, the field loses focus
                // right after being clicked and nothing can be typed.
                data-autofocus
            />
        </div>
    );
};
