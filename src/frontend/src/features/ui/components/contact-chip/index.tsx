import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Tooltip } from "@openfun/cunningham-react";
import { Contact } from "@/features/api/gen/models";
import { Icon, IconSize, IconType } from "@gouvfr-lasuite/ui-kit";

type ContactChipProps = {
    contact: Contact;
    showWarning?: boolean;
}

export const ContactChip = ({ contact, showWarning = false }: ContactChipProps) => {
    const { t } = useTranslation();
    const [copied, setCopied] = useState(false);
    const timeoutRef = useRef<NodeJS.Timeout | null>(null);

    const handleCopy = async () => {
        try {
            await navigator.clipboard.writeText(contact.email);
            setCopied(true);
            timeoutRef.current = setTimeout(() => setCopied(false), 1000);
        } catch (err) {
            console.error('Failed to copy email:', err);
        }
    };

    // Cleanup timeout on unmount
    useEffect(() => () => {
        if (timeoutRef.current) {
            clearTimeout(timeoutRef.current);
        }
    }, []);

    const chipContent = (
        <div className="contact-chip" data-copied={copied}>
            <button type="button" className="contact-chip__content" onClick={handleCopy}>
                {showWarning && (
                    <Icon name="warning" type={IconType.OUTLINED} size={IconSize.SMALL} className="contact-chip__warning" />
                )}
                <span className="contact-chip__email">{contact.email}</span>
                <span className="contact-chip__copied" aria-hidden={!copied}>
                    <Icon name="check" type={IconType.OUTLINED} size={IconSize.SMALL} aria-live="polite" />
                    {t('Copied!')}
                </span>
            </button>
        </div>
    );

    if (showWarning) {
        return (
            <Tooltip content={t('This contact cannot be trusted. Be careful when interacting with this contact.')}>
                {chipContent}
            </Tooltip>
        );
    }

    return chipContent;
};

