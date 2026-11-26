import { ContactSuggestion, getContactsListQueryKey, useContactsList, useContactsPartialUpdate } from "@/features/api/gen";
import { ComboBox, ComboBoxProps } from "../combobox";
import { useMemo, useState } from "react";
import { useMailboxContext } from "@/features/providers/mailbox";
import { Option, UserRow, useModals } from "@gouvfr-lasuite/ui-components";
import { Hide, Show } from "@gouvfr-lasuite/ui-components/icons";
import { Controller, useFormContext } from "react-hook-form";
import MailHelper from "@/features/utils/mail-helper";
import clsx from "clsx";
import { useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Icon } from "@/features/ui/components/icon";

type RhfContactComboBoxProps = Omit<ComboBoxProps, 'options'> & {
    name: string;
    // Colours the field footer as a warning (Cunningham's Field only knows
    // error / success); the items themselves come through `textItems`.
    warning?: boolean;
};

const CONTACTS_STALE_TIME = 5 * 60 * 1000;
const HIDDEN_SUGGESTIONS_LIMIT = 5;

const toOption = (contact: ContactSuggestion): Option => ({
    label: contact.email,
    value: contact.email,
    render: () => (
        <UserRow
            fullName={contact.name || undefined}
            email={contact.email}
        />
    ),
});

export const RhfContactComboBox = ({ warning = false, ...props }: RhfContactComboBoxProps) => {
    const { t } = useTranslation();
    const modals = useModals();
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
    const updateContactMutation = useContactsPartialUpdate({
        mutation: {
            onSuccess: () => {
                queryClient.invalidateQueries({ queryKey: getContactsListQueryKey({ mailbox_id: selectedMailbox?.id }) });
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

    const contactsOptions = useMemo(
        () => contacts.filter(contact => contact.is_suggested).map(toOption),
        [contacts]
    );

    // Hidden suggestions only show up when searched for, and the combobox
    // offers them only once no other suggestion matches.
    const hiddenOptions = useMemo(
        () => searchQuery
            ? contacts.filter(contact => !contact.is_suggested).slice(0, HIDDEN_SUGGESTIONS_LIMIT).map(toOption)
            : [],
        [contacts, searchQuery]
    );

    const contactsByEmail = useMemo(
        () => new Map(contacts.map(contact => [contact.email, contact])),
        [contacts]
    );

    const hideContact = async (contact: ContactSuggestion) => {
        const decision = await modals.confirmationModal({
            titleIcon: <Icon icon={Hide} />,
            title: t("Hide this suggestion?"),
            children: (
                <p>
                    {t("{{email}} will no longer be suggested to the people using {{mailbox}}. You will find it again by searching for it, and it will be suggested again if you write to it.", {
                        email: contact.email,
                        mailbox: selectedMailbox?.email,
                    })}
                </p>
            ),
        });
        if (decision !== "yes") return;
        updateContactMutation.mutate({ id: contact.id, data: { is_suggested: false } });
    };

    const getOptionAction: ComboBoxProps["getOptionAction"] = (option) => {
        const contact = option.value ? contactsByEmail.get(option.value) : undefined;
        if (!contact?.can_change_suggestion) return undefined;
        if (contact.is_suggested) {
            return {
                label: t("Hide from suggestions", { email: contact.email }),
                icon: <Icon icon={Hide} />,
                onAction: () => hideContact(contact),
            };
        }
        return {
            label: t("Show in suggestions", { email: contact.email }),
            icon: <Icon icon={Show} />,
            onAction: () => updateContactMutation.mutate({ id: contact.id, data: { is_suggested: true } }),
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
                    fallbackSection={{ title: t("Hidden suggestions"), options: hiddenOptions }}
                    getOptionAction={getOptionAction}
                />
                );
            }}
        />
    )
}
