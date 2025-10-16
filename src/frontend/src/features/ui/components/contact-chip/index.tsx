import { useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Tooltip } from "@openfun/cunningham-react";
import { Contact } from "@/features/api/gen/models";
import { Icon, IconSize, IconType } from "@gouvfr-lasuite/ui-kit";
import { ContactPopover } from "./contact-popover";
import { DateHelper } from "@/features/utils/date-helper";


type DeliveryStatus = 'undelivered' | 'delivering' | 'delivered';
export type ContactChipDeliveryStatus = {
    status: DeliveryStatus;
    timestamp: string | null;
    message: string | null;
}
type ContactChipSenderStatus = 'unverified';

type ContactChipProps = {
    contact: Contact;
    status?: ContactChipDeliveryStatus | ContactChipSenderStatus;
}

export const ContactChip = ({ contact, status }: ContactChipProps) => {
    const { t } = useTranslation();
    const popoverTriggerRef = useRef<HTMLButtonElement | null>(null);
    const [isPopoverOpen, setIsPopoverOpen] = useState(false);

    const chipContent = (
        <div className="contact-chip">
            <button type="button" ref={popoverTriggerRef} className="contact-chip__content" onClick={() => setIsPopoverOpen(open => !open)}>
                {status === 'unverified' && (
                    <Icon name="warning" type={IconType.OUTLINED} size={IconSize.SMALL} className="contact-chip__warning" />
                )}
                {status instanceof Object && status.status === 'undelivered' && (
                    <Icon name="cancel" type={IconType.FILLED} size={IconSize.SMALL} className="contact-chip__error" />
                )}
                {status instanceof Object && status.status === 'delivering' && (
                    <Icon name="update" type={IconType.OUTLINED} size={IconSize.SMALL} className="contact-chip__warning" />
                )}
                <span className="contact-chip__email">{contact.email}</span>
            </button>
            <ContactPopover
                contact={contact}
                isOpen={isPopoverOpen}
                triggerRef={popoverTriggerRef}
                onOpenChange={setIsPopoverOpen}
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
    if (status instanceof Object) {
        if (['undelivered', 'delivering'].includes(status.status)) {
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
            {status.status === 'undelivered' && (
                <p>{t("This message has not been delivered.")}</p>
            )}
            {status.status === 'delivering' && (
                <p>{t("This message is being delivered.")}</p>
            )}
            {status.timestamp || status.message && (
                <div style={{ marginTop: '1rem' }}>
                    {status.timestamp && (
                        <p><em>{t("Last update: {{timestamp}}", { timestamp: DateHelper.formatRelativeTime(status.timestamp) })}</em></p>
                    )}
                    {status.message && (
                        <details><summary>{t('Show logs')}</summary>{status.message}</details>
                    )}
                </div>
            )}
        </div>
    )
}
