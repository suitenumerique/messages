import { useState } from "react";
import { useTranslation } from "react-i18next";
import { keepPreviousData, useQueryClient } from "@tanstack/react-query";
import { Modal, ModalSize, Select, useModals } from "@gouvfr-lasuite/ui-components";
import {
    getMailboxesImportsListQueryKey,
    getMaildomainsImportsListQueryKey,
    getMaildomainsImportsStatsRetrieveQueryKey,
    useMaildomainsMailboxesList,
} from "@/features/api/gen";
import { ImportNewView } from "@/features/layouts/components/mailbox-settings/imports-view/import-new-view";
import { addToast, ToasterItem } from "@/features/ui/components/toaster";
import MailboxHelper from "@/features/utils/mailbox-helper";
import { useDebounceCallback } from "@/hooks/use-debounce-callback";

type ImportTarget = {
    id: string;
    email: string;
};

type ModalMailboxImportProps = {
    isOpen: boolean;
    onClose: () => void;
    domainId: string;
    /** The mailbox to import into. Without it, the admin picks one of the domain. */
    mailbox?: ImportTarget;
};

const SEARCH_DEBOUNCE_MS = 300;

type DomainMailboxSelectProps = {
    domainId: string;
    value: ImportTarget | null;
    onChange: (mailbox: ImportTarget | null) => void;
    disabled: boolean;
};

/** Picks a mailbox of the domain, searched server-side as the admin types. */
const DomainMailboxSelect = ({ domainId, value, onChange, disabled }: DomainMailboxSelectProps) => {
    const { t } = useTranslation();
    const [query, setQuery] = useState("");
    const setDebouncedQuery = useDebounceCallback(setQuery, SEARCH_DEBOUNCE_MS);
    const { data } = useMaildomainsMailboxesList(
        domainId,
        query ? { q: query } : undefined,
        { query: { placeholderData: keepPreviousData } },
    );
    const results = (data?.data.results ?? []).map((mailbox) => ({
        id: mailbox.id,
        email: MailboxHelper.toString(mailbox),
    }));
    // The selection must stay among the options even once a new search no
    // longer returns it, or the select would show it as empty.
    const options = value && !results.some((mailbox) => mailbox.id === value.id)
        ? [value, ...results]
        : results;

    return (
        <Select
            label={t("Mailbox to import into")}
            value={value?.id ?? ""}
            onChange={(event) => {
                const id = event.target.value;
                onChange(options.find((mailbox) => mailbox.id === id) ?? null);
            }}
            onSearchInputChange={(event) => setDebouncedQuery((event.target.value ?? "").trim())}
            options={options.map((mailbox) => ({ value: mailbox.id, label: mailbox.email }))}
            searchable
            disabled={disabled}
            fullWidth
        />
    );
};

/**
 * Starts an import into a mailbox of the domain on behalf of a domain admin,
 * who needs no access to the mailbox itself.
 */
export const ModalMailboxImport = ({ isOpen, onClose, domainId, mailbox }: ModalMailboxImportProps) => {
    const { t } = useTranslation();
    const modals = useModals();
    const queryClient = useQueryClient();
    const [pickedMailbox, setPickedMailbox] = useState<ImportTarget | null>(null);
    const [isUploading, setIsUploading] = useState(false);
    const target = mailbox ?? pickedMailbox;

    const reset = () => {
        setPickedMailbox(null);
        setIsUploading(false);
    };

    // Closing unmounts the form, which aborts an archive still uploading.
    const handleClose = async () => {
        if (isUploading) {
            const decision = await modals.confirmationModal({
                title: t("Abort the upload?"),
                children: t("The archive is still uploading. Closing this window aborts the upload and the import will not start."),
            });
            if (decision !== "yes") return;
        }
        reset();
        onClose();
    };

    const handleStarted = async () => {
        if (!target) return;
        reset();
        onClose();
        addToast(
            <ToasterItem type="info">
                <span>
                    {t("Import into {{mailbox}} started. It keeps running in the background.", { mailbox: target.email })}
                </span>
            </ToasterItem>,
        );
        await Promise.all([
            queryClient.invalidateQueries({ queryKey: getMaildomainsImportsListQueryKey(domainId), exact: false }),
            queryClient.invalidateQueries({ queryKey: getMaildomainsImportsStatsRetrieveQueryKey(domainId) }),
            queryClient.invalidateQueries({ queryKey: getMailboxesImportsListQueryKey(target.id), exact: false }),
        ]);
    };

    return (
        <Modal
            isOpen={isOpen}
            onClose={handleClose}
            size={ModalSize.MEDIUM}
            title={mailbox ? t("Import messages into {{mailbox}}", { mailbox: mailbox.email }) : t("New import")}
        >
            <div className="modal-mailbox-import">
                {!mailbox && (
                    <DomainMailboxSelect
                        domainId={domainId}
                        value={pickedMailbox}
                        onChange={setPickedMailbox}
                        disabled={isUploading}
                    />
                )}
                {target && (
                    <ImportNewView
                        // A new mailbox gets a fresh form, never the previous one's state.
                        key={target.id}
                        mailboxId={target.id}
                        onStarted={handleStarted}
                        onUploadingChange={setIsUploading}
                    />
                )}
            </div>
        </Modal>
    );
};
