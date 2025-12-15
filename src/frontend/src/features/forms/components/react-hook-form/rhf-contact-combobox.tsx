import { useContactsList } from "@/features/api/gen";
import { ComboBox, ComboBoxProps } from "../combobox";
import { useMemo, useState } from "react";
import { useMailboxContext } from "@/features/providers/mailbox";
import { UserRow } from "@gouvfr-lasuite/ui-kit";
import { Controller, useFormContext } from "react-hook-form";
import MailHelper from "@/features/utils/mail-helper";
import { useTranslation } from "react-i18next";

export const RhfContactComboBox = (props: Omit<ComboBoxProps, 'options'> & { name: string; maxRecipients?: number }) => {
    const { control, setValue, formState, trigger } = useFormContext();
    const [searchQuery, setSearchQuery] = useState("");
    const { selectedMailbox } = useMailboxContext();
    const MAX_RECIPIENTS_PER_MESSAGE = props.maxRecipients ?? selectedMailbox?.max_recipients_per_message;
    const { t } = useTranslation();
    const contactsQuery = useContactsList({ mailbox_id: selectedMailbox?.id }, {
        query: {
            enabled: !!selectedMailbox?.id,
        }
    });
    // MARK: Currently the contact list endpoint is not paginated, so we get the full list of contact
    // At first it is good as we are able to filter locally so we have a really good reactive UI
    // But I don't sure this strategy scale well with a lot of contacts
    const contacts = useMemo(
        () => {
            const contacts = contactsQuery.data?.data || [];
            if (!searchQuery) return contacts;
            return contacts.filter(contact => contact.name?.toLowerCase().includes(searchQuery.toLowerCase()) || contact.email.toLowerCase().includes(searchQuery.toLowerCase()));
        },
        [contactsQuery.data?.data, searchQuery]
    );

    const contactsOptions = useMemo(() => {
        if (!contacts) return [];
        return contacts.map(contact => ({
            label: contact.email,
            value: contact.email,
            render: () => (
                <UserRow
                    fullName={contact.name || undefined}
                    email={contact.email}
                />
            ),
        }));
    }, [contacts]);

    return (
        <Controller
            control={control}
            name={props.name}
            render={({ field }) => {
                // Use formState.errors directly to ensure reactivity to external setError calls
                const fieldError = formState.errors[props.name as keyof typeof formState.errors];
                const baseHelperText =
                    MAX_RECIPIENTS_PER_MESSAGE
                        ? t("Enter the email addresses of the recipients, maximum {{max}} for all recipients (to + cc + bcc)", { max: MAX_RECIPIENTS_PER_MESSAGE })
                        : t("Enter the email addresses of the recipients");

                return (
                    <ComboBox
                        {...field}
                        {...props}
                        clearable
                        state={fieldError ? "error" : "default"}
                        aria-invalid={!!fieldError}
                        value={field.value}
                        valueValidator={MailHelper.isValidEmail}
                        text={(fieldError?.message as string) || baseHelperText}
                        onChange={(value) => {
                            // Update the value of the field with validation
                            setValue(props.name, value, { shouldDirty: true, shouldValidate: true });
                            // Trigger validation for other recipient fields to update their error state
                            trigger(['to', 'cc', 'bcc']);
                        }}
                        onInputChange={(value) => setSearchQuery(value.trim())}
                        options={contactsOptions}
                    />
                );
            }}
        />
    )
}
