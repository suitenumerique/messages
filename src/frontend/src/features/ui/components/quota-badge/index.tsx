import { Icon, IconSize, IconType, Spinner } from "@gouvfr-lasuite/ui-kit";
import { Tooltip } from "@gouvfr-lasuite/cunningham-react";
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { fetchAPI } from "@/features/api/fetch-api";
import { clsx } from "clsx";
import QuotaHelper from "@/features/utils/quota-helper";

type RecipientQuota = {
    period: string;
    period_display: string;
    period_start: string;
    recipient_count: number;
    quota_limit: number;
    remaining: number;
    usage_percentage: number;
};

type QuotaBadgeProps = {
    mailboxId?: string;
    domainId?: string;
    /** Parent domain ID - when provided, uses the admin endpoint for mailbox quota */
    parentDomainId?: string;
    compact?: boolean;
};

/**
 * Badge component to display recipient quota status with colored indicators
 * Similar to Django admin display
 */
export const QuotaBadge = ({ mailboxId, domainId, parentDomainId, compact = false }: QuotaBadgeProps) => {
    const { t } = useTranslation();

    const entityId = mailboxId || domainId;
    const entityType = mailboxId ? 'mailbox' : 'domain';

    // Determine the correct API URL
    // For mailboxes in admin context (with parentDomainId), use the nested admin endpoint
    // Otherwise, use the regular endpoint
    let apiUrl: string;
    if (mailboxId && parentDomainId) {
        // Admin endpoint: /api/v1.0/maildomains/{domainId}/mailboxes/{mailboxId}/quota/
        apiUrl = `/api/v1.0/maildomains/${parentDomainId}/mailboxes/${mailboxId}/quota/`;
    } else if (mailboxId) {
        // Regular endpoint: /api/v1.0/mailboxes/{mailboxId}/quota/
        apiUrl = `/api/v1.0/mailboxes/${mailboxId}/quota/`;
    } else {
        // Domain endpoint: /api/v1.0/maildomains/{domainId}/quota/
        apiUrl = `/api/v1.0/maildomains/${domainId}/quota/`;
    }

    const { data: quota, isLoading, isError } = useQuery<RecipientQuota>({
        queryKey: [`${entityType}-quota`, entityId, parentDomainId],
        queryFn: async () => {
            const response = await fetchAPI<{ data: RecipientQuota }>(apiUrl);
            return response.data;
        },
        enabled: !!entityId,
        staleTime: 5000, // Cache for 5 seconds
        retry: 1,
    });

    if (isLoading) {
        return <Spinner size="sm" />;
    }

    if (isError || !quota) {
        console.warn('QuotaBadge: No quota data', { isError, quota, entityId, apiUrl });
        return <span style={{ color: 'var(--c--theme--colors--greyscale-400)' }}>-</span>;
    }


    const isLow = quota.usage_percentage >= 70;
    const isCritical = quota.usage_percentage >= 90;

    // Status indicator icon with color modifier
    const statusClass = isCritical ? 'quota-badge--critical' : isLow ? 'quota-badge--warning' : 'quota-badge--ok';

    const periodShortLabel = t(QuotaHelper.getPeriodShortLabel(quota.period, quota.period_display));
    const periodFullLabel = t(QuotaHelper.getPeriodFullLabel(quota.period, quota.period_display));

    const tooltipText = t("{{count}}/{{limit}} recipients {{period}} ({{remaining}} remaining)", {
        count: quota.recipient_count,
        limit: quota.quota_limit,
        period: periodFullLabel,
        remaining: quota.remaining,
        defaultValue_one: "{{count}}/{{limit}} recipient {{period}} ({{remaining}} remaining)"
    });

    return (
        <Tooltip content={tooltipText} placement="top">
            <div className={clsx("quota-badge", statusClass)} aria-label={tooltipText}>
                <span className="quota-badge__indicator" aria-hidden="true" />
                <span className="quota-badge__count">{quota.recipient_count}/{quota.quota_limit}</span>
                <Icon name="person" type={IconType.OUTLINED} size={IconSize.SMALL} />
                <span className="quota-badge__period">{periodShortLabel}</span>
                {!compact && <span className="quota-badge__percentage">({quota.usage_percentage}%)</span>}
            </div>
        </Tooltip>
    );
};

export default QuotaBadge;


