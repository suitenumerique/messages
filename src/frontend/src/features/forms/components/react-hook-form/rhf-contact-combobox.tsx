import { useContactsList } from "@/features/api/gen";
import { ComboBox, ComboBoxProps } from "../combobox";
import { useMemo, useState } from "react";
import { useMailboxContext } from "@/features/providers/mailbox";
import { UserRow } from "@gouvfr-lasuite/ui-components";
import { Controller, useFormContext } from "react-hook-form";
import MailHelper from "@/features/utils/mail-helper";
import clsx from "clsx";

type RhfContactComboBoxProps = Omit<ComboBoxProps, 'options'> & {
    name: string;
    // Colours the field footer as a warning (Cunningham's Field only knows
    // error / success); the items themselves come through `textItems`.
    warning?: boolean;
};

export const RhfContactComboBox = ({ warning = false, ...props }: RhfContactComboBoxProps) => {
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
            return contacts.filter(contact => contact.name?.toLowerCase().includes(searchQuery.toLowerCase()) || MailHelper.asciiLower(contact.email).includes(MailHelper.asciiLower(searchQuery)));
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
            render={({ field, fieldState }) => {
                // A caller-provided error state (e.g. a limit computed across
                // several fields) must win over the field's own validation.
                const hasError = !!fieldState.error || props.state === "error";
                return (
                <ComboBox
                    {...field}
                    {...props}
                    className={clsx(props.className, { "c__combobox--warning": warning })}
                    clearable
                    state={hasError ? "error" : "default"}
                    aria-invalid={hasError}
                    value={field.value}
                    valueValidator={MailHelper.isValidEmail}
                    valueTransformer={MailHelper.normalizeEmailDomain.bind(MailHelper)}
                    onChange={(value) => setValue(props.name, value, { shouldDirty: true })}
                    onInputChange={(value) => setSearchQuery(value.trim())}
                    options={contactsOptions}
                />
                );
            }}
        />
    )
}
