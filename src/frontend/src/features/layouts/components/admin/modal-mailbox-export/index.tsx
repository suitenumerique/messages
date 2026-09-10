import { MailboxAdminExportResponse, useMailboxesList, useMaildomainsMailboxesExport } from "@/features/api/gen";
import { MailboxAdmin } from "@/features/api/gen/models/mailbox_admin";
import { APIError, errorToString } from "@/features/api/api-error";
import { Banner } from "@/features/ui/components/banner";
import { addToast, ToasterItem } from "@/features/ui/components/toaster";
import MailboxHelper from "@/features/utils/mailbox-helper";
import { Icon, IconSize, Spinner } from "@gouvfr-lasuite/ui-kit";
import { Button, Modal, ModalSize, Select } from "@gouvfr-lasuite/cunningham-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";

type ModalMailboxExportProps = {
    isOpen: boolean;
    onClose: () => void;
    mailbox: MailboxAdmin;
    domainId: string;
}

const ModalMailboxExport = ({ isOpen, onClose, mailbox, domainId }: ModalMailboxExportProps) => {
    const { t } = useTranslation();
    const email = MailboxHelper.toString(mailbox);
    // Mailboxes the requester can access: the download link is delivered
    // to the one they pick here, never to a default.
    const { data: mailboxesData, isLoading } = useMailboxesList({
        query: { enabled: isOpen },
    });
    const mailboxes = mailboxesData?.data ?? [];
    const [destinationId, setDestinationId] = useState<string>("");
    // Default to the first accessible mailbox without a synchronizing effect:
    // an explicit pick sticks, otherwise the first option wins.
    const selectedId = destinationId || mailboxes[0]?.id || "";
    const handleClose = () => {
        setDestinationId("");
        onClose();
    };
    // The export runs in a worker and mails the download link to the chosen
    // mailbox, so a failure needs the backend's own wording rather than the
    // generic error banner.
    const exportMailboxMutation = useMaildomainsMailboxesExport({
        mutation: { meta: { noGlobalError: true } },
    });

    const handleExport = () => {
        if (!selectedId || exportMailboxMutation.isPending) return;
        exportMailboxMutation.mutate(
            { maildomainPk: domainId, id: mailbox.id, data: { recipient_mailbox_id: selectedId } },
            {
                onSuccess: (response) => {
                    handleClose();
                    addToast(
                        <ToasterItem>
                            <Icon name="file_download" size={IconSize.SMALL} />
                            <span>{t('Export of {{mailbox}} started. The download link will be sent to {{recipient}}.', { mailbox: email, recipient: (response.data as MailboxAdminExportResponse).recipient })}</span>
                        </ToasterItem>
                    );
                },
                onError: (error) => {
                    addToast(
                        <ToasterItem type="error">
                            <Icon name="file_download" size={IconSize.SMALL} />
                            <span>{error instanceof APIError && error.data
                                ? errorToString(error)
                                : t('An error occurred while exporting {{mailbox}}.', { mailbox: email })}</span>
                        </ToasterItem>
                    );
                },
            }
        );
    }

    return (
        <Modal
            isOpen={isOpen}
            title={t('Export {{mailbox}}', { mailbox: email })}
            size={ModalSize.MEDIUM}
            onClose={handleClose}
        >
            <p>{t('All the messages of this mailbox will be exported to an MBOX archive. Choose the mailbox that will receive the download link once the export is ready.')}</p>
            {isLoading && <Spinner />}
            {!isLoading && mailboxes.length === 0 &&
                <Banner type="error">{t('You have no mailbox to receive the export link. Ask for access to a mailbox before exporting.')}</Banner>
            }
            {!isLoading && mailboxes.length > 0 &&
                <Select
                    label={t('Mailbox receiving the download link')}
                    value={selectedId}
                    onChange={(event) => setDestinationId(event.target.value as string)}
                    options={mailboxes.map((mailbox) => ({
                        value: mailbox.id,
                        label: mailbox.email,
                    }))}
                    fullWidth
                />
            }
            <footer style={{ display: "flex", justifyContent: "flex-end", gap: "var(--c--globals--spacings--xs)", marginTop: "var(--c--globals--spacings--md)" }}>
                <Button
                    variant="secondary"
                    onClick={handleClose}
                    disabled={exportMailboxMutation.isPending}
                    icon={exportMailboxMutation.isPending && <Spinner />}
                >
                    {t('Cancel')}
                </Button>
                <Button
                    onClick={handleExport}
                    disabled={!selectedId || exportMailboxMutation.isPending}
                    icon={exportMailboxMutation.isPending && <Spinner />}
                >
                    {t('Export mailbox')}
                </Button>
            </footer>
        </Modal>
    )
}

export default ModalMailboxExport;
