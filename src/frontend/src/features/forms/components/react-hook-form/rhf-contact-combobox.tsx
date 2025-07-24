import { useContactsList } from "@/features/api/gen";
import { ComboBox, ComboBoxProps } from "../combobox";
import { useMemo, useState } from "react";
import { useMailboxContext } from "@/features/providers/mailbox";
import { UserRow } from "@gouvfr-lasuite/ui-kit";
import { Controller, useFormContext } from "react-hook-form";
import MailHelper from "@/features/utils/mail-helper";

export const RhfContactComboBox = (props: Omit<ComboBoxProps, 'options'> & { name: string }) => {
    const { control, setValue } = useFormContext();
    const [searchQuery, setSearchQuery] = useState("");
    const { selectedMailbox } = useMailboxContext();
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
            render={({ field, fieldState }) => (
                <ComboBox
                    {...field}
                    {...props}
                    clearable
                    state={fieldState.error ? "error" : "default"}
                    aria-invalid={!!fieldState.error}
                    value={field.value}
                    valueValidator={MailHelper.isValidEmail}
                    onChange={(value) => setValue(props.name, value, { shouldDirty: true })}
                    onInputChange={(value) => setSearchQuery(value.trim())}
                    options={contactsOptions}
                />
            )}
        />
    )
}
