import { useFormContext } from "react-hook-form";
import { useTranslation } from "react-i18next";
import { RhfContactComboBox, RhfContactComboBoxProps } from "./rhf-contact-combobox";

type RecipientFieldName = 'to' | 'cc' | 'bcc';

export type RhfRecipientComboBoxProps = Omit<RhfContactComboBoxProps, 'name' | 'onValueChange'> & {
    name: RecipientFieldName;
    maxRecipients?: number;
};

/**
 * Specialized contact combobox for message recipient fields (to, cc, bcc).
 * Extends RhfContactComboBox with:
 * - maxRecipients display in helper text
 * - Cross-field validation triggering (to, cc, bcc)
 * - Error state from formState.errors for external setError reactivity
 */
export const RhfRecipientComboBox = (props: RhfRecipientComboBoxProps) => {
    const { name, maxRecipients, text, ...rest } = props;
    const { formState, trigger } = useFormContext();
    const { t } = useTranslation();

    const baseHelperText = maxRecipients
        ? t("Enter the email addresses of the recipients separated by commas, maximum {{max}} for all recipients (to + cc + bcc)", { max: maxRecipients })
        : t("Enter the email addresses of the recipients separated by commas");

    // Use formState.errors directly to ensure reactivity to external setError calls
    const fieldError = formState.errors[name];
    const errorMessage = fieldError?.message as string | undefined;

    return (
        <RhfContactComboBox
            {...rest}
            name={name}
            state={fieldError ? "error" : "default"}
            aria-invalid={!!fieldError}
            text={errorMessage || text || baseHelperText}
            onValueChange={(value, setValue) => {
                setValue(name, value, { shouldDirty: true, shouldValidate: true });
                // Trigger validation for other recipient fields to update their error state
                trigger(['to', 'cc', 'bcc']);
            }}
        />
    );
}
