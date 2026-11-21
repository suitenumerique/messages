import { getContactsListQueryKey, useContactsDestroy, useContactsList } from "@/features/api/gen";
import { ComboBox, ComboBoxProps } from "../combobox";
import { useMemo, useState } from "react";
import { useMailboxContext } from "@/features/providers/mailbox";
import { UserRow } from "@gouvfr-lasuite/ui-components";
import { Controller, useFormContext } from "react-hook-form";
import MailHelper from "@/features/utils/mail-helper";
import clsx from "clsx";
import { useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { addToast, ToasterItem } from "@/features/ui/components/toaster";

type RhfContactComboBoxProps = Omit<ComboBoxProps, 'options'> & {
    name: string;
    // Colours the field footer as a warning (Cunningham's Field only knows
    // error / success); the items themselves come through `textItems`.
    warning?: boolean;
};

const CONTACTS_STALE_TIME = 5 * 60 * 1000;

export const RhfContactComboBox = ({ warning = false, ...props }: RhfContactComboBoxProps) => {
    const { t } = useTranslation();
    const { control, setValue } = useFormContext();
    const [searchQuery, setSearchQuery] = useState("");
    const { selectedMailbox } = useMailboxContext();
    const queryClient = useQueryClient();
    const contactsQuery = useContactsList({ mailbox_id: selectedMailbox?.id }, {
        query: {
            enabled: !!selectedMailbox?.id,
            // The whole list is fetched at once, so it is not worth refetching
            // on every composer opening or tab focus. Sending a message, the
            // only way to add a suggestion, invalidates it.
            staleTime: CONTACTS_STALE_TIME,
        }
    });
    const hideContactMutation = useContactsDestroy({
        mutation: {
            onSuccess: () => {
                queryClient.invalidateQueries({ queryKey: getContactsListQueryKey({ mailbox_id: selectedMailbox?.id }) });
                addToast(<ToasterItem type="info"><span>{t("Contact removed from suggestions.")}</span></ToasterItem>);
            },
        },
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

    const contactsByEmail = useMemo(
        () => new Map(contacts.map(contact => [contact.email, contact])),
        [contacts]
    );

    const getOptionRemoval: ComboBoxProps["getOptionRemoval"] = (option) => {
        const contact = option.value ? contactsByEmail.get(option.value) : undefined;
        if (!contact?.can_hide) return undefined;
        return {
            label: t("Remove {{email}} from suggestions", { email: contact.email }),
            onRemove: () => hideContactMutation.mutate({ id: contact.id }),
        };
    };

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
                    getOptionRemoval={getOptionRemoval}
                />
                );
            }}
        />
    )
}
