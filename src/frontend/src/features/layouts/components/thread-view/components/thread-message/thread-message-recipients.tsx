import { Fragment, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { MessageRecipient } from "@/features/api/gen/models";
import { ContactChip, ContactChipDeliveryStatus, ContactChipDeliveryAction } from "@/features/ui/components/contact-chip";
import { Button } from "@gouvfr-lasuite/cunningham-react";

type RecipientGroup = {
    key: 'to' | 'cc' | 'bcc';
    label: string;
    recipients: readonly MessageRecipient[];
};

type RecipientLineItem = {
    group: RecipientGroup['key'];
    label: string;
    recipient: MessageRecipient;
    isGroupStart: boolean;
    hasComma: boolean;
};

type ThreadMessageRecipientsProps = {
    to: readonly MessageRecipient[];
    cc: readonly MessageRecipient[];
    bcc: readonly MessageRecipient[];
    getDeliveryStatus: (recipient: MessageRecipient) => ContactChipDeliveryStatus | undefined;
    getDeliveryActions: (recipient: MessageRecipient) => ContactChipDeliveryAction[] | undefined;
};

/**
 * Recipients of a message, Apple Mail style: a single collapsed line
 * "To: a, b Copy: c and X more" by default. Clicking "and X more" expands to
 * the full grouped list (To / Copy / BCC on separate rows).
 *
 * The collapsed line cannot be laid out with CSS alone because the exact
 * overflow count must be displayed: a first render pass draws every recipient
 * in a single hidden-overflow row to measure item widths, then only the items
 * fitting next to the "and X more" button are kept. Widths are cached and the
 * visible count is recomputed on container resize.
 */
const ThreadMessageRecipients = ({
    to,
    cc,
    bcc,
    getDeliveryStatus,
    getDeliveryActions,
}: ThreadMessageRecipientsProps) => {
    const { t } = useTranslation();
    const [isExpanded, setIsExpanded] = useState(false);
    // null = measuring pass: render every item to capture their widths.
    const [visibleCount, setVisibleCount] = useState<number | null>(null);
    const containerRef = useRef<HTMLDivElement | null>(null);
    const itemRefs = useRef<(HTMLSpanElement | null)[]>([]);
    const ghostMoreRef = useRef<HTMLSpanElement | null>(null);
    const itemWidthsRef = useRef<number[] | null>(null);
    const moreWidthRef = useRef(0);

    const groups = useMemo<RecipientGroup[]>(() => [
        { key: 'to' as const, label: t('To: '), recipients: to },
        { key: 'cc' as const, label: t('Copy: '), recipients: cc },
        { key: 'bcc' as const, label: t('BCC: '), recipients: bcc },
    ].filter((group) => group.recipients.length > 0), [to, cc, bcc, t]);

    const items = useMemo<RecipientLineItem[]>(() => groups.flatMap((group) =>
        group.recipients.map((recipient, index) => ({
            group: group.key,
            label: group.label,
            recipient,
            isGroupStart: index === 0,
            hasComma: index < group.recipients.length - 1,
        }))
    ), [groups]);

    const computeVisibleCount = (containerWidth: number): number => {
        const widths = itemWidthsRef.current;
        if (!widths) return items.length;
        let usedWidth = 0;
        let count = 0;
        while (count < widths.length && usedWidth + widths[count] <= containerWidth) {
            usedWidth += widths[count];
            count += 1;
        }
        if (count >= widths.length) return widths.length;
        // Free up room for the "and X more" button, but always keep one recipient.
        while (count > 1 && usedWidth + moreWidthRef.current > containerWidth) {
            count -= 1;
            usedWidth -= widths[count];
        }
        return Math.max(count, 1);
    };

    // Recipients changed: cached widths are stale, trigger a new measuring pass.
    useLayoutEffect(() => {
        itemWidthsRef.current = null;
        setVisibleCount(null);
    }, [items]);

    useLayoutEffect(() => {
        if (isExpanded || visibleCount !== null) return;
        const container = containerRef.current;
        if (!container) return;
        itemWidthsRef.current = items.map((_, index) => itemRefs.current[index]?.offsetWidth ?? 0);
        moreWidthRef.current = ghostMoreRef.current?.offsetWidth ?? 0;
        setVisibleCount(computeVisibleCount(container.clientWidth));
    });

    useEffect(() => {
        if (isExpanded) return undefined;
        const container = containerRef.current;
        if (!container) return undefined;
        const observer = new ResizeObserver(() => {
            if (itemWidthsRef.current) {
                setVisibleCount(computeVisibleCount(container.clientWidth));
            }
        });
        observer.observe(container);
        return () => observer.disconnect();
    }, [isExpanded, items]);

    // Web fonts loading after the first paint change text metrics: re-measure once ready.
    useEffect(() => {
        let cancelled = false;
        document.fonts?.ready.then(() => {
            if (cancelled) return;
            itemWidthsRef.current = null;
            setVisibleCount(null);
        });
        return () => { cancelled = true; };
    }, []);

    if (items.length === 0) return null;

    if (isExpanded) {
        return (
            <div className="thread-message__recipients">
                <dl className="thread-message__correspondents">
                    {groups.map((group) => (
                        <Fragment key={group.key}>
                            <dt>{group.label}</dt>
                            <dd className="recipient-chip-list">
                                {group.recipients.map((recipient) => (
                                    <ContactChip
                                        key={`${group.key}-${recipient.id}`}
                                        contact={recipient.contact}
                                        status={getDeliveryStatus(recipient)}
                                        deliveryActions={getDeliveryActions(recipient)}
                                        displayOnlyEmail
                                    />
                                ))}
                            </dd>
                        </Fragment>
                    ))}
                </dl>
                <Button
                    color="neutral"
                    variant="tertiary"
                    size="small"
                    type="button"
                    className="thread-message__recipients-toggle"
                    aria-expanded
                    onClick={() => setIsExpanded(false)}
                >
                    {t('Hide')}
                </Button>
            </div>
        );
    }

    const isMeasuring = visibleCount === null;
    const renderedItems = isMeasuring ? items : items.slice(0, visibleCount);
    const hiddenCount = items.length - renderedItems.length;

    return (
        <div className="thread-message__recipients">
            <div className="thread-message__recipients-line" ref={containerRef}>
                {renderedItems.map((item, index) => (
                    <span
                        key={`${item.group}-${item.recipient.id}`}
                        ref={(element) => { itemRefs.current[index] = element; }}
                        className="thread-message__recipients-item"
                    >
                        {item.isGroupStart && (
                            <span className="thread-message__recipients-label">{item.label}</span>
                        )}
                        <ContactChip
                            contact={item.recipient.contact}
                            status={getDeliveryStatus(item.recipient)}
                            deliveryActions={getDeliveryActions(item.recipient)}
                            displayOnlyEmail
                        />
                        {item.hasComma && !(hiddenCount > 0 && index === renderedItems.length - 1) && (
                            <span className="thread-message__recipients-separator">,&nbsp;</span>
                        )}
                    </span>
                ))}
                {!isMeasuring && hiddenCount > 0 && (
                    <Button
                        color="neutral"
                        variant="tertiary"
                        type="button"
                        size="nano"
                        className="thread-message__recipients-more"
                        aria-expanded={false}
                        onClick={() => setIsExpanded(true)}
                    >
                        {t('and {{count}} more', { count: hiddenCount })}
                    </Button>
                )}
                {isMeasuring && (
                    <span
                        ref={ghostMoreRef}
                        className="thread-message__recipients-more thread-message__recipients-more--ghost"
                        aria-hidden
                    >
                        {t('and {{count}} more', { count: items.length })}
                    </span>
                )}
            </div>
        </div>
    );
};

export default ThreadMessageRecipients;
