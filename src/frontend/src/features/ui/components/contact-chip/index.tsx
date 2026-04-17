import { useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Tooltip } from "@gouvfr-lasuite/cunningham-react";
import { Contact } from "@/features/api/gen/models";
import { Icon, IconSize, IconType } from "@gouvfr-lasuite/ui-kit";
import { ContactPopover } from "./contact-popover";
import { DateHelper } from "@/features/utils/date-helper";
import clsx from "clsx";


type DeliveryStatus = 'undelivered' | 'delivering' | 'delivered' | 'cancelled';
export type ContactChipDeliveryStatus = {
    status: DeliveryStatus;
    timestamp: string | null;
    message: string | null;
}
type ContactChipSenderStatus = 'unverified' | 'forged';

export type ContactChipDeliveryAction = {
    label: string;
    onClick: () => void;
};

type ContactChipProps = {
    contact: Contact;
    status?: ContactChipDeliveryStatus | ContactChipSenderStatus;
    displayEmail?: boolean;
    isUser?: boolean;
    senderUserName?: string | null;
    deliveryActions?: ContactChipDeliveryAction[];
} & React.HTMLAttributes<HTMLDivElement>;

export const ContactChip = ({ contact, status, displayEmail = false, isUser = false, senderUserName, deliveryActions, className, ...props }: ContactChipProps) => {
    const { t } = useTranslation();
    const popoverTriggerRef = useRef<HTMLButtonElement | null>(null);
    const [isPopoverOpen, setIsPopoverOpen] = useState(false);

    // Get delivery status info for popover (only if status is an object, not a sender status)
    const deliveryStatusInfo = status instanceof Object ? status : undefined;

    const chipContent = (
        <div className={clsx(["contact-chip", className])} {...props}>
            <button type="button" ref={popoverTriggerRef} className="contact-chip__content" onClick={() => setIsPopoverOpen(open => !open)}>
                {status === 'unverified' && (
                    <Icon name="warning" type={IconType.OUTLINED} size={IconSize.SMALL} className="contact-chip__icon contact-chip__icon--warning" />
                )}
                {status === 'forged' && (
                    <Icon name="gpp_bad" type={IconType.FILLED} size={IconSize.SMALL} className="contact-chip__icon contact-chip__icon--error" />
                )}
                {status instanceof Object && status.status === 'undelivered' && (
                    <Icon name="error" type={IconType.FILLED} size={IconSize.SMALL} className="contact-chip__icon contact-chip__icon--error" />
                )}
                {status instanceof Object && status.status === 'delivering' && (
                    <Icon name="update" type={IconType.OUTLINED} size={IconSize.SMALL} className="contact-chip__icon contact-chip__icon--warning" />
                )}
                {status instanceof Object && status.status === 'cancelled' && (
                    <Icon name="cancel" type={IconType.FILLED} size={IconSize.SMALL} className="contact-chip__icon contact-chip__icon--muted" />
                )}
                {displayEmail ? (
                    <>
                        <strong className="contact-chip__identity-name">{isUser ? t('You') : contact.name || contact.email.toLowerCase()}</strong>{' '}
                        {(contact.name || isUser) && (
                            <span className="contact-chip__identity-email">&lt;{contact.email.toLowerCase()}&gt;</span>
                        )}
                    </>
                ) : (
                    <span className="contact-chip__identity-email">{contact.email.toLowerCase()}</span>
                )}
            </button>
            <ContactPopover
                contact={contact}
                senderUserName={senderUserName}
                isOpen={isPopoverOpen}
                triggerRef={popoverTriggerRef}
                onOpenChange={setIsPopoverOpen}
                deliveryStatus={deliveryStatusInfo}
                deliveryActions={deliveryActions}
            />
        </div>
    );

    if (status === 'unverified') {
        return (
            <Tooltip content={t("This contact's identity could not be verified. Proceed with caution.")}>
                {chipContent}
            </Tooltip>
        );
    }
    if (status === 'forged') {
        return (
            <Tooltip content={t("This message failed sender authentication and is likely a forgery. Do not trust it.")}>
                {chipContent}
            </Tooltip>
        );
    }
    if (status instanceof Object) {
        if (['undelivered', 'delivering', 'cancelled'].includes(status.status)) {
            return (
                <Tooltip content={<DeliveryStatusTooltip status={status} />}>
                    {chipContent}
                </Tooltip>
            )
        }
    }

    return chipContent;
};

const DeliveryStatusTooltip = ({ status }: { status: ContactChipDeliveryStatus }) => {
    const { t } = useTranslation();

    return (
        <div>
            {['undelivered'].includes(status.status) && (
                <p>{t("This message has not been delivered.")}</p>
            )}
            {status.status === 'cancelled' && (
                <p>{t("This message has not been delivered. You cancelled the delivery.")}</p>
            )}
            {status.status === 'delivering' && (
                <p>{t("This message is being delivered.")}</p>
            )}
            {(status.timestamp || status.message) && (
                <div style={{ marginTop: '1rem' }}>
                    {status.timestamp && (
                        <p><em>{t("Last update: {{timestamp}}", { timestamp: DateHelper.formatRelativeTime(status.timestamp) })}</em></p>
                    )}
                    {status.message && (
                        <details className="contact-chip__delivery-logs">
                            <summary>{t('Show logs')}</summary>
                            <pre>{status.message}</pre>
                        </details>
                    )}
                </div>
            )}
        </div>
    )
}
