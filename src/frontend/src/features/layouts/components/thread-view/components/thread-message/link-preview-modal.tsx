import { useState, useCallback, useRef } from "react";
import i18n from "@/features/i18n/initI18n";
import { Button, Modal, ModalSize, Alert, VariantType, iconFromType } from "@gouvfr-lasuite/cunningham-react";
import classNames from "classnames";
import { Trans, useTranslation } from "react-i18next";
/**
 * Modal component to show a preview of a link.
 * <a href={url}>{linkText}</a>
 *
 * @param isOpen - Whether the modal is open
 * @param onClose - Function to call when the modal is closed
 * @param url - The URL to preview
 * @param linkText - The text of the link (optional)
 * @param hardWarning - Whether to show a more prominent warning
 * @param decision - Function to call with the user's confirmation choice
 */
type LinkPreviewModalProps = {
    isOpen: boolean;
    url: string;
    hardWarning?: boolean;
    decision: (choice: boolean) => void;
}

/**
 * Confirmation modal before redirecting to an external link.
 * It alerts the user about potential risks (phishing, etc.).
 */
export const LinkPreviewModal = ({ isOpen, url, hardWarning, decision }: LinkPreviewModalProps) => {
    const { t } = useTranslation();
    return (
        <Modal
            isOpen={isOpen}
            size={ModalSize.SMALL}
            title={(
                <span className="c__modal__text--centered">{
                    hardWarning
                        ? i18n.t('Be careful!')
                        : i18n.t('This links redirects to :')
                }</span>
            )}
            titleIcon={hardWarning && (
                <span
                    className="material-icons modal-message-error-icon"
                >
                    {iconFromType(VariantType.WARNING)}
                </span>
            )}
            hideCloseButton={true}
            actions={[
                <Button
                    key="cancel"
                    variant={hardWarning ? "primary" : "tertiary"}
                    onClick={() => decision(false)}
                >
                    {t("Cancel")}
                </Button>,
                <Button
                    key="confirm"
                    variant={hardWarning ? "tertiary" : "primary"}
                    color={hardWarning ? "error" : "neutral"}
                    onClick={() => decision(true)}
                >
                    {t("Open the link")}
                </Button>
            ]}
            onClose={() => decision(false)}
            closeOnClickOutside={true}
        >
            <div className="link-preview__children">
                {hardWarning && i18n.t('The link you clicked is probably unsafe :')}
                <Alert type={hardWarning ? VariantType.WARNING : VariantType.NEUTRAL}>{url}</Alert>
                <p className="link-preview__phishing-notice">
                    <Trans i18nKey="phishing_notice">
                        Be careful when clicking links in email, it could be a
                        <a href={`https://www.service-public.gouv.fr/particuliers/vosdroits/F34800`}>
                            phishing attempt
                        </a>.
                    </Trans>
                </p>
            </div>
        </Modal >
    )
}

/**
 * Hook to manage the state and logic of the link preview modal.
 * Exposes an asynchronous `askConfirmation` function that waits for user action.
 * 
 * @returns An object containing:
 * - `askConfirmation`: an async function that opens the modal and returns a boolean (`true` if confirmed)
 * - `modal`: the React node of the modal to be injected into the component tree
 */
export const useLinkPreviewModal = () => {
    const [isOpen, setIsOpen] = useState(false);
    const [url, setUrl] = useState('');
    const [hardWarning, setHardWarning] = useState(false);
    const resolverRef = useRef<((choice: boolean) => void) | null>(null);

    const askConfirmation = useCallback((urlToPreview: string, isHardWarning: boolean = false, textToPreview?: string) => {
        setUrl(urlToPreview);
        setHardWarning(isHardWarning);
        setIsOpen(true);

        return new Promise<boolean>((resolve) => {
            resolverRef.current = resolve;
        });
    }, []);

    const decision = useCallback((choice: boolean) => {
        setIsOpen(false);
        if (resolverRef.current) {
            resolverRef.current(choice);
            resolverRef.current = null;
        }
    }, []);

    const modal = isOpen ? (
        <LinkPreviewModal
            isOpen={isOpen}
            url={url}
            hardWarning={hardWarning}
            decision={decision}
        />
    ) : null;

    return { askConfirmation, modal };
}