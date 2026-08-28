import { Contact } from "@/features/api/gen";
import { Icon, IconSize, IconType, UserAvatar } from "@gouvfr-lasuite/ui-kit";
import { Button } from "@gouvfr-lasuite/cunningham-react";
import { Popover, PopoverProps } from "react-aria-components";
import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import clsx from "clsx";
import { handle } from "@/features/utils/errors";
import MailHelper from "@/features/utils/mail-helper";
import type { ContactChipDeliveryStatus, ContactChipDeliveryAction } from "./index";

type ContactPopoverProps = PopoverProps & {
    contact: Contact;
    senderUserName?: string | null;
    deliveryStatus?: ContactChipDeliveryStatus;
    deliveryActions?: ContactChipDeliveryAction[];
};

type StatusVariant = 'error' | 'warning' | 'neutral';

const getStatusConfig = (
    status: ContactChipDeliveryStatus['status'],
    t: (key: string) => string
): { icon: string; variant: StatusVariant; label: string } | null => {
    switch (status) {
        case 'undelivered':
            return { icon: 'error', variant: 'error', label: t('Delivery failed') };
        case 'delivering':
            return { icon: 'schedule', variant: 'warning', label: t('Delivering') };
        case 'cancelled':
            return { icon: 'cancel', variant: 'neutral', label: t('Delivery cancelled') };
        // Don't show anything for delivered - keep it discreet
        default:
            return null;
    }
};

export const ContactPopover = ({ contact, senderUserName, deliveryStatus, deliveryActions, ...popoverProps }: ContactPopoverProps) => {
    const { t } = useTranslation();
    const [copied, setCopied] = useState(false);
    const timeoutRef = useRef<NodeJS.Timeout | null>(null);
    const popoverRef = useRef<HTMLDivElement>(null);

    const handleCopy = async (event: React.MouseEvent<HTMLButtonElement>) => {
        event.preventDefault();
        event.stopPropagation();
        try {
            await navigator.clipboard.writeText(contact.email);
            setCopied(true);
            timeoutRef.current = setTimeout(() => setCopied(false), 1000);
        } catch (error) {
            handle(new Error('Failed to copy email.'), { extra: { error } });
        }
    };

    // Cleanup timeout on unmount
    useEffect(() => () => {
        if (timeoutRef.current) {
            clearTimeout(timeoutRef.current);
        }
    }, []);

    const statusConfig = deliveryStatus ? getStatusConfig(deliveryStatus.status, t) : null;
    const showDeliverySection = statusConfig !== null;

    return (
        <Popover {...popoverProps}>
            <div ref={popoverRef} className="contact-popover">
                <div className="contact-popover__identity">
                    <UserAvatar fullName={contact.name || contact.email} size="large" />
                    <div className="contact-popover__identity-info">
                        <p title={contact.name || contact.email}>
                            <strong className="contact-popover__identity-name">{contact.name || MailHelper.splitEmail(contact.email)?.[0] || contact.email}</strong>
                        </p>
                        <button type="button" className="contact-popover__identity-email" onClick={handleCopy}>
                            <span>{contact.email}</span>
                            <Icon name={copied ? 'check' : 'copy'} className="contact-popover__copy-icon" type={IconType.OUTLINED} size={IconSize.SMALL} />
                        </button>
                    </div>
                </div>
                {senderUserName && (
                    <div className="contact-popover__sender-user">
                        <Icon name="person" type={IconType.FILLED} size={IconSize.SMALL} className="contact-popover__sender-user-icon" />
                        <span>{t('Sent by {{name}}', { name: senderUserName })}</span>
                    </div>
                )}
                {showDeliverySection && statusConfig && (
                    <div className="contact-popover__delivery">
                        <div className="contact-popover__delivery-row">
                            <div className="contact-popover__delivery-info">
                                <Icon
                                    name={statusConfig.icon}
                                    type={IconType.OUTLINED}
                                    size={IconSize.SMALL}
                                    className={clsx("contact-popover__delivery-icon", `contact-popover__delivery-icon--${statusConfig.variant}`)}
                                />
                                <span className="contact-popover__delivery-label">{statusConfig.label}</span>
                            </div>
                            {deliveryActions && deliveryActions.length > 0 && (
                            <div className="contact-popover__delivery-actions">
                                {deliveryActions.map((action, index) => (
                                    <Button
                                        key={index}
                                        size="nano"
                                        color="neutral"
                                        variant="secondary"
                                        onClick={action.onClick}
                                    >
                                        {action.label}
                                    </Button>
                                ))}
                            </div>
                            )}
                        </div>
                        {deliveryStatus?.message && (
                            <details className="contact-chip__delivery-logs">
                                <summary>{t('Show logs')}</summary>
                                <pre>{deliveryStatus.message}</pre>
                            </details>
                        )}
                    </div>
                )}
            </div>
        </Popover>
    );
};
