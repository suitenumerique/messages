import { Contact } from "@/features/api/gen";
import { Icon, IconSize, IconType, UserAvatar } from "@gouvfr-lasuite/ui-kit";
import { Popover, PopoverProps } from "react-aria-components";
import { useEffect, useRef, useState } from "react";
import { handle } from "@/features/utils/errors";

type ContactPopoverProps = PopoverProps & {
    contact: Contact;
};

export const ContactPopover = ({ contact, ...popoverProps }: ContactPopoverProps) => {
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

    return (
        <Popover {...popoverProps}>
            <div ref={popoverRef} className="contact-popover">
                <div className="contact-popover__identity">
                    <UserAvatar fullName={contact.name || contact.email} size="large" />
                    <div className="contact-popover__identity-info">
                        <p title={contact.name || contact.email}>
                            <strong className="contact-popover__identity-name">{contact.name || contact.email.split('@')[0]}</strong>
                        </p>
                        <button type="button" className="contact-popover__identity-email" onClick={handleCopy}>
                            <span>{contact.email}</span>
                            <Icon name={copied ? 'check' : 'copy'} className="contact-popover__copy-icon" type={IconType.OUTLINED} size={IconSize.SMALL} />
                        </button>
                    </div>
                </div>
            </div>
        </Popover>
    );
};
